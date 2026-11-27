"""P4 static planning fixtures: no repository programs or test commands run."""
from dataclasses import replace
from unittest.mock import patch
import json
import pytest

from knowledge_base.test_change_capture import repo, git
from knowledge_base.test_change_evidence import synthetic_atomic
from change_analysis.evidence import build_change_evidence
from change_analysis.impact import analyze_impact
from change_analysis.service import ChangeAnalysis
from test_analysis.planner import TestPlanner as Planner
from test_analysis.contracts import TestPlan as Plan


def prepare(repo, test_source=None, manifest=None):
    (repo / 'a.js').write_text('export function value() { return 1; }\n')
    (repo / 'value.test.js').write_text(test_source or "import test from 'node:test';\nimport assert from 'node:assert/strict';\nimport { value } from './a.js';\ntest('value contract', () => { assert.equal(value(), 1); });\n")
    (repo / 'package.json').write_text(json.dumps(manifest or {'type': 'module', 'engines': {'node': '>=22'}, 'scripts': {'test': 'touch NEVER_EXECUTE'}}))
    (repo / 'package-lock.json').write_text('{"lockfileVersion": 3}\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'fixture baseline')
    (repo / 'a.js').write_text('export function value() { return 2; }\n')
    capture = synthetic_atomic(repo)
    evidence = build_change_evidence(capture)
    return capture, ChangeAnalysis(capture.id, evidence, analyze_impact(evidence, capture))


def test_plan_uses_only_capture_and_records_expectations(repo):
    capture, analysis = prepare(repo)
    with patch('subprocess.run', side_effect=AssertionError('Planner may not spawn')), patch('pathlib.Path.open', side_effect=AssertionError('Planner may not read disk')):
        plan = Planner().build(capture, analysis)
    assert plan.execution_available is False
    assert any(s.evidence_basis == 'resolved_reference' for s in plan.scenarios)
    assertion = next(s for s in plan.scenarios if s.expectation_source)
    assert assertion.expected_outcome.endswith('assert.equal(value(), 1)')
    assert all(s.expected_outcome is None and s.unknowns for s in plan.scenarios if s.category != 'existing_test')
    profile = next(p for p in plan.profiles if p.package_root == '.')
    assert profile.framework == 'node:test' and profile.runtime_requirement == '>=22'
    assert profile.dependency_state == 'lockfile_captured'
    assert all('NEVER_EXECUTE' not in ' '.join(c.argv or []) for c in plan.proposed_checks)
    assert any(c.argv == ['node', '--test', '--', 'value.test.js'] for c in plan.proposed_checks)
    Plan.model_validate(plan.model_dump())


def test_misleading_filename_and_no_assertion_do_not_prove_coverage(repo):
    capture, analysis = prepare(repo, "// looks like a test but has no runner binding\nconst test = 'fake';\n")
    plan = Planner().build(capture, analysis)
    assert not any(t.framework == 'node:test' for t in plan.tests)
    assert all(s.expectation_source is None for s in plan.scenarios)
    assert any('No matching test found within' in s for s in plan.no_matching_test)
    assert not any(c.argv for c in plan.proposed_checks)


def test_assertionless_node_test_keeps_expectation_unknown(repo):
    capture, analysis = prepare(repo, "import test from 'node:test'; import {value} from './a.js';\ntest('exercise', () => { value(); });\n")
    plan = Planner().build(capture, analysis)
    existing = next(s for s in plan.scenarios if s.category == 'existing_test')
    assert existing.expectation_source is None and existing.eligibility == 'needs_expectation'


def test_incremental_catalog_and_plan_cache_invalidation(repo):
    capture, analysis = prepare(repo)
    planner = Planner()
    first = planner.build(capture, analysis)
    parses = planner.catalog.parse_count
    first.selected_check_ids = ['tampered']
    second = planner.build(capture, analysis)
    assert second.selected_check_ids == [] and planner.build_count == 1
    assert planner.catalog.parse_count == parses
    (repo / 'package.json').write_text('{"engines":{"node":">=24"},"type":"module"}')
    next_capture = synthetic_atomic(repo)
    next_analysis = ChangeAnalysis(next_capture.id, analysis.evidence, analysis.impact)
    next_evidence = build_change_evidence(next_capture)
    next_analysis = ChangeAnalysis(next_capture.id, next_evidence, analyze_impact(next_evidence, next_capture))
    third = planner.build(next_capture, next_analysis)
    assert third.plan_id != second.plan_id and third.catalog_id != second.catalog_id
    assert third.profiles[0].profile_id != second.profiles[0].profile_id
    assert planner.catalog.parse_count == parses
    (repo / 'value.test.js').write_text("import test from 'node:test'; test('changed', () => {});\n")
    changed = synthetic_atomic(repo)
    e = build_change_evidence(changed)
    planner.build(changed, ChangeAnalysis(changed.id, e, analyze_impact(e, changed)))
    assert planner.catalog.parse_count > parses


def test_configuration_is_metadata_never_executed(repo):
    (repo / 'vitest.config.js').write_text("throw new Error('MUST_NOT_EXECUTE');\n")
    capture, analysis = prepare(repo, "import {test} from 'vitest'; test('fake', () => {});\n", {'devDependencies': {'vitest': '1.0'}})
    plan = Planner().build(capture, analysis)
    assert plan.profiles[0].framework == 'unsupported'
    assert any(ref.path == 'vitest.config.js' for ref in plan.profiles[0].configuration)
    assert all(c.argv is None for c in plan.proposed_checks)
    assert any('not evaluated' in item for item in plan.profiles[0].limitations)


def test_shadowed_assert_or_runner_is_not_qualified(repo):
    capture, analysis = prepare(repo, "import test from 'node:test'; import assert from 'node:assert';\nfunction fake(test) { test('wrong', () => { assert.equal(1, 2); }); }\n")
    plan = Planner().build(capture, analysis)
    assert all(t.framework != 'node:test' for t in plan.tests)


def test_metadata_is_in_capture_digest_and_handoff(repo):
    capture, _ = prepare(repo)
    assert any(v.path == 'package.json' and v.data for v in capture.after_metadata)
    from change_analysis.transport import encode, decode
    decoded = decode(json.loads(json.dumps(encode(capture))), str(repo))
    assert decoded == capture
    (repo / 'package-lock.json').write_text('{"lockfileVersion": 2}\n')
    assert synthetic_atomic(repo).digest != capture.digest


def test_plan_ownership_and_unknown_checks_are_rejected(repo):
    capture, analysis = prepare(repo)
    with pytest.raises(ValueError, match='another capture'):
        Planner().build(replace(capture, id='different'), analysis)
    plan = Planner().build(capture, analysis)
    data = plan.model_dump()
    data['selected_check_ids'] = ['0' * 64]
    with pytest.raises(ValueError):
        Plan.model_validate(data)


def test_commonjs_node_adapter_and_missing_fixture_unknowns(repo):
    capture, analysis = prepare(repo, "const test = require('node:test');\nconst assert = require('node:assert/strict');\nconst {value} = require('./a.js');\ntest('fixture case', () => { assert.equal(value(process.env.REQUIRED_FIXTURE), 1); });\n")
    plan = Planner().build(capture, analysis)
    assert any(test.framework == 'node:test' and test.assertions for test in plan.tests)
    scenario = next(s for s in plan.scenarios if s.category == 'existing_test')
    assert any('fixtures' in text for text in scenario.unknowns)
    assert all(check.execution == 'not_run' for check in plan.proposed_checks)


def test_unavailable_lockfile_is_unknown_and_symlink_metadata_not_read(repo, tmp_path):
    capture, analysis = prepare(repo)
    (repo / 'package-lock.json').unlink()
    external = tmp_path / 'external-lock'
    external.write_text('outside')
    (repo / 'package-lock.json').symlink_to(external)
    capture = synthetic_atomic(repo)
    evidence = build_change_evidence(capture)
    analysis = ChangeAnalysis(capture.id, evidence, analyze_impact(evidence, capture))
    plan = Planner().build(capture, analysis)
    assert plan.profiles[0].dependency_state == 'unknown'
    assert any('metadata unavailable' in item for item in plan.limitations)


def test_metadata_excludes_secrets_and_staging_uses_saved_bytes(repo):
    (repo / '.env').write_text('secret')
    (repo / 'unrelated.json').write_text('{"secret":"private"}')
    (repo / 'package.json').write_text('{"engines":{"node":">=20"}}')
    git(repo, 'add', 'package.json')
    (repo / 'package.json').write_text('{"engines":{"node":">=22"}}')
    capture = synthetic_atomic(repo)
    assert {v.path for v in capture.after_metadata} == {'package.json'}
    assert capture.after_metadata[0].data == b'{"engines":{"node":">=22"}}'


def test_binary_exclusions_do_not_break_private_capture_handoff(repo):
    (repo / 'binary.js').write_bytes(b'\x00binary source excluded')
    (repo / 'package-lock.json').write_bytes(b'\x00binary metadata excluded')
    capture = synthetic_atomic(repo)
    from change_analysis.transport import encode, decode
    restored = decode(json.loads(json.dumps(encode(capture))), str(repo))
    assert restored == capture
    assert restored.size_bytes == sum(len(v.data or b'') for v in (*restored.before, *restored.after, *restored.before_metadata, *restored.after_metadata))


def test_single_letter_nested_package_cannot_inherit_root_execution_profile(repo):
    (repo / 'x').mkdir()
    (repo / 'x/package.json').write_text('{"type":"module"}')
    (repo / 'x/nested.test.js').write_text("import test from 'node:test'; test('nested', () => {});\n")
    capture, analysis = prepare(repo)
    plan = Planner().build(capture, analysis)
    test = next(t for t in plan.tests if t.source.path == 'x/nested.test.js')
    profile = next(p for p in plan.profiles if p.profile_id == test.profile_id)
    assert profile.package_root == 'x' and profile.command is None


def test_unknown_node_test_members_and_assertion_chains_stay_unsupported(repo):
    capture, analysis = prepare(repo, "import test from 'node:test'; import assert from 'node:assert/strict'; import {value} from './a.js';\ntest.notARealAPI('fake', () => { assert.equal(value(), 1); });\ntest('real', () => { assert.notARealAPI.equal(value(), 1); });\n")
    plan = Planner().build(capture, analysis)
    assert not any(t.name == 'fake' and t.enabled for t in plan.tests)
    real = next(t for t in plan.tests if t.name == 'real')
    assert not real.assertions
