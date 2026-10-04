'use strict';

const { createHash } = require('node:crypto');
const path = require('node:path');
const { Parser, Language } = require('web-tree-sitter');
const { extractFrameworkFacts } = require('./frameworkAdapters');

const GRAMMARS = {
  javascript: { '.jsx': 'javascript', '.js': 'javascript' },
  typescript: { '.tsx': 'tsx', '.ts': 'typescript' },
  python: { '.py': 'python' },
};
const TYPES = new Set(['function_declaration', 'function_definition', 'method_definition',
  'class_declaration', 'class_definition', 'interface_declaration', 'type_alias_declaration',
  'enum_declaration']);
const PARSER_VERSION = 2;
let initialized;
const languages = new Map();

function stableId(...parts) {
  return createHash('sha256').update(JSON.stringify(parts)).digest('hex').slice(0, 40);
}

async function languageFor(language, filePath) {
  const grammar = GRAMMARS[language]?.[path.extname(filePath).toLowerCase()];
  if (!grammar) throw new Error(`Unsupported parser language for ${filePath}.`);
  initialized ||= Parser.init();
  await initialized;
  if (!languages.has(grammar)) {
    const wasm = require.resolve(`@vscode/tree-sitter-wasm/wasm/tree-sitter-${grammar}.wasm`);
    languages.set(grammar, await Language.load(wasm));
  }
  return languages.get(grammar);
}

function field(node, name) { return node?.childForFieldName(name); }
function text(node, source) { return node ? source.slice(node.startIndex, node.endIndex) : ''; }
function cleanString(node, source) {
  const value = text(node, source);
  const quote = value[0];
  return (quote === '"' || quote === "'" || quote === '`') && value.at(-1) === quote && !value.includes('${')
    ? value.slice(1, -1) : null;
}
function hasJsx(node) {
  if (!node) return false;
  if (node.type.startsWith('jsx_')) return true;
  return node.namedChildren.some(hasJsx);
}
function nameOf(node, source) { return text(field(node, 'name'), source); }
function isTestFile(filePath) {
  return /(^|\/)(__tests__|tests?)(\/|$)|(?:\.test\.[jt]sx?$)|(?:^|\/)test_.*\.py$|_test\.py$/.test(filePath);
}

function parseImports(node, source, imports) {
  if (node.type === 'import_statement' && node.parent?.type !== 'module') {
    // Python import statements are still recorded for visibility, but only local imports resolve to file edges.
  }
  if (node.type === 'import_statement' && field(node, 'source')) {
    const module = cleanString(field(node, 'source'), source);
    if (!module) return;
    const bindings = [];
    const clause = node.namedChildren.find(child => child.type === 'import_clause');
    if (clause) {
      for (const child of clause.namedChildren) {
        if (child.type === 'identifier') bindings.push({ imported: 'default', local: text(child, source) });
        if (child.type === 'named_imports') {
          for (const spec of child.namedChildren) {
            if (spec.type !== 'import_specifier') continue;
            bindings.push({ imported: text(field(spec, 'name'), source),
              local: text(field(spec, 'alias') || field(spec, 'name'), source) });
          }
        }
        if (child.type === 'namespace_import') {
          const local = child.namedChildren.find(item => item.type === 'identifier');
          if (local) bindings.push({ imported: '*', local: text(local, source) });
        }
      }
    }
    imports.push({ module, bindings, line: node.startPosition.row + 1 });
  } else if (node.type === 'import_from_statement') {
    const module = text(field(node, 'module_name'), source);
    const bindings = [];
    const name = field(node, 'name');
    if (name) {
      if (name.type === 'dotted_name') bindings.push({ imported: text(name, source), local: text(name, source) });
      else for (const child of name.namedChildren) {
        if (child.type === 'dotted_name') bindings.push({ imported: text(child, source), local: text(child, source) });
        if (child.type === 'aliased_import') bindings.push({ imported: text(field(child, 'name'), source),
          local: text(field(child, 'alias'), source) });
      }
    }
    imports.push({ module, bindings, line: node.startPosition.row + 1 });
  } else if (node.type === 'import_statement') {
    const modules = node.namedChildren.filter(child => child.type === 'dotted_name');
    for (const item of modules) imports.push({ module: text(item, source), bindings: [], line: node.startPosition.row + 1 });
  } else if (node.type === 'variable_declarator') {
    const value = field(node, 'value');
    if (value?.type !== 'call_expression' || text(field(value, 'function'), source) !== 'require') return;
    const module = cleanString(field(value, 'arguments')?.namedChildren[0], source);
    if (!module) return;
    const bindingNode = field(node, 'name');
    const bindings = [];
    if (bindingNode?.type === 'identifier') {
      bindings.push({ imported: '*', local: text(bindingNode, source) });
    } else if (bindingNode?.type === 'object_pattern') {
      for (const item of bindingNode.namedChildren) {
        if (item.type === 'shorthand_property_identifier_pattern') {
          bindings.push({ imported: text(item, source), local: text(item, source) });
        } else if (item.type === 'pair_pattern') {
          bindings.push({ imported: text(field(item, 'key'), source),
            local: text(field(item, 'value'), source) });
        }
      }
    }
    imports.push({ module, bindings, line: node.startPosition.row + 1 });
  }
}

