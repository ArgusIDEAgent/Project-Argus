"""P1 qualification: real disposable Git repositories, no models or index builds."""
from dataclasses import FrozenInstanceError
import subprocess
from unittest.mock import patch

import pytest

from change_analysis import CaptureError, CaptureLimits, CaptureStore


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True)
    return result.stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-q')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    git(root, 'config', 'user.name', 'Fixture')
    (root / 'a.ts').write_text('export const answer = 1;\n')
    git(root, 'add', '.')
    git(root, 'commit', '-qm', 'baseline')
    return root


def capture(repo, **limits):
    with CaptureStore(repo, CaptureLimits(**limits)) as store:
        return store.capture()


def versions(snapshot, side='after'):
    return {v.path: v for v in getattr(snapshot, side)}


def test_final_saved_bytes_and_index_unchanged(repo):
    (repo / 'a.ts').write_text('export const answer = 2;\n')
    git(repo, 'add', 'a.ts')
    (repo / 'a.ts').write_text('export const answer = 3;\n')
    index = (repo / '.git/index').read_bytes()
    source = (repo / 'a.ts').read_bytes()
    result = capture(repo)
    assert versions(result)['a.ts'].data == source
    assert versions(result, 'before')['a.ts'].data == b'export const answer = 1;\n'
    assert result.changes[0].ranges == ((1, 2, 1, 2),)
    assert result.staging[0][2] == git(repo, 'rev-parse', ':a.ts').strip().decode()
    assert (repo / '.git/index').read_bytes() == index
    assert (repo / 'a.ts').read_bytes() == source
    assert any('unsaved' in n for n in result.notices)
    assert not (repo / '.codemind').exists()


def test_staged_change_reverted_in_worktree_is_not_a_saved_change(repo):
    original = (repo / 'a.ts').read_bytes()
    (repo / 'a.ts').write_text('staged\n')
    git(repo, 'add', 'a.ts')
    (repo / 'a.ts').write_bytes(original)
    assert capture(repo).changes == ()


def test_untracked_delete_and_exact_rename_candidates(repo):
    git(repo, 'mv', 'a.ts', 'renamed.ts')
    (repo / 'new.ts').write_text('export const added = true;\n')
    result = capture(repo)
    changes = {c.path: c for c in result.changes}
    assert changes['a.ts'].status == 'deleted'
    assert changes['renamed.ts'].rename_candidates == ('a.ts',)
    assert changes['new.ts'].status == 'added'


def test_ambiguous_rename_preserves_candidates(repo):
    (repo / 'b.ts').write_bytes((repo / 'a.ts').read_bytes())
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'duplicate')
    (repo / 'b.ts').unlink()
    (repo / 'a.ts').rename(repo / 'c.ts')
    assert {c.path: c for c in capture(repo).changes}['c.ts'].rename_candidates == ('a.ts', 'b.ts')


def test_unborn_and_non_git(tmp_path):
    with pytest.raises(CaptureError):
        CaptureStore(tmp_path)
    git(tmp_path, 'init', '-q')
    (tmp_path / 'new.js').write_text('const x = 1;\n')
    result = capture(tmp_path)
    assert result.head is None and result.mode == 'all_files_added'
    assert result.changes[0].status == 'added'


def test_nested_selected_folder_does_not_widen(repo):
    folder = repo / 'selected'
    folder.mkdir()
    (folder / 'inside.ts').write_text('old\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'nested')
    (folder / 'inside.ts').write_text('new\n')
    (repo / 'outside.ts').write_text('private\n')
    result = capture(folder)
    assert set(versions(result)) == {'inside.ts'}
    assert set(versions(result, 'before')) == {'inside.ts'}
    assert result.root == str(folder.resolve())
    assert result.git_root == str(repo.resolve())


def test_exclusions_and_tracked_ignored_source(repo, tmp_path):
    (repo / '.gitignore').write_text('ignored/\na.ts\n')
    (repo / 'ignored').mkdir()
    (repo / 'ignored/hidden.ts').write_text('ignored')
    (repo / 'binary.ts').write_bytes(b'\0binary')
    (repo / 'large.ts').write_bytes(b'a' * 101)
    (repo / 'encoding.js').write_bytes(b'\xff')
    (repo / '.env').write_text('SECRET=secret')
    (repo / 'notes.txt').write_text('notes')
    outside = tmp_path / 'outside.ts'
    outside.write_text('must not read')
    (repo / 'link.ts').symlink_to(outside)
    result = capture(repo, max_file_bytes=100)
    after = versions(result)
    assert after['a.ts'].data is not None  # tracked files stay eligible despite ignore rules
    for name, reason in [('binary.ts', 'binary'), ('large.ts', 'oversized'),
                         ('encoding.js', 'non_utf8'), ('.env', 'sensitive_filename'),
                         ('notes.txt', 'unsupported_file_type'), ('ignored', 'ignored')]:
        assert after[name].exclusion == reason
        assert after[name].data is None
        assert {c.path: c for c in result.changes}[name].status == 'unanalyzed'
    assert after['link.ts'].exclusion == 'symlink_or_non_directory'


def test_parent_symlink_cannot_expose_outside_source(repo, tmp_path):
    (repo / 'dir').mkdir()
    (repo / 'dir/child.ts').write_text('baseline')
    git(repo, 'add', '.')
    git(repo, 'commit', '-qm', 'directory')
    (repo / 'dir/child.ts').unlink()
    (repo / 'dir').rmdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'child.ts').write_text('secret')
    (repo / 'dir').symlink_to(outside, target_is_directory=True)
    assert versions(capture(repo))['dir/child.ts'].data is None
    with pytest.raises(CaptureError, match='symlink'):
        CaptureStore(repo / 'dir')


