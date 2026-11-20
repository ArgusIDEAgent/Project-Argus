"""Preserve the retired Node implementation as inert qualification input."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from query_system.qualify_phase3 import PROFILES
from knowledge_base.verify_resolver_s1 import copy_text


class LegacyNodeCorpusTests(unittest.TestCase):
    def test_relocated_profile_preserves_original_bytes_and_relative_paths(self):
        fixtures = Path(__file__).parent / "benchmarks/fixtures"
        manifest = json.loads((fixtures / "legacy_node_manifest.json").read_text())
        source, prefix = PROFILES["node"]
        self.assertEqual(source, fixtures / "vendor/legacy_node_server")
        self.assertEqual(prefix, "")
        expected = {item["path"]: item["sha256"] for item in manifest["files"]}
        self.assertEqual(len(expected), 16)
        self.assertEqual({p.name for p in source.iterdir()}, set(expected))
        for name, digest in expected.items():
            self.assertEqual(hashlib.sha256((source / name).read_bytes()).hexdigest(), digest, name)
        with tempfile.TemporaryDirectory(prefix="cm-legacy-corpus-") as temporary:
            copied = copy_text(source, Path(temporary))
            self.assertEqual({name: digest for name, _, digest in copied}, expected)


if __name__ == "__main__":
    unittest.main()
