"use strict";

// One-shot, read-only TypeScript semantic pass over the allowlisted inventory.
// stdin: {root, files, configs}; stdout: {events, diagnostics, typescriptVersion}.
const fs = require("node:fs");
const path = require("node:path");
const ts = require("../extension/node_modules/typescript");

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const root = path.resolve(input.root);
const allowed = new Set(input.files.map((file) => path.resolve(root, file)));
const configPaths = new Set(input.configs.map((file) => path.resolve(root, file)));
const allowedDirectories = new Set([root]);
for (const file of new Set([...allowed, ...configPaths])) {
  for (let dir = path.dirname(file); dir.startsWith(root + path.sep); dir = path.dirname(dir)) {
    allowedDirectories.add(dir);
  }
}
const inRoot = (file) => file === root || file.startsWith(root + path.sep);
const relative = (file) => inRoot(path.resolve(file)) ? path.relative(root, file).split(path.sep).join("/") : null;
function safeFile(file, inventory) {
  const absolute = path.resolve(file);
  if (!inventory.has(absolute) || !inRoot(absolute)) return false;
  try {
    return fs.lstatSync(absolute).isFile() && fs.realpathSync(absolute) === absolute;
  } catch {
    return false;
  }
}
const included = (file) => safeFile(file, allowed);
const events = [];
const diagnostics = [];

const tsconfig = path.join(root, "tsconfig.json");
let configOptions = {};
if (configPaths.has(tsconfig)) {
  const configHost = {
    useCaseSensitiveFileNames: ts.sys.useCaseSensitiveFileNames,
    getCurrentDirectory: () => root,
    readFile: (file) => safeFile(file, configPaths) ? fs.readFileSync(file, "utf8") : undefined,
    fileExists: (file) => safeFile(file, configPaths) || included(file),
    directoryExists: (dir) => allowedDirectories.has(path.resolve(dir)),
    readDirectory: (dir) => [...allowed].filter((file) => file.startsWith(path.resolve(dir) + path.sep)),
    onUnRecoverableConfigFileDiagnostic: (error) => diagnostics.push({kind: "config_error",
      detail: ts.flattenDiagnosticMessageText(error.messageText, " ")}),
  };
  const parsed = ts.getParsedCommandLineOfConfigFile(tsconfig, {}, configHost);
  if (parsed) {
    configOptions = parsed.options;
    for (const error of parsed.errors) diagnostics.push({kind: "config_error",
      detail: ts.flattenDiagnosticMessageText(error.messageText, " ")});
  }
}

const options = {
  target: ts.ScriptTarget.ESNext,
  module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Node10,
  jsx: ts.JsxEmit.Preserve,
  ...configOptions,
  allowJs: true,
  checkJs: false,
  noLib: true,
  noEmit: true,
  types: [],
};
// The compiler may not read imports, declarations, or package files that the
// Phase 1 scanner excluded. This also keeps sensitive files out of the pass.
const host = ts.createCompilerHost(options);
const originalGetSourceFile = host.getSourceFile.bind(host);
host.getSourceFile = (file, ...rest) => included(file) ? originalGetSourceFile(file, ...rest) : undefined;
host.readFile = (file) => included(file) ? fs.readFileSync(file, "utf8") : undefined;
host.fileExists = (file) => included(file);
host.directoryExists = (dir) => allowedDirectories.has(path.resolve(dir));
host.getDirectories = (dir) => [...allowedDirectories].filter((item) => path.dirname(item) === path.resolve(dir));
host.realpath = (file) => file;

const program = ts.createProgram([...allowed], options, host);
const checker = program.getTypeChecker();

function byte(source, offset) {
  return Buffer.byteLength(source.text.slice(0, offset), "utf8");
}

function place(node, source) {
  return {file: relative(source.fileName), startByte: byte(source, node.getStart(source)),
          endByte: byte(source, node.end)};
}

function declaration(symbol) {
  if (!symbol) return null;
  if (symbol.flags & ts.SymbolFlags.Alias) {
    symbol = checker.getAliasedSymbol(symbol);
  }
  const declarations = symbol.declarations || [];
  // Union receivers can expose declarations from several classes. They do
  // not provide a single verified callee. Overloads share one owner.
  const owners = new Set(declarations.map((decl) => {
    // Namespace aliases resolve to the module's SourceFile, an AST root with
    // no parent. Other declarations retain their existing overload/union owner.
    const owner = ts.isSourceFile(decl) ? decl : decl.parent;
    return `${decl.getSourceFile().fileName}:${owner.pos}:${owner.end}`;
  }));
  const implementations = declarations.filter((decl) => decl.body);
  if (owners.size > 1 || implementations.length > 1) return null;
  const candidate = implementations[0] || symbol.valueDeclaration
    || declarations.find((decl) => included(decl.getSourceFile().fileName)) || declarations[0];
  if (!candidate || !included(candidate.getSourceFile().fileName)) return null;
  const source = candidate.getSourceFile();
  const name = candidate.name && ts.isIdentifier(candidate.name) ? candidate.name.text : symbol.getName();
  return {...place(candidate, source), name, nodeKind: ts.SyntaxKind[candidate.kind]};
}

function targetAt(node) {
  if (!node) return null;
  let symbol = checker.getSymbolAtLocation(node);
  if (!symbol && ts.isPropertyAccessExpression(node)) symbol = checker.getSymbolAtLocation(node.name);
  return declaration(symbol);
}

