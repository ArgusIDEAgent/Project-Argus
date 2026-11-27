"""P2 source/version integrity; synthetic atomic marker is only a unit-test input."""
from dataclasses import replace
from unittest.mock import patch

import pytest

from change_analysis import CaptureError, CaptureStore
from change_analysis.evidence import build_change_evidence, validate_against_capture, ChangeEvidencePackage
from knowledge_base.test_change_capture import repo, git


def synthetic_atomic(repo):
    with CaptureStore(repo) as store:
        return replace(store.capture(), consistency='atomic_apfs', atomic_snapshot_id='fixture-snapshot')


@pytest.fixture
def changed(repo):
    (repo / 'a.ts').write_text('export function answer() { return 1; }\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'function baseline')
    (repo / 'a.ts').write_text('export function answer() { return 2; }\n')
    (repo / 'b.ts').write_text("import { answer } from './a';\nexport function caller() { return answer(); }\n")
    return synthetic_atomic(repo)


def test_two_versions_and_no_live_source_or_model_access(changed):
    import subprocess
    native_run = subprocess.run
    def checked_run(args, **kwargs):
        assert args[0] == 'node' and args[1].endswith('semantic_resolver.cjs')
        assert 'sources' in kwargs['input']
        return native_run(args, **kwargs)
    with patch('pathlib.Path.open', side_effect=AssertionError('No live source read')), patch('subprocess.run', side_effect=checked_run):
        package = build_change_evidence(changed)
        validate_against_capture(package, changed)
    assert package.before.package.snapshot_id != package.after.package.snapshot_id
    assert package.before.manifest['a.ts'] != package.after.manifest['a.ts']
    assert package.before.symbols[0].changed
    assert package.correspondence[0].state == 'matched'
    assert not package.after.package.findings  # structural evidence is not a behavioral claim
    assert any(e.relationship == 'CALLS' for e in package.after.relationships)


def test_observed_capture_and_conflicts_are_blocked(changed):
    with pytest.raises(CaptureError) as error:
        build_change_evidence(replace(changed, consistency='observed_stable'))
    assert error.value.code == 'atomic_capture_required'
    with pytest.raises(CaptureError) as error:
        build_change_evidence(replace(changed, conflicts=('a.ts',), definitive=False))
    assert error.value.code == 'merge_conflicts'


@pytest.mark.parametrize('mutation', ['repository', 'version', 'hash', 'citation', 'mapping', 'provenance'])
def test_invalid_ownership_is_rejected(changed, mutation):
    package = build_change_evidence(changed)
    data = package.model_dump()
    record = next(iter(data['after']['package']['evidence'].values()))
    if mutation == 'repository':
        data['repository_id'] = '0' * 24
    elif mutation == 'version':
        data['after'] = data['before']
    elif mutation == 'hash':
        data['after']['manifest']['a.ts'] = '0' * 64
    elif mutation == 'citation':
        citation = next(iter(data['after']['package']['citations'].values()))
        citation['record_id'] = 'missing'
    elif mutation == 'mapping':
        data['correspondence'][0]['after_candidates'] = ['missing']
    else:
        record['provenance']['capture_id'] = 'different-capture'
    with pytest.raises(ValueError):
        ChangeEvidencePackage.model_validate(data)


def test_deleted_code_retains_baseline_citations(repo):
    (repo / 'a.ts').write_text('export function removed() { return 1; }\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline')
    (repo / 'a.ts').unlink()
    capture = synthetic_atomic(repo)
    package = build_change_evidence(capture)
    assert package.before.package.citations
    assert package.after.manifest == {}
    assert package.correspondence[0].state == 'unmatched'
    validate_against_capture(package, capture)


def test_budget_is_deterministic_and_omission_counted(changed):
    first = build_change_evidence(changed, max_records_per_version=1)
    second = build_change_evidence(changed, max_records_per_version=1)
    assert first == second
    assert first.before.omitted_records == 1
    assert first.after.omitted_records == 3
    assert len(first.after.package.evidence) == 1
    assert any('omitted 3' in s for s in first.after.limitations)


def test_capture_hash_mismatch_rejected(changed):
    altered = replace(changed.after[0], data=b'tampered')
    with pytest.raises(ValueError, match='digest'):
        build_change_evidence(replace(changed, after=(altered, *changed.after[1:])))


def test_another_capture_cannot_own_package(changed):
    package = build_change_evidence(changed)
    with pytest.raises(ValueError, match='different capture'):
        validate_against_capture(package, replace(changed, id='another'))


def test_duplicate_named_declarations_remain_ambiguous(repo):
    (repo / 'a.ts').write_text('function same() {}\nfunction same() {}\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'duplicates')
    package = build_change_evidence(synthetic_atomic(repo))
    assert all(m.state == 'ambiguous' for m in package.correspondence)


def test_exact_file_rename_maps_declarations(repo):
    (repo / 'a.ts').write_text('export function stable() { return 1; }\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline')
    (repo / 'a.ts').rename(repo / 'renamed.ts')
    package = build_change_evidence(synthetic_atomic(repo))
    assert package.correspondence[0].state == 'matched'
    assert package.correspondence[0].basis == 'exact_file_rename'


def test_coherently_tampered_syntax_proof_cannot_claim_resolution(changed):
    package = build_change_evidence(changed)
    symbol = package.after.symbols[0]
    record = package.after.package.evidence[symbol.record_id]
    record.proof.resolution = 'resolved'
    for citation in package.after.package.citations.values():
        if citation.record_id == symbol.record_id:
            citation.source.resolution = 'resolved'
    with pytest.raises(ValueError, match='canonical'):
        validate_against_capture(package, changed)


def test_resolved_calls_imports_and_changed_relationships(changed):
    package = build_change_evidence(changed)
    edges = package.after.relationships
    assert any(e.relationship == 'IMPORTS' for e in edges)
    assert any(e.relationship == 'CALLS' for e in edges)
    assert all(e.record_id in package.after.package.evidence for e in edges)
    assert not any(e.relationship in ('CALLS', 'IMPORTS') for e in package.before.relationships)
    validate_against_capture(package, changed)


def test_ambiguous_dynamic_call_remains_unresolved(repo):
    (repo / 'a.ts').write_text('export function dynamic(value: any) { return value.run(); }\n')
    capture = synthetic_atomic(repo)
    package = build_change_evidence(capture)
    assert not any(e.relationship == 'CALLS' for e in package.after.relationships)
    assert any('CALLS' in item for item in package.after.unresolved)


def test_relationship_endpoint_and_proof_tampering_rejected(changed):
    package = build_change_evidence(changed)
    data = package.model_dump()
    edge = next(e for e in data['after']['relationships'] if e['relationship'] == 'CALLS')
    edge['target_id'] = 'nonexistent'
    with pytest.raises(ValueError):
        ChangeEvidencePackage.model_validate(data)
    package = build_change_evidence(changed)
    edge = next(e for e in package.after.relationships if e.relationship == 'CALLS')
    edge.target_id = next(s.symbol_id for s in package.after.symbols if s.symbol_id != edge.target_id)
    with pytest.raises(ValueError):
        validate_against_capture(package, changed)


def test_captured_semantics_ignore_later_live_edits(changed, repo):
    original = build_change_evidence(changed)
    (repo / 'a.ts').write_text('export function different() { return 0; }\n')
    (repo / 'b.ts').write_text('export function unrelated() { return 9; }\n')
    later = build_change_evidence(changed)
    assert original == later
    validate_against_capture(later, changed)


def test_edited_file_rename_with_signature_change(repo):
    (repo / 'a.ts').write_text('export function keep(value: string) { return value; }\nexport function changed() { return 1; }\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline')
    (repo / 'a.ts').rename(repo / 'renamed.ts')
    (repo / 'renamed.ts').write_text('export function keep(value: string) { return value; }\nexport function changed() { return 2; }\n')
    capture = synthetic_atomic(repo)
    package = build_change_evidence(capture)
    stable = next(m for m in package.correspondence if m.basis == 'structural_content')
    assert stable.state == 'matched'
    validate_against_capture(package, capture)


def test_changed_symbol_metadata_and_correspondence_cannot_be_forged(changed):
    package = build_change_evidence(changed)
    package.after.symbols[0].changed = not package.after.symbols[0].changed
    with pytest.raises(ValueError, match='metadata'):
        validate_against_capture(package, changed)
    package = build_change_evidence(changed)
    package.correspondence[0].state = 'unmatched'
    package.correspondence[0].after_candidates = []
    package.correspondence[0].basis = 'unresolved'
    with pytest.raises(ValueError, match='Correspondence'):
        validate_against_capture(package, changed)


def test_phase2_owner_and_historical_lifetime(changed):
    from change_analysis.service import analyze_change
    from unittest.mock import Mock
    store = Mock()
    store.get.return_value = changed
    result = analyze_change(store, changed.id)
    store.get.assert_called_once_with(changed.id)
    assert result.freshness == 'historical_capture'
    assert result.framework_adapters == ()
    assert result.impact.capture_id == changed.id


def test_framework_adapter_request_is_explicitly_unsupported(changed):
    from change_analysis.service import analyze_change
    from unittest.mock import Mock
    store = Mock()
    with pytest.raises(ValueError, match='No framework'):
        analyze_change(store, changed.id, framework_adapters=('express',))
    store.get.assert_not_called()


def test_signature_edit_matches_by_declaration_key(repo):
    (repo / 'a.ts').write_text('export function keep(value: string) { return value; }\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline')
    (repo / 'a.ts').write_text('export function keep(value: number, optional?: boolean) { return value; }\n')
    capture = synthetic_atomic(repo)
    package = build_change_evidence(capture)
    assert package.correspondence[0].state == 'matched'
    assert package.correspondence[0].basis == 'declaration_key'
    validate_against_capture(package, capture)


def test_dynamic_import_boundary_is_visible(repo):
    (repo / 'a.ts').write_text('export async function load(path: string) { return import(path); }\n')
    package = build_change_evidence(synthetic_atomic(repo))
    assert any('dynamic_import_path' in item for item in package.after.unresolved)


def test_changed_file_precedes_unchanged_files_under_record_budget(repo):
    (repo / 'a.ts').write_text('export function unchanged() {}\n')
    (repo / 'z.ts').write_text('export function late() { return 1; }\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline')
    (repo / 'z.ts').write_text('export function late() { return 2; }\n')
    package = build_change_evidence(synthetic_atomic(repo), max_records_per_version=1)
    assert next(iter(package.after.package.evidence.values())).proof.file_path == 'z.ts'
