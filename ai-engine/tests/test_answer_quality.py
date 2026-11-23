"""Bounded source preparation and adaptive explanation budgets."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from answers import answer_question, evidence_for
from test_semantic_answers import model_reply
from knowledge_base.retrieval import HybridRetriever, plan_query
from unittest.mock import Mock


def test_large_source_leaves_room_for_later_evidence_and_marks_gaps():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        items = []
        for name, content in [('entry.ts', 'start();\n' + 'work();\n' * 4000 + 'return result;\n'),
                              ('output.ts', 'export const result = 42;\n')]:
            path = root / name
            path.write_text(content)
            items.append(dict(path=name, startLine=1, endLine=len(content.splitlines()),
                              sourceHash=hashlib.sha256(path.read_bytes()).hexdigest(), reason='Exact source'))
        verified, excerpts = evidence_for(root, items)
        assert len(verified) == 2
        assert 'intervening code omitted' in excerpts[0]
        assert 'return result;' in excerpts[0]
        assert 'result = 42' in excerpts[1]
        assert sum(len(text) for text in excerpts) < 14500
        for question, limit in [('Explain the workflow', 700), ('Where is result?', 350)]:
            with patch.dict(os.environ, {'CODEMIND_LOCAL_MODEL': 'test-local'}), patch('answers.urlopen', return_value=model_reply('Explanation.')) as model:
                reply = answer_question(root, question, {'results': items})
            assert model.call_count == 1
            body = json.loads(model.call_args.args[0].data)
            assert body['options']['num_ctx'] == 8192
            assert body['options']['num_predict'] == limit
            assert len(reply['citations']) == 2


def test_exact_definition_chunks_supply_the_verified_continuous_function():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        path = root / 'flow.py'
        path.write_text('\n'.join(f'line_{number}' for number in range(1, 101)) + '\n')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        base = dict(path='flow.py', sourceHash=digest, reason='Exact source definition; not proof of runtime behavior',
                    name='flow', definitionKey='python-context:flow.py|flow')
        verified, evidence = evidence_for(root, [{**base, 'startLine': 1, 'endLine': 10},
                                                 {**base, 'startLine': 91, 'endLine': 100}])
        assert len(verified) == 2
        assert len(evidence) == 1
        assert 'line_50' in evidence[0]
        assert 'line_100' in evidence[0]


def test_workflow_expansion_is_bounded_and_location_queries_skip_it():
    retriever = object.__new__(HybridRetriever)
    retriever._verify = lambda item: True
    retriever._graph = Mock(return_value=([], 'not_found', []))
    seeds = [dict(path=f'{n}.ts', startLine=1, endLine=2, sourceHash='hash',
                  entityId=str(n), symbolId=str(n), name=f'function{n}') for n in range(12)]
    rankings = {'semantic': seeds}
    retriever._expand(plan_query('How does the request reach the answer?'), rankings, {}, 15)
    assert retriever._graph.call_count == 3
    retriever._graph.reset_mock()
    retriever._expand(plan_query('Where is authentication checked?'), rankings, {}, 15)
    retriever._graph.assert_not_called()