def test_submodule_reference_is_reported_without_recursion(repo):
    oid = git(repo, 'rev-parse', 'HEAD').strip().decode()
    git(repo, 'update-index', '--add', '--cacheinfo', f'160000,{oid},module')
    git(repo, 'commit', '-qm', 'gitlink')
    (repo / 'module').mkdir()
    (repo / 'module/hidden.ts').write_text('do not recurse')
    result = capture(repo)
    assert versions(result, 'before')['module'].exclusion == 'submodule'
    assert versions(result)['module'].exclusion == 'submodule'
    assert 'module/hidden.ts' not in versions(result)


def test_conflicts_block_definitive_analysis(repo):
    branch = git(repo, 'branch', '--show-current').strip().decode()
    git(repo, 'checkout', '-qb', 'other')
    (repo / 'a.ts').write_text('other\n')
    git(repo, 'commit', '-qam', 'other')
    git(repo, 'checkout', '-q', branch)
    (repo / 'a.ts').write_text('main\n')
    git(repo, 'commit', '-qam', 'main')
    subprocess.run(['git', '-C', str(repo), 'merge', 'other'], capture_output=True)
    result = capture(repo)
    assert result.conflicts == ('a.ts',)
    assert not result.definitive


def test_saved_race_retries_then_returns_matching_bytes(repo):
    with CaptureStore(repo) as store:
        native = store._observe
        calls = 0

        def changing():
            nonlocal calls
            result = native()
            calls += 1
            if calls == 1:
                (repo / 'a.ts').write_text('new saved content\n')
            return result

        with patch.object(store, '_observe', side_effect=changing):
            result = store.capture()
        assert calls == 5
        assert versions(result)['a.ts'].data == b'new saved content\n'


def test_continuous_edits_never_publish(repo):
    with CaptureStore(repo, CaptureLimits(attempts=2)) as store:
        native = store._observe
        calls = 0

        def changing():
            nonlocal calls
            result = native()
            calls += 1
            (repo / 'a.ts').write_text(f'change {calls}\n')
            return result

        with patch.object(store, '_observe', side_effect=changing), pytest.raises(CaptureError) as error:
            store.capture()
        assert error.value.code == 'source_changed'
        assert calls == 4 and not store._captures


def test_head_movement_retries(repo):
    with CaptureStore(repo) as store:
        native = store._observe
        moved = False

        def changing():
            nonlocal moved
            result = native()
            if not moved:
                moved = True
                (repo / 'a.ts').write_text('new commit\n')
                git(repo, 'commit', '-qam', 'move head')
            return result

        with patch.object(store, '_observe', side_effect=changing):
            result = store.capture()
        assert result.head == git(repo, 'rev-parse', 'HEAD').strip().decode()
        assert result.changes == ()


def test_hostile_helpers_and_git_environment_not_executed(repo, monkeypatch):
    marker = repo / 'executed'
    helper = repo / 'helper.sh'
    helper.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
    helper.chmod(0o700)
    git(repo, 'config', 'core.fsmonitor', str(helper))
    git(repo, 'config', 'diff.evil.textconv', str(helper))
    git(repo, 'config', 'filter.evil.clean', str(helper))
    (repo / '.gitattributes').write_text('*.ts diff=evil filter=evil\n')
    monkeypatch.setenv('GIT_EXTERNAL_DIFF', str(helper))
    monkeypatch.setenv('GIT_DIR', '/nonexistent')
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'core.fsmonitor')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', str(helper))
    assert capture(repo).head
    assert not marker.exists()