function symbolKind(node, name, owner, filePath) {
  if (node.type.includes('class')) return 'class';
  if (node.type === 'interface_declaration') return 'interface';
  if (node.type === 'type_alias_declaration') return 'type';
  if (node.type === 'enum_declaration') return 'enum';
  if (node.type === 'method_definition' || (owner?.kind === 'class' && node.type === 'function_definition')) return 'method';
  if ((name.startsWith('test_') && isTestFile(filePath))) return 'test';
  if (/^(predict|infer|inference|preprocess|transform)_?/i.test(name)) return 'ml_entry';
  if (/^[A-Z]/.test(name) && (filePath.endsWith('.jsx') || filePath.endsWith('.tsx')) && hasJsx(field(node, 'body'))) return 'component';
  return 'function';
}

function signature(node, source) {
  const body = field(node, 'body') || field(field(node, 'value'), 'body');
  const end = body ? body.startIndex : Math.min(node.endIndex, node.startIndex + 500);
  return source.slice(node.startIndex, end).replace(/\s+/g, ' ').trim().slice(0, 500);
}

function docstring(node, source, language) {
  if (language === 'python') {
    const first = field(node, 'body')?.namedChildren[0];
    if (first?.type === 'expression_statement' && first.namedChildren[0]?.type === 'string') {
      return text(first.namedChildren[0], source).slice(0, 1000);
    }
  }
  const previous = node.previousNamedSibling || (node.parent?.type === 'export_statement' ? node.parent.previousNamedSibling : null);
  return previous?.type === 'comment' ? text(previous, source).slice(0, 1000) : null;
}

