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
    with patch('pathlib.Path.open', side_effect=AssertionError('No live source read')), patch('subprocess.run', side_effect=AssertionError('No model/process')):
        package = build_change_evidence(changed)
        validate_against_capture(package, changed)
    assert package.before.package.snapshot_id != package.after.package.snapshot_id
    assert package.before.manifest['a.ts'] != package.after.manifest['a.ts']
    assert package.before.symbols[0].changed
    assert package.correspondence[0].state == 'matched'
    assert not package.after.package.findings  # syntax references do not become resolved calls


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
