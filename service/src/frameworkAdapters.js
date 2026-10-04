'use strict';

const HTTP_METHODS = new Set(['get', 'post', 'put', 'patch', 'delete', 'options', 'head']);
const READ_METHODS = new Set(['findUnique', 'findFirst', 'findMany', 'count', 'aggregate', 'query', 'get', 'select']);
const WRITE_METHODS = new Set(['create', 'update', 'upsert', 'delete', 'createMany', 'updateMany', 'deleteMany', 'add', 'commit']);

function callParts(functionNode, source, text, field) {
  if (!functionNode) return [];
  if (functionNode.type === 'identifier') return [text(functionNode, source)];
  if (functionNode.type === 'member_expression' || functionNode.type === 'attribute') {
    return [...callParts(field(functionNode, 'object'), source, text, field),
      text(field(functionNode, 'property') || field(functionNode, 'attribute'), source)];
  }
  return text(functionNode, source).split('.');
}

const frameworkAdapters = [
  {
    name: 'http_routes',
    extract(node, owner, context, helpers, call) {
      const { field, text, cleanString } = helpers;
      if (node.type === 'decorated_definition') {
        const definition = field(node, 'definition');
        const handlerName = text(field(definition, 'name'), context.source);
        for (const decorator of node.namedChildren.filter(child => child.type === 'decorator')) {
          const routeCall = decorator.namedChildren.find(child => child.type === 'call');
          const parts = callParts(field(routeCall, 'function'), context.source, text, field);
          const method = parts.at(-1)?.toLowerCase();
          const route = cleanString(field(routeCall, 'arguments')?.namedChildren[0], context.source);
          if (HTTP_METHODS.has(method) && route?.startsWith('/')) {
            context.routes.push({ method: method.toUpperCase(), route, handlerName,
              line: decorator.startPosition.row + 1 });
          }
        }
      }
      if (!call) return;
      const method = call.parts.at(-1)?.toLowerCase();
      if (node.type === 'call_expression' && call.parts.length >= 2 && HTTP_METHODS.has(method) &&
          ['app', 'router', 'server', 'blueprint'].includes(call.parts[0]) && call.first?.startsWith('/')) {
        const handler = call.args.at(-1);
        context.routes.push({ method: method.toUpperCase(), route: call.first,
          handlerName: handler && handler !== call.args[0] ? text(handler, context.source) : null,
          line: call.line });
      }
    },
  },
  {
    name: 'frontend_clients',
    extract(_node, owner, context, _helpers, call) {
      if (!owner || !call) return;
      const method = call.parts.at(-1)?.toLowerCase();
      if (call.parts[0] === 'fetch' && call.first?.startsWith('/')) {
        const option = call.args[1] ? context.source.slice(call.args[1].startIndex, call.args[1].endIndex) : '';
        const match = /\bmethod\s*:\s*['"]([A-Za-z]+)['"]/.exec(option);
        context.clients.push({ sourceId: owner.id, method: match ? match[1].toUpperCase() : 'GET',
          route: call.first, line: call.line });
      } else if (call.parts[0] === 'axios' && HTTP_METHODS.has(method) && call.first?.startsWith('/')) {
        context.clients.push({ sourceId: owner.id, method: method.toUpperCase(), route: call.first, line: call.line });
      }
    },
  },
  {
    name: 'orm_schema',
    extract(node, owner, context, helpers, call) {
      const { field, text } = helpers;
      if (node.type === 'class_definition' || node.type === 'class_declaration') {
        const name = text(field(node, 'name'), context.source);
        const bases = text(field(node, 'superclasses') || field(node, 'heritage'), context.source);
        if (name && /\b(Base|Model|Document)\b/.test(bases)) {
          context.entities.push({ name, sourceId: owner?.id || null, line: node.startPosition.row + 1 });
        }
      }
      if (!owner || !call) return;
      if (call.parts.length >= 3 && ['prisma', 'db', 'client'].includes(call.parts[0])) {
        const operation = call.parts.at(-1);
        const type = READ_METHODS.has(operation) ? 'READS_FROM' : WRITE_METHODS.has(operation) ? 'WRITES_TO' : null;
        if (type) context.accesses.push({ sourceId: owner.id,
          entityName: call.parts.at(-2)[0].toUpperCase() + call.parts.at(-2).slice(1), type, line: call.line });
      }
      if (call.parts.at(-1) === 'query' && call.args[0]?.type === 'identifier') {
        context.accesses.push({ sourceId: owner.id,
          entityName: text(call.args[0], context.source), type: 'READS_FROM', line: call.line });
      }
    },
  },
  {
    name: 'ml_entrypoints',
    extract(node, owner, context, _helpers, call) {
      if (owner?.kind === 'ml_entry' && node.startPosition.row + 1 === owner.startLine) {
        if (!context.mlEntries.some(item => item.symbolId === owner.id)) {
          context.mlEntries.push({ symbolId: owner.id, line: owner.startLine });
        }
      }
      if (owner && call && ['predict', 'transform'].includes(call.parts.at(-1))) {
        context.accesses.push({ sourceId: owner.id, entityName: call.parts.at(-2) || 'Model',
          type: 'USES_MODEL', line: call.line });
      }
    },
  },
  {
    name: 'tests',
    extract(node, owner, context, _helpers, call) {
      if (owner?.kind !== 'test') return;
      if (node.type === 'function_definition' || (call && ['test', 'it'].includes(call.parts[0]))) {
        if (!context.tests.some(item => item.testId === owner.id)) context.tests.push({ testId: owner.id });
      }
    },
  },
];

function extractFrameworkFacts(node, owner, context, helpers) {
  let call = null;
  if (node.type === 'call_expression' || node.type === 'call') {
    const args = helpers.field(node, 'arguments')?.namedChildren || [];
    call = { parts: callParts(helpers.field(node, 'function'), context.source, helpers.text, helpers.field),
      args, first: helpers.cleanString(args[0], context.source), line: node.startPosition.row + 1 };
  }
  for (const adapter of frameworkAdapters) adapter.extract(node, owner, context, helpers, call);
}

module.exports = { frameworkAdapters, extractFrameworkFacts };