async function parseCode({ repoId, filePath, language, source, contentHash, revision }) {
  if (language === 'markdown') {
    const lines = source.split('\n');
    const sections = [];
    let fenced = false;
    for (let i = 0; i < lines.length; i++) {
      if (/^\s*(```|~~~)/.test(lines[i])) fenced = !fenced;
      const match = !fenced && /^(#{1,6})\s+(.+)/.exec(lines[i]);
      if (match) sections.push({ name: match[2].trim(), startLine: i + 1 });
    }
    if (!sections.length || sections[0].startLine > 1) sections.unshift({ name: filePath, startLine: 1 });
    return { path: filePath, language, contentHash, revision,
      symbols: sections.map((section, i) => ({ ...section, id: stableId(repoId, filePath, 'document', section.name, i),
        type: 'symbol', kind: 'document', path: filePath, language, revision,
        qualifiedName: section.name, endLine: (sections[i + 1]?.startLine || lines.length + 1) - 1 })),
      imports: [], calls: [], routes: [], clients: [], entities: [], accesses: [], tests: [], mlEntries: [], parseErrors: false };
  }
  const grammar = await languageFor(language, filePath);
  const parser = new Parser();
  parser.setLanguage(grammar);
  const tree = parser.parse(source);
  const symbols = [];
  const imports = [];
  const calls = [];
  const occurrences = new Map();
  const context = { repoId, filePath, language, source, symbols, imports, calls,
    routes: [], clients: [], entities: [], accesses: [], tests: [], mlEntries: [] };

  function addSymbol(node, name, owner, forcedKind) {
    if (!name) return null;
    const kind = forcedKind || symbolKind(node, name, owner, filePath);
    const qualifiedName = owner ? `${owner.qualifiedName}.${name}` : name;
    const sig = forcedKind === 'test' ? `test(${JSON.stringify(name)})` : signature(node, source);
    const key = `${kind}:${qualifiedName}:${sig}`;
    const ordinal = occurrences.get(key) || 0;
    occurrences.set(key, ordinal + 1);
    const visibility = node.namedChildren.some(child => child.type === 'accessibility_modifier')
      ? text(node.namedChildren.find(child => child.type === 'accessibility_modifier'), source)
      : name.startsWith('_') ? 'private' : node.parent?.type === 'export_statement' ? 'public' : 'module';
    const externalNames = new Set();
    const localNames = new Set([name]);
    function identifiers(item, target) {
      if (!item) return;
      if (['identifier', 'this', 'super'].includes(item.type)) target.add(text(item, source));
      for (const child of item.namedChildren) identifiers(child, target);
    }
    identifiers(field(node, 'parameters') || field(field(node, 'value'), 'parameters'), localNames);
    identifiers(field(node, 'body') || field(field(node, 'value'), 'body'), externalNames);
    const symbol = { id: stableId(repoId, filePath, kind, qualifiedName, sig, ordinal),
      type: 'symbol', kind, name, qualifiedName, signature: sig,
      startLine: node.startPosition.row + 1, endLine: node.endPosition.row + 1,
      startIndex: node.startIndex, endIndex: node.endIndex,
      externalNames: [...externalNames].filter(item => !localNames.has(item)).sort(),
      bodyHash: (() => {
        const body = field(node, 'body') || field(field(node, 'value'), 'body');
        if (!body) return null;
        const leaves = [];
        function tokens(item) {
          if (item.type === 'comment') return;
          if (!item.childCount) leaves.push([item.type, text(item, source)]);
          else for (const child of item.children) tokens(child);
        }
        tokens(body);
        return stableId(leaves);
      })(),
      visibility, parentId: owner?.id || null, docstring: docstring(node, source, language),
      path: filePath, language, revision };
    symbols.push(symbol);
    return symbol;
  }

  function visit(node, owner) {
    parseImports(node, source, imports);
    let current = owner;
    if (TYPES.has(node.type)) {
      current = addSymbol(node, nameOf(node, source), owner) || owner;
    } else if (node.type === 'variable_declarator') {
      const value = field(node, 'value');
      if (value && ['arrow_function', 'function_expression'].includes(value.type)) {
        const name = nameOf(node, source);
        current = addSymbol(node, name, owner, /^[A-Z]/.test(name) && hasJsx(value) ? 'component' : undefined) || owner;
      }
    }
    if (node.type === 'call_expression' || node.type === 'call') {
      const functionNode = field(node, 'function');
      const callName = text(functionNode, source);
      if (current && callName) calls.push({ callerId: current.id, name: callName, line: node.startPosition.row + 1 });
      if (isTestFile(filePath) && ['test', 'it'].includes(callName)) {
        const args = field(node, 'arguments');
        const title = cleanString(args?.namedChildren[0], source);
        if (title) {
          current = addSymbol(node, title, owner, 'test') || owner;
        }
      }
    }
    extractFrameworkFacts(node, current, context, { field, text, cleanString });
    for (const child of node.namedChildren) visit(child, current);
  }
  visit(tree.rootNode, null);
  const result = { path: filePath, language, contentHash, revision, symbols, imports, calls,
    routes: context.routes, clients: context.clients, entities: context.entities,
    accesses: context.accesses, tests: context.tests, mlEntries: context.mlEntries,
    parseErrors: tree.rootNode.hasError };
  tree.delete();
  parser.delete();
  return result;
}

module.exports = { parseCode, stableId, isTestFile, PARSER_VERSION };
