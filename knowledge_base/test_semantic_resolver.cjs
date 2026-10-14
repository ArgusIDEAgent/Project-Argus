"use strict";

const assert = require("node:assert/strict");
const {spawnSync} = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const resolver = path.join(__dirname, "semantic_resolver.cjs");

function resolve(files, configs = {}) {
  const temporary = fs.mkdtempSync(path.join(os.tmpdir(), "cm-resolver-s1-"));
  const root = fs.realpathSync(temporary);
  try {
    for (const [relative, text] of Object.entries({...files, ...configs})) {
      const target = path.join(root, relative);
      fs.mkdirSync(path.dirname(target), {recursive: true});
      fs.writeFileSync(target, text);
    }
    const input = {root, files: Object.keys(files), configs: Object.keys(configs)};
    const result = spawnSync(process.execPath, [resolver], {
      input: JSON.stringify(input), encoding: "utf8", timeout: 30000,
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
});
