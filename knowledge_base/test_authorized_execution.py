"""P6 session authorization races and structured result classification."""
from dataclasses import replace
import json
import threading
import time
from unittest.mock import patch

from fastapi import HTTPException
import pytest

from change_api import ChangeJobs, Start, PlanRequest
from knowledge_base.identity import repository_id
from knowledge_base.test_change_capture import repo, git
from knowledge_base.test_test_planning import prepare
from safe_runner.contracts import Policy, RunResult
from safe_runner.reports import classify


def report(tests=1, passed=1, failed=0, skipped=0, todo=0, cancelled=0, code=0, failure=None, file=True):
    summary = {'counts': dict(tests=tests, passed=passed, failed=failed, skipped=skipped, todo=todo, cancelled=cancelled), 'success': code == 0}
    events = ([{'type': 'failure', 'code': failure}] if failure else [])
    if file:
        events.append({'type': 'file_summary', **summary})
    events.append({'type': 'summary', **summary})
    return RunResult('completed', code, '\n'.join(map(json.dumps, events)), False, True, None, .1, 'fixture')


class FakeBroker:
    policy = Policy()
    launched = []
    identity_value = {'runtime': {'image_id': 'pinned'}, 'policy_digest': 'qualified'}

    def capability(self, **kwargs):
        return {'isolation_qualified': True, 'identity': self.identity_value}

    def _run_check(self, files, check, cancel, before_launch):
        before_launch()
        if cancel.is_set():
            return replace(report(), state='cancelled')
        self.launched.append(check.path)
        return report()


@pytest.fixture
def execution(repo):
    capture, analysis = prepare(repo)
    rid = repository_id(repo)
    manager = ChangeJobs({rid: {'rootPath': str(repo)}})
    started = manager.start(Start(repo_id=rid, idempotency_key='execution'))
    job = manager.jobs[started['id']]
    job.update(state='completed', snapshot=capture, analysis=analysis)
    manager.test_plan(job['id'], PlanRequest(repo_id=rid))
    checks = [c.check_id for c in job['plan'].proposed_checks if c.eligibility == 'reviewable_existing_test']
    manager.test_plan(job['id'], PlanRequest(repo_id=rid, selected_check_ids=checks))
    manager.execution.broker_factory = FakeBroker
    FakeBroker.launched = []
    yield manager, job, rid
    manager.close()


def wait(manager, value):
    run = manager.execution.runs[value['id']]
    for _ in range(1500):
        if run['state'] not in ('queued', 'running'):
            return manager.execution.view(run)
        time.sleep(.01)
    raise AssertionError('Worker did not finish')


def authorize(manager, job, rid, kind):
    e = manager.execution
    review = e.review(job['id'], rid, kind)
    return review, e.submit(job['id'], rid, review['authorization'], review['review_digest'], True)


def test_separate_preparation_and_execution_exact_authorization(execution):
    manager, job, rid = execution
    with pytest.raises(HTTPException) as error:
        manager.execution.review(job['id'], rid, 'run')
    assert error.value.status_code == 409
    review, value = authorize(manager, job, rid, 'prepare')
    assert wait(manager, value)['results'][0]['outcome'] == 'passed'
    review, value = authorize(manager, job, rid, 'run')
    result = wait(manager, value)
    assert result['executed'] == 1 and result['historical_capture']
    assert result['results'][0]['outcome'] == 'passed'
    assert FakeBroker.launched == ['prepare.cjs', 'value.test.js']
    assert manager.execution.submit(job['id'], rid, review['authorization'], review['review_digest'], True)['id'] == value['id']
    assert len(FakeBroker.launched) == 2
    with pytest.raises(HTTPException):
        manager.execution.submit(job['id'], rid, review['authorization'], 'wrong', True)


def test_trust_ownership_expiry_and_wrong_digest_do_not_launch(execution):
    manager, job, rid = execution
    e = manager.execution
    review = e.review(job['id'], rid, 'prepare')
    for repo_id, token, sha, trusted in [(rid, review['authorization'], review['review_digest'], False),
        ('foreign', review['authorization'], review['review_digest'], True), (rid, 'x'*64, review['review_digest'], True),
        (rid, review['authorization'], 'x'*64, True)]:
        with pytest.raises(HTTPException):
            e.submit(job['id'], repo_id, token, sha, trusted)
    e.tickets[review['authorization']]['created'] -= 61
    with pytest.raises(HTTPException):
        e.submit(job['id'], rid, review['authorization'], review['review_digest'], True)
    assert FakeBroker.launched == []


