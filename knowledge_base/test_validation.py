"""Independent gold and future change-impact contracts (no embedding model)."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from knowledge_base.graph import build_graph, KnowledgeGraph
from knowledge_base.metadata import trace
from knowledge_base.validation import evaluate_graph, prepare_fixture, compare_sets, percentile, citation_valid


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='cm-gold-tests-')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.fixtures = {}
        for language in ('javascript', 'typescript'):
            root = Path(cls.temp.name)/language
            prepare_fixture(root, language)
            built = build_graph(root, data_dir=Path(cls.temp.name)/'data')
            cls.fixtures[language] = (root, built, KnowledgeGraph(built['database'], built['repository_id']))

    def test_hand_authored_quality_gates_for_both_languages(self):
        for language, (_, built, _) in self.fixtures.items():
            with self.subTest(language=language):
                quality = evaluate_graph(built['database'], built['repository_id'], language)
                self.assertTrue(quality['passed'], json.dumps(quality['gates']))
                self.assertEqual(quality['symbols']['precision'], 1.)
                self.assertEqual(quality['relationships']['precision'], 1.)
                self.assertEqual(quality['unsupported']['recall'], 1.)

    def test_ambiguous_receiver_and_unknown_callbacks_are_not_verified(self):
        graph = self.fixtures['typescript'][2]
        for name in ('unionDispatch', 'unresolvedDispatch', 'invokeDynamic', 'applyValidator'):
            identifier = graph.symbol(name)['symbol_id']
            self.assertEqual(graph.direct_callees(identifier), [])
            self.assertTrue(graph.unresolved_references(symbol_id=identifier))

    def test_overloaded_method_callers_target_implementation_not_signature(self):
        _, built, graph = self.fixtures['typescript']
        with closing(sqlite3.connect(built['database'])) as db:
            rows = db.execute("SELECT id,kind,start_line FROM symbols WHERE qualified_name='SecureService.check'").fetchall()
        for identifier, kind, line in rows:
            callers = graph.direct_callers(identifier)
            if kind == 'method':
                self.assertEqual(line, 12)
                self.assertEqual({r['other_label'] for r in callers}, {'requestLogin', 'methodTest'})
            else:
                self.assertEqual(callers, [])

    def test_function_paths_canonical_ids_and_evidence_are_traceable(self):
        _, built, graph = self.fixtures['typescript']
        source, target = (graph.symbol(n)['symbol_id'] for n in ('applicationEntry', 'validateCredential'))
        result = graph.symbol_dependency_paths(source, target)
        self.assertFalse(result['truncated'])
        self.assertEqual(len(result['paths']), 1)
        self.assertEqual(len(result['paths'][0]['symbolIds']), 4)
        for identifier in result['paths'][0]['graphEdges']:
            self.assertEqual(trace(built['database'], built['repository_id'], identifier)['relationship'], 'CALLS')
        self.assertIn('Potentially affected', result['interpretation'])
        self.assertTrue(graph.symbol_dependency_paths(source, target, max_depth=1)['truncated'])
        self.assertTrue(graph.symbol_dependency_paths(source, target, max_work=1)['truncated'])
        self.assertEqual(graph.symbol_dependency_paths('missing', target)['paths'], [])

    def test_cycle_safe_and_invalid_budgets_fail(self):
        graph = self.fixtures['typescript'][2]
        a, b = (graph.symbol(n)['symbol_id'] for n in ('cycleA', 'cycleB'))
        self.assertEqual(len(graph.symbol_dependency_paths(a, b, max_depth=20)['paths']), 1)
        self.assertEqual(graph.symbol_dependency_paths(a, a)['paths'], [])
        for option in ({'max_depth':0}, {'max_depth':21}, {'max_paths':0}, {'max_work':50001}):
            with self.assertRaises(ValueError): graph.symbol_dependency_paths(a, b, **option)

    def test_module_dependents_and_dependency_path_contract(self):
        graph = self.fixtures['typescript'][2]
        self.assertIn('src/controller.ts', {r['other_path'] for r in graph.reverse_dependencies('src/auth.ts')})
        self.assertIn(['src/entry.ts', 'src/controller.ts', 'src/auth.ts'], graph.dependency_paths('src/entry.ts', 'src/auth.ts'))

    def test_test_references_use_canonical_identity_not_duplicate_names(self):
        _, built, graph = self.fixtures['typescript']
        with closing(sqlite3.connect(built['database'])) as db:
            identifiers = dict(db.execute("SELECT relative_path,id FROM symbols WHERE qualified_name='authorizeRequest'"))
        self.assertTrue(graph.test_references(identifiers['src/auth.ts'])['references'])
        self.assertEqual(graph.test_references(identifiers['src/alternate.ts'])['references'], [])
        references = graph.test_references(graph.symbol('validateCredential')['symbol_id'])
        self.assertTrue(any(r['relationship']=='REFERENCES' for r in references['references']))
        self.assertIn('not proof', references['interpretation'])
        limited = graph.test_references(identifiers['src/auth.ts'], limit=1)
        self.assertEqual(len(limited['references']), 1)
        with self.assertRaises(ValueError): graph.test_references('missing', limit=0)

    def test_multiline_utf8_crlf_evidence_and_in_bounds_line_tampering(self):
        with tempfile.TemporaryDirectory(prefix='cm-citation-test-') as folder:
            root = Path(folder)/'repo'; root.mkdir()
            (root/'source.ts').write_bytes('export function target(value: string) { return value; }\r\nexport function caller() {\r\n  return target(\r\n    "é"\r\n  );\r\n}\r\n'.encode())
            built = build_graph(root, data_dir=Path(folder)/'private')
            with closing(sqlite3.connect(built['database'])) as db:
                identifier = db.execute("SELECT id FROM relationships WHERE relationship='CALLS'").fetchone()[0]
            info = trace(built['database'], built['repository_id'], identifier)
            self.assertEqual((info['startLine'], info['endLine']), (3, 5))
            with closing(sqlite3.connect(built['database'])) as db, db:
                db.execute('UPDATE relationships SET start_line=2 WHERE id=?', (identifier,))
            with self.assertRaisesRegex(ValueError, 'disagree'):
                trace(built['database'], built['repository_id'], identifier)

    def test_metric_differences_and_empty_gold(self):
        metric = compare_sets(['a','b'], ['a','c'])
        self.assertEqual((metric['precision'], metric['recall']), (.5,.5))
        self.assertEqual(metric['missing'], ['b'])
        self.assertEqual(compare_sets(['a'], [])['precision'], 0.)
        self.assertEqual(percentile([1,2,3,4], .95), 4)
        self.assertIsNone(percentile([], .95))

    def test_citation_identity_mismatch_cannot_pass_as_same_span(self):
        root, built, graph = self.fixtures['typescript']
        info = trace(built['database'], built['repository_id'], graph.symbol('validateCredential')['symbol_id'])
        fields = ('path','startLine','endLine','sourceHash','entityId','fileId','symbolId','snapshotId','sourceOrigin','schemaVersion')
        item = {key:info[key] for key in fields}
        self.assertTrue(citation_valid(root,built['database'],built['repository_id'],dict(item),item))
        for key in ('entityId','fileId','sourceHash','snapshotId'):
            self.assertFalse(citation_valid(root,built['database'],built['repository_id'],{**item,key:'wrong'},item))

    def test_reports_never_overwrite_existing_outputs(self):
        with tempfile.TemporaryDirectory(prefix='cm-report-test-') as folder:
            path = Path(folder)/'quality-report.json'; path.write_text('keep this report')
            process = subprocess.run([sys.executable, '-m', 'knowledge_base.validation', '--output', folder,
                                      '--structural-only', '--skip-regressions', '--sizes', '0'], capture_output=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(path.read_text(), 'keep this report')


if __name__ == '__main__': unittest.main()
