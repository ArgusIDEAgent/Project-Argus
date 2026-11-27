from pathlib import Path
import pytest
from knowledge_base.test_change_evidence import repo, git, synthetic_atomic
from change_analysis.evidence import build_change_evidence
from change_analysis.impact import analyze_impact


def test_added_call_and_bounded_caller_path(repo):
    (repo / 'a.ts').write_text('export function leaf() { return 1; }\nexport function middle() { return leaf(); }\nexport function top() { return middle(); }\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline')
    (repo / 'a.ts').write_text('export function leaf() { return 2; }\nexport function middle() { return leaf(); }\nexport function top() { return middle(); }\n')
    capture = synthetic_atomic(repo)
    package = build_change_evidence(capture)
    result = analyze_impact(package, capture)
    assert not any(c.status == 'added' and c.relationship == 'CALLS' for c in result.relationship_changes)
    assert any(len(p.caller_ids) == 2 for p in result.caller_paths)
    limited = analyze_impact(package, capture, max_depth=1, max_paths=1)
    assert not any(len(p.caller_ids) > 1 for p in limited.caller_paths)
    assert limited.omitted_caller_paths >= 0


def test_relationship_addition_and_removal(repo):
    (repo / 'a.ts').write_text('function first() {}\nfunction second() {}\nfunction use() { first(); }\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline')
    (repo / 'a.ts').write_text('function first() {}\nfunction second() {}\nfunction use() { second(); }\n')
    capture = synthetic_atomic(repo)
    result = analyze_impact(build_change_evidence(capture), capture)
    assert {(c.status, c.relationship) for c in result.relationship_changes} >= {('added', 'CALLS'), ('removed', 'CALLS')}
    assert all(c.proof_record_id for c in result.relationship_changes)


def test_unmatched_rename_is_not_a_verified_relationship_change(repo):
    (repo / 'a.ts').write_text('function oldName() {}\nfunction use() { oldName(); }\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline')
    (repo / 'a.ts').write_text('function newName() { return 42; }\nfunction use() { newName(); }\n')
    capture = synthetic_atomic(repo)
    result = analyze_impact(build_change_evidence(capture), capture)
    assert result.uncertain_relationships == 2
    assert not any(c.relationship == 'CALLS' for c in result.relationship_changes)


def test_impact_budgets_and_capture_ownership(repo):
    (repo / 'a.ts').write_text('function target() {}\nfunction use() { target(); target(); target(); }\n')
    capture = synthetic_atomic(repo)
    package = build_change_evidence(capture)
    result = analyze_impact(package, capture, max_relationships=1)
    assert len(result.relationship_changes) <= 1
    with pytest.raises(ValueError):
        analyze_impact(package, capture, max_depth=5)
    from dataclasses import replace
    with pytest.raises(ValueError):
        analyze_impact(package, replace(capture, id='wrong'))


def test_incomplete_evidence_never_establishes_edge_removal(repo):
    (repo / 'a.ts').write_text('function first() {}\nfunction second() {}\nfunction use() { first(); }\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'baseline')
    (repo / 'a.ts').write_text('function first() {}\nfunction second() {}\nfunction use() { second(); }\n')
    capture = synthetic_atomic(repo)
    package = build_change_evidence(capture, max_records_per_version=4)
    result = analyze_impact(package, capture)
    assert package.before.omitted_records or package.before.omitted_relationships
    assert result.relationship_changes == []


def test_cycles_and_depth_omissions_are_explicit(repo):
    (repo / 'a.ts').write_text('function one() { two(); }\nfunction two() { three(); }\nfunction three() { one(); }\n')
    capture = synthetic_atomic(repo)
    package = build_change_evidence(capture)
    result = analyze_impact(package, capture, max_depth=1)
    assert result.depth_limited_frontiers > 0
    assert all(len(p.caller_ids) == 1 and p.origin_id not in p.caller_ids for p in result.caller_paths)
