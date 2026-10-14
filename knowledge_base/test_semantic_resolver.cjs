"use strict";

const assert = require("node:assert/strict");
const {spawnSync} = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const resolver = path.join(__dirname, "semantic_resolver.cjs");

function resolve(files, configs = {}, source = null, missing = []) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "cm-resolver-s1-"));
  const root = fs.realpathSync(temporary);
  try {
    for (const [relative, text] of Object.entries({...files, ...configs})) {
      const target = path.join(root, relative);
      fs.mkdirSync(path.dirname(target), {recursive: true});
      fs.writeFileSync(target, text);
    }
    const input = {root, files: [...Object.keys(files), ...missing], configs: Object.keys(configs)};
    const result = spawnSync(process.execPath, source === null ? [resolver] : ["-e", source], {
      cwd: __dirname, input: JSON.stringify(input), encoding: "utf8", timeout: 30000,
    });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stderr, "");
    return JSON.parse(result.stdout);
  } finally {
    fs.rmSync(temporary, {recursive: true, force: true});
  }
}

test("namespace import has a parentless SourceFile declaration and keeps its resolved member call", () => {
  const result = resolve({
    "dep.ts": "export function run() { return 1; }\n",
    "use.ts": "import * as lib from './dep';\nexport function entry() { return lib.run(); }\n",
  });
  assert.ok(result.events.some(event => event.kind === "call" && event.expression === "lib.run" && event.target?.file === "dep.ts" && event.target.name === "run"));
  assert.ok(result.events.some(event => event.kind === "import" && event.targetModule?.file === "dep.ts"));
  assert.deepEqual(result.diagnostics, []);
});

test("namespace reexports preserve the member target through a barrel", () => {
  const result = resolve({
    "dep.ts": "export function run() { return 1; }\n",
    "barrel.ts": "export * as lib from './dep';\n",
    "use.ts": "import {lib} from './barrel'; export function entry() { return lib.run(); }\n",
  });
  assert.ok(result.events.some(event => event.kind === "call" && event.target?.file === "dep.ts" && event.target.name === "run"));
  assert.ok(result.events.some(event => event.kind === "export" && event.exportedName === "lib" && event.target?.nodeKind === "SourceFile"));
  assert.deepEqual(result.diagnostics, []);
});

test("CommonJS object exports reproduce the checkout crash and retain supported calls", () => {
  const files = {"use.js": "function run() { return 1; }\nfunction entry() { return run(); }\nmodule.exports = {run, entry};\n"};
  const result = resolve(files);
  assert.ok(result.events.some(event => event.kind === "call" && event.expression === "run" && event.target?.name === "run"));
  assert.deepEqual(result.diagnostics, []);
  const current = fs.readFileSync(resolver, "utf8");
  const legacy = current.replace(/const owners = new Set\(declarations\.map\(\(decl\) => \{[\s\S]*?\n  \}\)\);/,
    'const owners = new Set(declarations.map((decl) => `${decl.getSourceFile().fileName}:${decl.parent.pos}:${decl.parent.end}`));');
  assert.throws(() => resolve(files, {}, legacy), /Cannot read properties of undefined \(reading 'pos'\)/);
});

test("JavaScript namespace imports and mixed TS/JS targets remain supported", () => {
  const result = resolve({
    "dep.js": "export function run() { return 1; }\n",
    "use.ts": "import * as lib from './dep.js'; export function entry() { return lib.run(); }\n",
    "main.js": "import * as app from './use'; export function main() { return app.entry(); }\n",
  });
  const calls = result.events.filter(event => event.kind === "call");
  assert.deepEqual(calls.map(event => [event.expression, event.target?.file, event.target?.name]), [
    ["app.entry", "use.ts", "entry"], ["lib.run", "dep.js", "run"],
  ]);
  assert.deepEqual(result.diagnostics, []);
});