def test_source_change_after_authorization_blocks_launch(execution):
    manager, job, rid = execution
    review = manager.execution.review(job['id'], rid, 'prepare')
    from pathlib import Path
    (Path(job['root']) / 'a.js').write_text('export const changedAgain=3;')
    value = manager.execution.submit(job['id'], rid, review['authorization'], review['review_digest'], True)
    assert wait(manager, value)['state'] == 'failed'
    assert FakeBroker.launched == []


def test_policy_change_after_review_blocks_launch(execution):
    manager, job, rid = execution
    review = manager.execution.review(job['id'], rid, 'prepare')
    with patch.object(FakeBroker, 'identity_value', {'runtime': {}, 'policy_digest': 'new'}):
        value = manager.execution.submit(job['id'], rid, review['authorization'], review['review_digest'], True)
        assert wait(manager, value)['state'] == 'failed'
    assert FakeBroker.launched == []


def test_selection_invalidation_and_session_close_revoke_authorization(execution):
    manager, job, rid = execution
    review = manager.execution.review(job['id'], rid, 'prepare')
    manager.test_plan(job['id'], PlanRequest(repo_id=rid, selected_check_ids=[]))
    with pytest.raises(HTTPException):
        manager.execution.submit(job['id'], rid, review['authorization'], review['review_digest'], True)
    manager.close()
    assert not manager.execution.tickets and not manager.execution.receipts
    assert FakeBroker.launched == []


def test_global_capacity_and_cancellation_are_bounded(execution):
    manager, job, rid = execution
    entered, release = threading.Event(), threading.Event()
    original = FakeBroker._run_check
    def blocking(self, files, check, cancel, before_launch):
        entered.set()
        assert release.wait(5)
        return original(self, files, check, cancel, before_launch)
    with patch.object(FakeBroker, '_run_check', blocking):
        _, first = authorize(manager, job, rid, 'prepare')
        assert entered.wait(5)
        review = manager.execution.review(job['id'], rid, 'prepare')
        with pytest.raises(HTTPException) as error:
            manager.execution.submit(job['id'], rid, review['authorization'], review['review_digest'], True)
        assert error.value.status_code == 429
        manager.execution.owned(job['id'], rid, first['id'])['cancel'].set()
        release.set()
        assert wait(manager, first)['state'] == 'cancelled'
    assert FakeBroker.launched == []


def test_last_moment_revalidation_blocks_launch(execution):
    manager, job, rid = execution
    def changed_before_start(self, files, check, cancel, before_launch):
        job['stale_for_workspace'] = True
        before_launch()
        raise AssertionError('Must not reach container start')
    with patch.object(FakeBroker, '_run_check', changed_before_start):
        _, value = authorize(manager, job, rid, 'prepare')
        assert wait(manager, value)['state'] == 'failed'
    assert FakeBroker.launched == []


def test_unqualified_runtime_never_launches(execution):
    manager, job, rid = execution
    with patch.object(FakeBroker, 'capability', return_value={'isolation_qualified': False, 'reason': 'stale qualification'}):
        with pytest.raises(HTTPException) as error:
            manager.execution.review(job['id'], rid, 'prepare')
        assert error.value.status_code == 503
    assert FakeBroker.launched == []


@pytest.mark.parametrize('profile_update', [ {'runtime_requirement': '20'}, {'dependency_state': 'unknown'}, {'package_root': 'nested'} ])
def test_unsupported_profiles_fail_closed(execution, profile_update):
    manager, job, rid = execution
    for key, value in profile_update.items():
        setattr(job['plan'].profiles[0], key, value)
    with pytest.raises(HTTPException):
        manager.execution.review(job['id'], rid, 'prepare')
    assert FakeBroker.launched == []


@pytest.mark.parametrize('result,outcome,executed', [
    (report(), 'passed', 1), (report(file=False), 'zero_tests', 0),
    (report(passed=0, skipped=1), 'zero_tests', 0),
    (report(passed=0, failed=1, code=1, failure='ERR_ASSERTION'), 'assertion_failed', 1),
    (report(passed=0, failed=1, code=1, failure='ERR_MODULE_NOT_FOUND', file=False), 'setup_failed', 0),
    (replace(report(), output='forged invalid data'), 'setup_failed', 0),
    (replace(report(), state='timed_out'), 'timed_out', 0),
    (replace(report(), state='cancelled'), 'cancelled', 0),
    (replace(report(), state='cleanup_failed', cleanup_complete=False), 'cleanup_failed', 0),
])
def test_report_outcomes(result, outcome, executed):
    value = classify(result)
    assert (value['outcome'], value['executed']) == (outcome, executed)