def test_limits_lifetime_and_immutable_digest(repo):
    with pytest.raises(CaptureError) as error:
        capture(repo, max_total_bytes=1)
    assert error.value.code == 'size_limit'
    (repo / 'b.ts').write_text('new')
    with pytest.raises(CaptureError) as error:
        capture(repo, max_files=1)
    assert error.value.code == 'file_limit'
    with CaptureStore(repo, CaptureLimits(max_captures=1)) as store:
        first = store.capture()
        with pytest.raises(FrozenInstanceError):
            first.head = 'bad'
        with pytest.raises(CaptureError) as error:
            store.capture()
        assert error.value.code == 'retention_limit'
        store.release(first.id)
        second = store.capture()
        assert first.id != second.id and first.digest == second.digest
        (repo / 'b.ts').write_text('different')
        store.release(second.id)
        assert store.capture().digest != second.digest
    with pytest.raises(KeyError):
        store.get(first.id)
    with pytest.raises(CaptureError):
        store.capture()


def test_unusual_filename_and_diff_budget(repo):
    name = 'tab\tnewline\n.ts'
    (repo / name).write_text('one\ntwo\nthree\n')
    result = capture(repo, max_diff_lines=2)
    assert versions(result)[name].data == b'one\ntwo\nthree\n'
    assert {c.path: c for c in result.changes}[name].limitation == 'diff_line_budget'


def test_worktree_supported(repo, tmp_path):
    folder = tmp_path / 'worktree'
    git(repo, 'worktree', 'add', '-qb', 'worktree', str(folder))
    (folder / 'a.ts').write_text('worktree edit\n')
    assert versions(capture(folder))['a.ts'].data == b'worktree edit\n'


def test_baseline_tracked_file_remains_eligible_after_cached_removal(repo):
    (repo / '.gitignore').write_text('a.ts\n')
    git(repo, 'rm', '--cached', 'a.ts')
    (repo / 'a.ts').write_text('saved after index removal\n')
    assert versions(capture(repo))['a.ts'].data == b'saved after index removal\n'


def test_git_output_budget_fails_without_publication(repo):
    with CaptureStore(repo, CaptureLimits(max_git_output_bytes=300)) as store:
        for i in range(30):
            (repo / f'long_name_{i}.ts').write_text('source')
        with pytest.raises(CaptureError) as error:
            store.capture()
        assert error.value.code == 'git_output_limit'
        assert not store._captures


def test_concurrent_capture_respects_session_capacity(repo):
    from concurrent.futures import ThreadPoolExecutor
    with CaptureStore(repo, CaptureLimits(max_captures=1)) as store:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(store.capture) for _ in range(2)]
            outcomes = []
            for future in results:
                try:
                    outcomes.append(future.result())
                except CaptureError as error:
                    outcomes.append(error.code)
        assert outcomes.count('retention_limit') == 1
        assert len(store._captures) == 1


@pytest.mark.parametrize('mutation', ['index', 'addition', 'deletion'])
def test_inventory_and_index_races_retry(repo, mutation):
    (repo / 'a.ts').write_text('saved edit\n')
    with CaptureStore(repo) as store:
        native = store._observe
        calls = 0

        def changing():
            nonlocal calls
            result = native()
            calls += 1
            if calls == 1:
                if mutation == 'index':
                    git(repo, 'add', 'a.ts')
                elif mutation == 'addition':
                    (repo / 'b.ts').write_text('added\n')
                else:
                    (repo / 'a.ts').unlink()
            return result

        with patch.object(store, '_observe', side_effect=changing):
            result = store.capture()
        assert calls == 5
        if mutation == 'index':
            assert result.staging[0][2] == git(repo, 'rev-parse', ':a.ts').strip().decode()
        elif mutation == 'addition':
            assert 'b.ts' in versions(result)
        else:
            assert 'a.ts' not in versions(result)


def test_git_deadline_and_unavailable_errors(repo):
    with pytest.raises(CaptureError) as error:
        CaptureStore(repo, CaptureLimits(git_timeout_seconds=1e-12))
    assert error.value.code == 'git_timeout'
    with patch('change_analysis.capture.subprocess.Popen', side_effect=FileNotFoundError), pytest.raises(CaptureError) as error:
        CaptureStore(repo)
    assert error.value.code == 'git_unavailable'