function moduleTarget(specifier, source) {
  const resolved = ts.resolveModuleName(specifier, source.fileName, options, host).resolvedModule;
  return resolved && included(resolved.resolvedFileName)
    ? {file: relative(resolved.resolvedFileName)} : null;
}

function configuredAlias(specifier) {
  return Object.keys(options.paths || {}).some((pattern) => {
    const star = pattern.indexOf("*");
    return star < 0 ? specifier === pattern
      : specifier.startsWith(pattern.slice(0, star)) && specifier.endsWith(pattern.slice(star + 1));
  });
}

function add(kind, node, source, details) {
  events.push({kind, ...place(node, source), ...details});
}

function exportEvidence(source, exportedName, target) {
  for (const statement of source.statements) {
    if (ts.isExportDeclaration(statement)) {
      if (statement.exportClause && ts.isNamedExports(statement.exportClause)) {
        const specifier = statement.exportClause.elements.find((item) => item.name.text === exportedName);
        if (specifier) return specifier;
      } else if (statement.moduleSpecifier && ts.isStringLiteralLike(statement.moduleSpecifier)
          && moduleTarget(statement.moduleSpecifier.text, source)?.file === target?.file) {
        return statement;
      }
    }
    if (exportedName === "default" && ts.isExportAssignment(statement)) return statement;
  }
  return null;
}

function visit(node, source) {
  if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node) && node.moduleSpecifier) {
    const specifier = node.moduleSpecifier;
    if (specifier && ts.isStringLiteralLike(specifier)) {
      add("import", node, source, {specifier: specifier.text,
          targetModule: moduleTarget(specifier.text, source), alias: configuredAlias(specifier.text),
          reexport: ts.isExportDeclaration(node)});
    }
  }
  if (ts.isCallExpression(node)) {
    const isImport = node.expression.kind === ts.SyntaxKind.ImportKeyword;
    const isRequire = ts.isIdentifier(node.expression) && node.expression.text === "require"
      && !targetAt(node.expression);
    if (isImport || isRequire) {
      const argument = node.arguments[0];
      if (argument && ts.isStringLiteralLike(argument)) {
        add("import", node, source, {specifier: argument.text,
            targetModule: moduleTarget(argument.text, source), alias: configuredAlias(argument.text),
            dynamic: isImport, commonjs: isRequire});
      } else {
        add("unresolved", node, source, {relationship: "IMPORTS",
          reason: isImport ? "dynamic_import_path" : "dynamic_require_path"});
      }
    } else {
      add("call", node, source, {target: targetAt(node.expression),
          expression: node.expression.getText(source)});
    }
  }
  if (ts.isNewExpression(node)) {
    add("construction", node, source, {target: targetAt(node.expression),
        expression: node.expression.getText(source)});
  }
  if (ts.isClassDeclaration(node) || ts.isClassExpression(node) || ts.isInterfaceDeclaration(node)) {
    for (const clause of node.heritageClauses || []) {
      for (const type of clause.types) {
        add("inheritance", type, source, {sourceDeclaration: {...place(node, source), name: node.name?.text},
            target: targetAt(type.expression), expression: type.expression.getText(source),
            relationship: clause.token === ts.SyntaxKind.ImplementsKeyword ? "IMPLEMENTS" : "EXTENDS"});
      }
    }
  }
  // Only emit data references that the checker binds to a declaration. Calls,
  // construction, and imports get their own distinct relationship types.
  if (ts.isIdentifier(node) && node.parent) {
    const parent = node.parent;
    const declarationName = parent.name === node && (
      ts.isDeclaration(parent) || ts.isImportSpecifier(parent) || ts.isExportSpecifier(parent));
    const calleeName = (ts.isCallExpression(parent) || ts.isNewExpression(parent)) && parent.expression === node;
    const propertyCallee = ts.isPropertyAccessExpression(parent) && parent.name === node &&
      (ts.isCallExpression(parent.parent) || ts.isNewExpression(parent.parent));
    if (!declarationName && !calleeName && !propertyCallee &&
        !ts.isImportClause(parent) && !ts.isImportSpecifier(parent) && !ts.isExportSpecifier(parent)) {
      const target = targetAt(node);
      if (target) add("reference", node, source, {target, expression: node.text});
    }
  }
  ts.forEachChild(node, (child) => visit(child, source));
}

for (const file of [...allowed].sort()) {
  const source = program.getSourceFile(file);
  if (!source) {
    diagnostics.push({kind: "missing_source", file: relative(file)});
    continue;
  }
  for (const diagnostic of source.parseDiagnostics || []) {
    diagnostics.push({kind: "syntax_error", file: relative(file),
      detail: ts.flattenDiagnosticMessageText(diagnostic.messageText, " ")});
  }
  visit(source, source);
  const moduleSymbol = checker.getSymbolAtLocation(source);
  if (moduleSymbol) {
    for (const exported of checker.getExportsOfModule(moduleSymbol)) {
      const target = declaration(exported);
      const evidence = exportEvidence(source, exported.getName(), target)
        || exported.declarations?.find((item) => item.getSourceFile() === source) || source;
      add("export", evidence, source, {exportedName: exported.getName(), target});
    }
  }
}

process.stdout.write(JSON.stringify({typescriptVersion: ts.version, events, diagnostics}));
