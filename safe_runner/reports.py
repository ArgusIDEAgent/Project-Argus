"""Bounded Node reporter observations. Never establish change attribution."""
import json


def classify(result):
    value = {'outcome': 'setup_failed', 'executed': 0, 'passed': 0, 'failed': 0,
             'skipped': 0, 'todo': 0, 'cancelled': 0,
             'console': result.output, 'cleanup_complete': result.cleanup_complete,
             'termination': result.state, 'exit_code': result.exit_code,
             'elapsed_seconds': result.elapsed_seconds, 'failures': []}
    if result.state != 'completed':
        value['outcome'] = result.state
        return value
    try:
        events = [json.loads(line) for line in result.output.splitlines()]
        if not events or len(events) > 500 or any(not isinstance(e, dict) for e in events):
            return value
        summaries = [e for e in events if e.get('type') == 'summary']
        if len(summaries) != 1:
            return value
        summary = summaries[0]
        file_summaries = [e for e in events if e.get('type') == 'file_summary']
        if not file_summaries:
            value['outcome'] = 'zero_tests' if result.exit_code == 0 else 'setup_failed'
            return value
        if len(file_summaries) != 1:
            return value
        summary = file_summaries[0]
        counts = summary['counts']
        fields = ('tests', 'passed', 'failed', 'skipped', 'todo', 'cancelled')
        if any(type(counts[k]) is not int or not 0 <= counts[k] <= 100000 for k in fields):
            return value
        if sum(counts[k] for k in fields if k != 'tests') != counts['tests']:
            return value
        if type(summary['success']) is not bool:
            return value
        failures = [e for e in events if e.get('type') == 'failure']
        value.update(executed=max(0, counts['tests'] - counts['skipped'] - counts['todo']),
                     **{k: counts[k] for k in fields if k != 'tests'})
        value['failures'] = failures[:20]
        value['omitted_failure_observations'] = max(0,len(failures)-20)
        if counts['failed']:
            leaf=[e for e in failures if e.get('failureType')!='subtestsFailed']
            if leaf and all(e.get('code')=='ERR_ASSERTION' for e in leaf):value['outcome']='assertion_failed'
            elif any(e.get('code') in ('MODULE_NOT_FOUND','ERR_MODULE_NOT_FOUND','ERR_UNKNOWN_FILE_EXTENSION') for e in leaf):value['outcome']='setup_failed'
            else:value['outcome']='test_error'
        elif value['executed'] == 0:
            value['outcome'] = 'zero_tests'
        elif counts['cancelled']:
            value['outcome'] = 'cancelled'
        elif result.exit_code == 0 and summary['success'] and summaries[0]['success'] is True and counts['passed'] > 0:
            value['outcome'] = 'passed'
        return value
    except (ValueError, KeyError, TypeError):
        return value
