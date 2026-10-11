"""Phase 2 syntax extraction tests; run with python -m unittest knowledge_base.test_parser."""

from pathlib import Path
from contextlib import closing
import sqlite3
import subprocess
import tempfile
import unittest

from knowledge_base.parser import parse_repository, parse_source


class ParserTests(unittest.TestCase):
    def parse(self, code, suffix=".ts"):
        return parse_source(code.encode(), suffix=suffix,
                            repository_id="fixture", relative_path="src/example" + suffix)

    def test_typescript_entities_scopes_and_exact_spans(self):
        code = """/** service doc */
export class Service extends Base implements Runner {
  @memo
  run(x: string): number {
    function nested(y: number) { return util(y); }
    return new Builder().go(x);
  }
}
interface Runner { run(x: string): number }
type Result = string | number;
function overloaded(x: string): string;
function overloaded(x: number): number;
function overloaded(x: unknown) { return x; }
const callback = (value: number): number => value + 1;
items.map((item) => callback(item));
import {make as create} from './factory';
export {create as maker};
"""
        parsed = self.parse(code)
        self.assertEqual(parsed.status, "parsed")
        by_name = {s["qualified_name"]: s for s in parsed.symbols}
        self.assertEqual(by_name["Service"]["kind"], "class")
        self.assertTrue(by_name["Service"]["exported"])
        self.assertEqual(by_name["Service"]["doc_comment"], "/** service doc */")
        self.assertEqual(by_name["Service"]["start_line"], 2)
        self.assertEqual(by_name["Service.run"]["kind"], "method")
        self.assertEqual(by_name["Service.run"]["parameters"][0]["type"], ": string")
        self.assertEqual(by_name["Service.run"]["return_annotation"], ": number")
        self.assertEqual(by_name["Service.run.nested"]["parent_symbol_id"], by_name["Service.run"]["id"])
        self.assertEqual(by_name["Runner"]["kind"], "interface")
        self.assertEqual(by_name["Result"]["kind"], "type_alias")
        self.assertEqual(by_name["callback"]["kind"], "arrow_function")
        overloads = [s for s in parsed.symbols if s["name"] == "overloaded"]
        self.assertEqual(len(overloads), 3)
        self.assertEqual(len({s["id"] for s in overloads}), 3)
        self.assertEqual(sum(s["kind"] == "function_overload" for s in overloads), 2)
        kinds = {r["kind"] for r in parsed.references}
        self.assertTrue({"call", "construction", "inheritance", "decorator", "import", "export"} <= kinds)
        self.assertTrue(any(r["kind"] == "import" and r["metadata"]["local"] == "create"
                            for r in parsed.references))
        self.assertTrue(any(r["kind"] == "export" and r["metadata"]["exported"] == "maker"
                            for r in parsed.references))
        self.assertTrue(any(r["kind"] == "call" and r["expression"] == "util"
                            and r["scope_symbol_id"] == by_name["Service.run.nested"]["id"]
                            for r in parsed.references))

    def test_js_anonymous_duplicate_and_stable_identity(self):
        code = """function outer() {
  const x = () => 1;
  arr.map(() => x());
  arr.map(() => x());
  function same() {}
  function same() {}
}
"""
        first = self.parse(code, ".js")
        second = self.parse("// inserted header\n" + code, ".js")
        self.assertEqual(first.status, "parsed")
        self.assertEqual({s["id"] for s in first.symbols}, {s["id"] for s in second.symbols})
        self.assertEqual(len(first.symbols), len({s["id"] for s in first.symbols}))
        self.assertEqual(sum(s["name"].startswith("<anonymous:") for s in first.symbols), 2)
        self.assertEqual(sum(s["name"] == "same" for s in first.symbols), 2)
        self.assertEqual(first.symbols[0]["start_line"] + 1, second.symbols[0]["start_line"])

    def test_jsx_tsx_unicode_and_error_status(self):
        jsx = self.parse("const Card = ({x}: Props) => <div>{x}</div>;", ".tsx")
        self.assertEqual(jsx.status, "parsed")
        self.assertEqual(jsx.symbols[0]["name"], "Card")
        self.assertTrue(any(s["kind"] == "arrow_function" for s in jsx.symbols))
        unicode = self.parse("const café = () => 1;", ".js")
        self.assertEqual(unicode.symbols[0]["start_column"], 14)
        self.assertEqual(unicode.symbols[0]["start_byte"], 14)
        broken = self.parse("function broken( {", ".js")
        self.assertEqual(broken.status, "partial")
        self.assertTrue(broken.diagnostics)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.parse("x", ".py")

    def test_dynamic_import_namespace_and_default_export(self):
        parsed = self.parse("""import * as api from 'pkg';
interface A extends B, C { run(x?: number): void }
export default (value) => import('lazy').then(mod => new mod.Widget(value));
""")
        self.assertEqual(parsed.status, "parsed")
        self.assertTrue(any(s["kind"] == "arrow_function" and s["default_export"]
                            for s in parsed.symbols))
        self.assertTrue(any(r["kind"] == "import" and r["metadata"].get("local") == "api"
                            for r in parsed.references))
        self.assertEqual(sum(r["kind"] == "inheritance" for r in parsed.references), 2)
        self.assertTrue(any(r["kind"] == "dynamic_import" for r in parsed.references))
        self.assertTrue(any(r["kind"] == "construction" for r in parsed.references))

    def test_persistence_and_rescan_remove_deleted_symbols(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            root.mkdir()
            data = Path(directory) / "private"
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "a.ts").write_text("export function find() { return lookup(); }\n")
            (root / "b.js").write_text("const removed = () => 1;\n")
            (root / "vite.config.ts").write_text("export default function setup() {}\n")
            result = parse_repository(root, data_dir=data)
            self.assertEqual(result["parsing"]["parsed"], 3)
            with closing(sqlite3.connect(result["database"])) as db:
                rows = db.execute("SELECT name, id FROM symbols WHERE relative_path='a.ts'").fetchall()
                self.assertEqual(rows[0][0], "find")
                self.assertEqual(db.execute("SELECT parse_status FROM files WHERE relative_path='a.ts'").fetchone()[0], "parsed")
                self.assertEqual(db.execute("SELECT count(*) FROM symbols WHERE relative_path='vite.config.ts'").fetchone()[0], 1)
                self.assertEqual(db.execute("SELECT kind, expression FROM syntax_references WHERE relative_path='a.ts'").fetchall(), [("call", "lookup")])
            (root / "b.js").unlink()
            (root / "a.ts").write_text("// shifted\nexport function find() { return lookup(); }\n")
            again = parse_repository(root, data_dir=data)
            with closing(sqlite3.connect(again["database"])) as db:
                self.assertEqual(db.execute("SELECT id FROM symbols WHERE relative_path='a.ts'").fetchone()[0], rows[0][1])
                self.assertEqual(db.execute("SELECT count(*) FROM symbols WHERE relative_path='b.js'").fetchone()[0], 0)
                self.assertEqual(db.execute("SELECT count(*) FROM files WHERE relative_path='b.js'").fetchone()[0], 0)
                self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
