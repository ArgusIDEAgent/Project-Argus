"""Phase 1 inventory regression tests; run with python -m unittest knowledge_base.test_scanner."""

from pathlib import Path
from contextlib import closing
import sqlite3
import subprocess
import tempfile
import unittest

from knowledge_base.scanner import scan_repository


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.data = Path(self.temp.name) / "data"
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def write(self, path, content):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
        return target

    def rows(self, result):
        with closing(sqlite3.connect(result["database"])) as db:
            return {row[0]: row[1:] for row in db.execute(
                "SELECT relative_path, status, reason, kind, language, size_bytes, content_sha256 FROM files")}

    def test_inventory_exclusions_and_evidence(self):
        self.write(".gitignore", "cache/\n*.log\n!keep.log\n")
        self.write("src/.gitignore", "*.ts\n!keep.ts\n")
        self.write("src/keep.ts", "export const ok = 1;\n")
        self.write("src/drop.ts", "export const bad = 1;\n")
        self.write("keep.log", "allowed, but unsupported")
        self.write("cache/hidden.ts", "do not traverse")
        self.write("node_modules/lib.ts", "do not traverse")
        self.write("package.json", '{"name":"fixture"}')
        self.write("tsconfig.json", '{"compilerOptions":{}}')
        self.write("notes.py", "print('unsupported')")
        self.write(".env", "API_KEY=secret")
        self.write("id_rsa", "private")
        self.write("large.ts", "x" * 100)
        self.write("binary.ts", b"abc\0def")
        self.write("secret.ts", "const token = 'AKIAABCDEFGHIJKLMNOP';")
        self.write("custom.ts", "export {}")
        (self.root / "linked.ts").symlink_to(self.root / "src/keep.ts")
        result = scan_repository(self.root, data_dir=self.data,
                                 exclusions=("custom.ts",), max_file_bytes=80)
        rows = self.rows(result)
        self.assertEqual(rows["src/keep.ts"][0], "included")
        self.assertEqual(rows["src/keep.ts"][2], "source")
        self.assertEqual(rows["src/keep.ts"][3], "typescript")
        self.assertEqual(len(rows["src/keep.ts"][5]), 64)
        self.assertEqual(rows["src/drop.ts"][0], "ignored")
        self.assertIn("gitignore", rows["src/drop.ts"][1])
        self.assertEqual(rows["keep.log"][0], "unsupported")
        self.assertEqual(rows["cache"][0], "ignored")
        self.assertNotIn("cache/hidden.ts", rows)
        self.assertEqual(rows["node_modules"][1], "excluded_directory")
        self.assertEqual(rows["package.json"][2], "config")
        self.assertEqual(rows["tsconfig.json"][0], "included")
        self.assertEqual(rows[".env"][1], "sensitive_filename")
        self.assertEqual(rows["id_rsa"][1], "sensitive_filename")
        self.assertEqual(rows["large.ts"][1], "too_large")
        self.assertEqual(rows["binary.ts"][1], "binary")
        self.assertEqual(rows["secret.ts"][1], "possible_embedded_secret")
        self.assertEqual(rows["linked.ts"][1], "symlink")
        self.assertEqual(rows["custom.ts"][1], "custom_exclusion")
        self.assertEqual(result["statistics"]["source_files"], 1)
        self.assertEqual(len(rows), len(set(rows)))
        self.assertTrue(Path(result["database"]).is_file())
        with closing(sqlite3.connect(result["database"])) as db:
            self.assertEqual(db.execute("SELECT parse_status FROM files WHERE relative_path='src/keep.ts'").fetchone()[0], "pending")
            self.assertEqual(db.execute("SELECT parse_status FROM files WHERE relative_path='package.json'").fetchone()[0], "not_applicable")

    def test_rescan_replaces_deleted_and_modified_files(self):
        self.write("a.ts", "export const a = 1;")
        self.write("b.ts", "export const b = 1;")
        first = scan_repository(self.root, data_dir=self.data)
        old_hash = self.rows(first)["a.ts"][5]
        (self.root / "b.ts").unlink()
        self.write("a.ts", "export const a = 2;")
        second = scan_repository(self.root, data_dir=self.data)
        rows = self.rows(second)
        self.assertNotIn("b.ts", rows)
        self.assertNotEqual(rows["a.ts"][5], old_hash)
        with closing(sqlite3.connect(second["database"])) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM inventory_runs").fetchone()[0], 2)
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_refuses_storage_inside_repository(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            scan_repository(self.root, data_dir=self.root / ".codemind")

    def test_git_root_with_trailing_space(self):
        spaced_root = self.root.with_name("repo ")
        self.root.rename(spaced_root)
        self.root = spaced_root
        self.write("a.ts", "export const a = 1;")
        result = scan_repository(self.root, data_dir=self.data)
        self.assertEqual(result["root_path"], str(self.root.resolve()))
        self.assertEqual(self.rows(result)["a.ts"][0], "included")

    def test_non_git_directory_with_ignore_rules_is_rejected(self):
        local = Path(self.temp.name) / "plain"
        local.mkdir()
        (local / ".gitignore").write_text("ignored.ts\n")
        with self.assertRaisesRegex(ValueError, "Git repository"):
            scan_repository(local, data_dir=self.data)
        (local / ".gitignore").unlink()
        (local / "app.js").write_text("export const value = 1;")
        result = scan_repository(local, data_dir=self.data)
        self.assertIsNone(result["git_revision"])
        self.assertEqual(self.rows(result)["app.js"][0], "included")


if __name__ == "__main__":
    unittest.main()