@pytest.mark.parametrize('options', [{'attempts': 6}, {'max_files': 1.5},
                                     {'max_captures': 0}, {'git_timeout_seconds': float('nan')}])
def test_invalid_limits_rejected(options):
    with pytest.raises(ValueError):
        CaptureLimits(**options)


def test_same_bytes_atomic_replacement_between_reads_is_detected(repo):
    with CaptureStore(repo, CaptureLimits(attempts=1)) as store:
        native = store._observe
        calls = 0

        def replace_source():
            nonlocal calls
            result = native()
            calls += 1
            if calls == 1:
                replacement = repo / 'replacement.tmp'
                replacement.write_bytes((repo / 'a.ts').read_bytes())
                replacement.replace(repo / 'a.ts')
            return result

        with patch.object(store, '_observe', side_effect=replace_source), pytest.raises(CaptureError) as error:
            store.capture()
        assert error.value.code == 'source_changed'
        assert not store._captures


def test_edit_during_diff_cannot_publish_stale_observation(repo):
    from change_analysis.diff import compare
    with CaptureStore(repo, CaptureLimits(attempts=1)) as store:
        def changing(*args):
            result = compare(*args)
            (repo / 'a.ts').write_text('saved while diff was computing\n')
            return result

        with patch('change_analysis.capture.compare', side_effect=changing), pytest.raises(CaptureError) as error:
            store.capture()
        assert error.value.code == 'source_changed'
        assert not store._captures


def test_selected_root_replacement_invalidates_session(repo):
    with CaptureStore(repo) as store:
        moved = repo.with_name('original')
        repo.rename(moved)
        repo.mkdir()
        git(repo, 'init', '-q')
        (repo / 'new.ts').write_text('different repository\n')
        with pytest.raises(CaptureError) as error:
            store.capture()
        assert error.value.code == 'boundary_changed'
        assert not store._captures


def test_same_bytes_rewrite_between_reads_is_detected(repo):
    with CaptureStore(repo, CaptureLimits(attempts=1)) as store:
        native = store._observe
        calls = 0

        def rewrite_source():
            nonlocal calls
            result = native()
            calls += 1
            if calls == 1:
                source = repo / 'a.ts'
                original = source.read_bytes()
                source.write_text('temporary content\n')
                source.write_bytes(original)
            return result

        with patch.object(store, '_observe', side_effect=rewrite_source), pytest.raises(CaptureError) as error:
            store.capture()
        assert error.value.code == 'source_changed'
        assert not store._captures


def test_selected_parent_symlink_replacement_invalidates_session(repo):
    folder = repo / 'parent' / 'selected'
    folder.mkdir(parents=True)
    (folder / 'a.ts').write_text('source\n')
    with CaptureStore(folder) as store:
        original_parent = repo / 'original_parent'
        folder.parent.rename(original_parent)
        folder.parent.symlink_to(original_parent, target_is_directory=True)
        with pytest.raises(CaptureError) as error:
            store.capture()
        assert error.value.code == 'boundary_changed'
        assert not store._captures


def test_git_metadata_replacement_invalidates_session(repo):
    with CaptureStore(repo) as store:
        (repo / '.git').rename(repo / 'old_metadata')
        git(repo, 'init', '-q')
        with pytest.raises(CaptureError) as error:
            store.capture()
        assert error.value.code == 'boundary_changed'
        assert not store._captures


def test_atomic_requirement_fails_closed_without_reading_source(repo):
    with CaptureStore(repo) as store:
        with patch.object(store, '_observe', side_effect=AssertionError('Must not capture')):
            with pytest.raises(CaptureError) as error:
                store.capture(require_atomic=True)
        assert error.value.code == 'atomic_capture_unavailable'
        assert not store._captures
        result = store.capture()
        assert result.consistency == 'observed_stable'


@pytest.mark.parametrize('mutation', ['head', 'index', 'addition'])
def test_changes_during_diff_are_rechecked(repo, mutation):
    from change_analysis.diff import compare
    (repo / 'a.ts').write_text('unstaged\n')
    with CaptureStore(repo, CaptureLimits(attempts=1)) as store:
        def changing(*args):
            result = compare(*args)
            if mutation == 'head':
                git(repo, 'commit', '--allow-empty', '-qm', 'move head during diff')
            elif mutation == 'index':
                git(repo, 'add', 'a.ts')
            else:
                (repo / 'new.ts').write_text('added during diff\n')
            return result

        with patch('change_analysis.capture.compare', side_effect=changing), pytest.raises(CaptureError) as error:
            store.capture()
        assert error.value.code == 'source_changed'
        assert not store._captures
