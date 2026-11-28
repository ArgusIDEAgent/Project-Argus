"""Conservative paired-observation attribution; never infer coverage or intent."""
import json
import re
from hashlib import sha256


def environment(snapshot, paths):
    before = {v.path: v for v in snapshot.before if v.data is not None}
    after = {v.path: v for v in snapshot.after if v.data is not None}
    differences = []
    for path in paths:
        if path not in before or path not in after or before[path].digest != after[path].digest:
            differences.append('Check source changed or absent in baseline: ' + path)
    def metadata(side):
        versions = (*getattr(snapshot, side + '_metadata'), *getattr(snapshot, side))
        return sorted((v.path, v.digest, v.exclusion) for v in versions if v in getattr(snapshot, side + '_metadata') or '.config.' in v.path)
    if metadata('before') != metadata('after'):
        differences.append('Captured dependency/runtime/configuration metadata differs between versions')
    if snapshot.head is None:
        differences.append('No committed baseline exists')
    return {'comparable': not differences, 'differences': differences,
            'test_policy': 'Identical captured check bytes; separate disposable materializations; same pinned dependencies/runtime/policy',
            'attempts': 2}


def signature(value):
    return sha256(json.dumps({k: value.get(k) for k in ('outcome','executed','passed','failed','skipped','todo','cancelled','failures')}, sort_keys=True).encode()).hexdigest()


def attribution(results, comparable):
    base = {'classification': 'inconclusive', 'unstable': False,
            'reason': 'Insufficient comparable paired observations; no regression claim.'}
    if not comparable:
        base['reason'] = 'Baseline check/configuration/dependency compatibility was not established.'
        return base
    groups = {side: [r for r in results if r.get('version') == side] for side in ('before','after')}
    if any(len(values) != 2 for values in groups.values()):
        return base
    if any(r.get('omitted_failure_observations',0) or any(len(f.get('message',''))>=1000 for f in r.get('failures',[])) for r in results):
        base['reason'] = 'Failure observations were truncated; retry identity is incomplete.'
        return base
    if any(any(not re.fullmatch(r'[a-f0-9]{64}',f.get('failure_digest','')) for f in r.get('failures',[])) for r in results):
        base['reason']='Unredacted failure fingerprints are unavailable; redacted messages cannot establish exact retry identity.'
        return base
    if any(len({signature(v) for v in values}) != 1 for values in groups.values()):
        base.update(unstable=True, reason='Retry observations differ; all attempts retained.')
        return base
    before, after = groups['before'][0], groups['after'][0]
    if not all(v.get('cleanup_complete') and v.get('executed',0)>0 for v in (before,after)):
        return base
    if before.get('executed') != after.get('executed'):
        base['reason'] = 'Runtime test counts differ between versions.'
        return base
    if before['outcome'] == 'passed' and after['outcome'] == 'assertion_failed' and after.get('failures'):
        base.update(classification='introduced', reason='Repeated identical check passed on baseline and assertion-failed on captured current source under comparable environments. This does not establish coverage or intended behavior beyond that check.')
    elif before['outcome'] == after['outcome'] == 'assertion_failed' and before.get('failures') and all(f.get('message') for f in before['failures']) and signature(before) == signature(after):
        base.update(classification='pre_existing', reason='The same assertion failure was observed repeatedly on both versions.')
    elif before['outcome'] == after['outcome'] == 'passed':
        base.update(classification='no_failure_observed', reason='Selected checks passed repeatedly on both versions; this is not a coverage guarantee.')
    return base
