"""Independent Phase 3 graph fixture; run with python -m unittest knowledge_base.test_graph."""

import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

from knowledge_base.graph import KnowledgeGraph, build_graph
from knowledge_base.scanner import scan_repository


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.data = Path(self.temp.name) / "private"
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def build(self):
        result = build_graph(self.root, data_dir=self.data)
        return result, KnowledgeGraph(result["database"], result["repository_id"])

    def test_semantic_alias_import_calls_and_reverse_paths(self):
        self.write("tsconfig.base.json", json.dumps({"compilerOptions": {
            "baseUrl": ".", "paths": {"@lib/*": ["src/lib/*"]}}}))
        self.write("tsconfig.json", json.dumps({"extends": "./tsconfig.base.json"}))
        self.write("src/lib/auth.ts", """export function validateToken(token: string) { return token.length > 0; }
export interface User { id: string }
export class Base { check() { return true; } }
export class AuthService extends Base implements User {
  id = 'one';
  validate(token: string) { return validateToken(token); }
}
""")
        self.write("src/controller.ts", """import { validateToken as check, AuthService } from '@lib/auth';
export function login() { const service = new AuthService(); return check('ok') && service.validate('ok'); }
export function dynamic(obj: any, key: string) { return obj[key](); }
""")
        self.write("src/entry.ts", """import { login } from './controller';
export function main() { return login(); }
""")
        result, graph = self.build()
        self.assertEqual(result["parsing"]["parsed"], 3)
        self.assertEqual(result["semantic_diagnostics"], [])
        self.assertTrue(result["graph"]["resolved"] > 0)
        login = graph.symbol("login")
        validate = graph.symbol("validateToken")
        main = graph.symbol("main")
        callees = graph.direct_callees(login["symbol_id"])
        self.assertIn("validateToken", {edge["other_label"] for edge in callees})
        callers = graph.direct_callers(validate["symbol_id"])
        self.assertIn("login", {edge["other_label"] for edge in callers})
        self.assertIn("AuthService.validate", {edge["other_label"] for edge in callers})
        method = graph.symbol("AuthService.validate")
        self.assertIn("login", {edge["other_label"] for edge in graph.direct_callers(method["symbol_id"])})
        self.assertIn("main", {edge["other_label"] for edge in graph.direct_callers(login["symbol_id"])})
        self.assertEqual(graph.dependency_paths("src/entry.ts", "src/lib/auth.ts", max_depth=2),
                         [["src/entry.ts", "src/controller.ts", "src/lib/auth.ts"]])
        self.assertIn("src/lib/auth.ts", {edge["other_path"] for edge in graph.imports("src/controller.ts")})
        self.assertIn("src/controller.ts", {edge["other_path"] for edge in graph.reverse_dependencies("src/lib/auth.ts")})
        with closing(sqlite3.connect(result["database"])) as db:
            rows = db.execute("""SELECT relationship, a.label, b.label, resolution,
                evidence_type, evidence_file, evidence_line
                FROM graph_relationships e JOIN graph_nodes a ON a.id=e.source_id
                JOIN graph_nodes b ON b.id=e.target_id
                WHERE relationship IN ('EXTENDS','IMPLEMENTS')""").fetchall()
            self.assertIn(("EXTENDS", "AuthService", "Base", "resolved", "semantic_reference",
                           "src/lib/auth.ts", 4), rows)
            self.assertIn(("IMPLEMENTS", "AuthService", "User", "resolved", "semantic_reference",
                           "src/lib/auth.ts", 4), rows)
            unresolved = db.execute("SELECT relationship, expression, reason FROM graph_unresolved").fetchall()
            self.assertTrue(any(row[0] == "CALLS" and "obj[key]" in row[1] for row in unresolved))
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_missing_targets_are_not_guessed_and_rescan_invalidates_graph(self):
        self.write("a.ts", """import {value} from './missing';
export function a(fn: any) { return fn(); }
""")
        result, graph = self.build()
        self.assertEqual(graph.imports("a.ts"), [])
        self.assertIn("local_module_not_indexed_or_missing",
                      {item["reason"] for item in graph.unresolved_references(module_path="a.ts")})
        with closing(sqlite3.connect(result["database"])) as db:
            unresolved = db.execute("SELECT relationship, reason FROM graph_unresolved").fetchall()
            self.assertIn(("IMPORTS", "local_module_not_indexed_or_missing"), unresolved)
            self.assertIn(("CALLS", "target_not_uniquely_indexed"), unresolved)
            self.assertFalse(db.execute("SELECT count(*) FROM graph_relationships WHERE relationship='CALLS'").fetchone()[0])
        scan_repository(self.root, data_dir=self.data)
        with self.assertRaisesRegex(RuntimeError, "stale"):
            graph.imports("a.ts")

    def test_disk_edit_invalidates_graph_even_without_rescan(self):
        self.write("a.ts", "export function a() { return 1; }\n")
        _, graph = self.build()
        self.write("a.ts", "export function a() { return 2; }\n")
        with self.assertRaisesRegex(RuntimeError, "stale"):
            graph.symbol("a")

    def test_deletion_rebuild_removes_edges(self):
        self.write("a.ts", "export function a() { return 1; }\n")
        self.write("b.ts", "import {a} from './a'; export function b() { return a(); }\n")
        first, graph = self.build()
        self.assertTrue(graph.direct_callers(graph.symbol("a")["symbol_id"]))
        (self.root / "a.ts").unlink()
        second, graph = self.build()
        self.assertFalse(graph.imports("b.ts"))
        with closing(sqlite3.connect(second["database"])) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM graph_nodes WHERE relative_path='a.ts'").fetchone()[0], 0)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_same_names_bind_to_the_imported_file_not_the_nearest_spelling(self):
        self.write("a.ts", "export function run() { return 'a'; }\n")
        self.write("b.ts", "export function run() { return 'b'; }\n")
        self.write("caller.ts", """import {run as first} from './a';
import {run as second} from './b';
export function start() { first(); second(); }
""")
        result, graph = self.build()
        start = graph.symbol("start")
        callees = graph.direct_callees(start["symbol_id"])
        self.assertEqual({edge["other_path"] for edge in callees}, {"a.ts", "b.ts"})
        with self.assertRaisesRegex(ValueError, "found 2"):
            graph.symbol("run")
        with closing(sqlite3.connect(result["database"])) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM graph_relationships WHERE relationship='CALLS' AND resolution='inferred'").fetchone()[0], 0)

    def test_missing_alias_and_commonjs_require(self):
        self.write("tsconfig.json", json.dumps({"compilerOptions": {
            "baseUrl": ".", "paths": {"@app/*": ["src/*"]}}}))
        self.write("src/helper.cjs", "module.exports = function helper() { return 1; };\n")
        self.write("src/main.js", """const helper = require('./helper.cjs');
const absent = require('@app/missing');
module.exports = () => helper();
""")
        result, graph = self.build()
        self.assertIn("src/helper.cjs", {edge["other_path"] for edge in graph.imports("src/main.js")})
        with closing(sqlite3.connect(result["database"])) as db:
            unresolved = db.execute("SELECT relationship, expression FROM graph_unresolved").fetchall()
            self.assertIn(("IMPORTS", "@app/missing"), unresolved)
            self.assertFalse(db.execute("SELECT count(*) FROM graph_nodes WHERE kind='ExternalDependency' AND label='@app/missing'").fetchone()[0])

    def test_reexport_evidence_points_to_exporting_file(self):
        self.write("src/a.ts", "export function make() { return 1; }\n")
        self.write("src/barrel.ts", "export {make as build} from './a';\n")
        self.write("src/use.ts", "import {build} from './barrel'; export function use() { return build(); }\n")
        result, graph = self.build()
        self.assertIn("make", {item["other_label"] for item in graph.direct_callees(graph.symbol("use")["symbol_id"])})
        with closing(sqlite3.connect(result["database"])) as db:
            rows = db.execute("""SELECT e.evidence_file, e.evidence_line, b.label, e.resolution
                FROM graph_relationships e JOIN graph_nodes a ON a.id=e.source_id
                JOIN graph_nodes b ON b.id=e.target_id
                WHERE e.relationship='EXPORTS' AND a.relative_path='src/barrel.ts'""").fetchall()
            self.assertIn(("src/barrel.ts", 1, "make", "resolved"), rows)


if __name__ == "__main__":
    unittest.main()
