"""Explicit local atomic-capture qualification; emits counts only, never source."""
import argparse
import json
import time

from .atomic_session import atomic_session
from .service import analyze_change


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    args = parser.parse_args()
    started = time.monotonic()
    with atomic_session(args.root) as store:
        capture = store.capture()
        result = analyze_change(store, capture.id)
        output = {'capture_id': capture.id, 'consistency': capture.consistency,
                  'freshness': result.freshness,
                  'versions': {side: {'files': len(value.manifest), 'symbols': len(value.symbols),
                                      'relationships': len(value.relationships),
                                      'unresolved': len(value.unresolved),
                                      'omitted_records': value.omitted_records,
                                      'omitted_relationships': value.omitted_relationships}
                               for side, value in (('before', result.evidence.before),
                                                   ('after', result.evidence.after))},
                  'relationship_changes': len(result.impact.relationship_changes),
                  'uncertain_relationships': result.impact.uncertain_relationships,
                  'caller_paths': len(result.impact.caller_paths)}
    output['elapsed_seconds'] = round(time.monotonic() - started, 3)
    output['mount_cleanup'] = 'completed'
    print(json.dumps(output))


if __name__ == '__main__':
    main()