test("missing source, missing symbol and malformed syntax have explicit conservative outcomes", () => {
  const result = resolve({
    "broken.ts": "import * as missing from './absent'; export function broken() { missing.run(); unknown(); }\nconst malformed = ;\n",
    "partial.ts": "export function partial() { return missing( ; }\n",
  });
  assert.ok(result.diagnostics.some(item => item.kind === "syntax_error" && item.file === "broken.ts"));
  assert.ok(result.diagnostics.some(item => item.kind === "syntax_error" && item.file === "partial.ts"));
  assert.ok(result.events.some(item => item.kind === "import" && item.targetModule === null));
  assert.ok(result.events.some(item => item.kind === "call" && item.expression === "missing.run" && item.target === null));
  assert.ok(result.events.some(item => item.kind === "call" && item.expression === "unknown" && item.target === null));
  const absent = resolve({}, {}, null, ["absent.ts"]);
  assert.deepEqual(absent.events, []);
  assert.deepEqual(absent.diagnostics, [{kind: "missing_source", file: "absent.ts"}]);
});

test("empty calls and dynamic paths do not invent missing nodes or targets", () => {
  const result = resolve({"use.ts": "export function run(path: string) { require(); import(path); return unknown(); }\n"});
  assert.ok(result.events.some(event => event.kind === "unresolved" && event.reason === "dynamic_require_path"));
  assert.ok(result.events.some(event => event.kind === "unresolved" && event.reason === "dynamic_import_path"));
  assert.ok(result.events.some(event => event.kind === "call" && event.target === null));
});

test("union receivers remain ambiguous while overload calls target the implementation", () => {
  const source = `
export class First { run(value: string) { return value; } }
export class Second { run(value: string) { return value; } }
export function ambiguous(value: First | Second) { return value.run('x'); }
export function overloaded(value: string): string;
export function overloaded(value: number): number;
export function overloaded(value: any) { return value; }
export function invoke() { return overloaded('x'); }
`;
  const result = resolve({"use.ts": source});
  assert.equal(result.events.find(event => event.kind === "call" && event.expression === "value.run").target, null);
  const call = result.events.find(event => event.kind === "call" && event.expression === "overloaded");
  assert.equal(call.target.name, "overloaded");
  assert.equal(call.target.startByte, Buffer.from(source).indexOf("export function overloaded(value: any)"));
});

test("namespace cycles, nested calls and aliases keep distinct file identities", () => {
  const result = resolve({
    "one.ts": "import * as two from './two'; export function run() { return two.run(); }\n",
    "two.ts": "import * as one from './one'; export function run() { return one.run(); }\n",
    "nested.ts": "import * as first from './one'; import * as second from './two'; export function outer() { function inner() { return first.run(); } inner(); return second.run(); }\n",
  });
  const targets = result.events.filter(event => event.kind === "call").map(event => [event.file, event.expression, event.target?.file, event.target?.name]);
  assert.ok(targets.some(item => item.join(":") === "one.ts:two.run:two.ts:run"));
  assert.ok(targets.some(item => item.join(":") === "two.ts:one.run:one.ts:run"));
  assert.ok(targets.some(item => item.join(":") === "nested.ts:inner:nested.ts:inner"));
  assert.ok(targets.some(item => item.join(":") === "nested.ts:first.run:one.ts:run"));
  assert.ok(targets.some(item => item.join(":") === "nested.ts:second.run:two.ts:run"));
});

test("named-import, inheritance, nested, CommonJS and ambiguous outputs equal the pre-fix resolver", () => {
  const files = {
    "dep.ts": "export function run() { return 1; }\nexport class Base { check() { return true; } }\n",
    "use.ts": "import {run, Base} from './dep'; export class Child extends Base {}\nexport function outer() { function inner() { return run(); } return inner(); }\n",
    "legacy.cjs": "module.exports = function helper() { return 1; };\n",
    "consumer.js": "const helper = require('./legacy.cjs'); module.exports = () => helper();\n",
    "ambiguous.ts": "export class A { run() { return 1; } } export class B { run() { return 2; } } export function use(value: A | B) { return value.run(); }\n",
  };
  const current = fs.readFileSync(resolver, "utf8");
  // The released owner calculation is the behavioral oracle for inputs that
  // worked before S1. All remaining resolver logic is exactly the same source.
  const legacy = current.replace(/const owners = new Set\(declarations\.map\(\(decl\) => \{[\s\S]*?\n  \}\)\);/,
    'const owners = new Set(declarations.map((decl) => `${decl.getSourceFile().fileName}:${decl.parent.pos}:${decl.parent.end}`));');
  assert.notEqual(legacy, current);
  assert.deepEqual(resolve(files), resolve(files, {}, legacy));
});
