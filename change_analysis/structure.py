"""Resolve captured JS/TS source without reading or executing the repository."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from knowledge_base.identity import file_id
from knowledge_base.parser import parse_source
from .capture import CaptureError

BRIDGE = Path(__file__).resolve().parents[1] / 'knowledge_base' / 'semantic_resolver.cjs'


def semantic_events(snapshot, side):
    sources = {v.path: v.data.decode('utf-8') for v in getattr(snapshot, side) if v.data is not None}
    if not sources:
        return {'events': [], 'diagnostics': [], 'omittedEvents': 0, 'typescriptVersion': 'none'}
    payload = json.dumps({'root': snapshot.root, 'files': sorted(sources), 'configs': [],
                          'sources': sources}, ensure_ascii=False)
    # Node reads the checked-in bridge and its TypeScript dependency. The host
    # supplies every repository source byte through stdin; no repository code runs.
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'TMPDIR') if key in os.environ}
    env['NODE_OPTIONS'] = '--max-old-space-size=512'
    try:
        proc = subprocess.run(['node', str(BRIDGE)], input=payload, text=True,
                              capture_output=True, timeout=30, env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CaptureError('semantic_unavailable', 'Captured semantic pass did not complete') from error
    if proc.returncode or len(proc.stdout) > 16_000_000:
        raise CaptureError('semantic_unavailable', 'Captured semantic pass failed or exceeded output budget')
    try:
        result = json.loads(proc.stdout)
        if not isinstance(result.get('events'), list) or not isinstance(result.get('diagnostics'), list):
            raise ValueError('Malformed semantic result')
        return result
    except (ValueError, TypeError) as error:
        raise CaptureError('semantic_unavailable', 'Captured semantic result was malformed') from error


def canonical_relationships(snapshot, side, retained_symbols, retained_files, events):
    """Map resolver byte spans to parsed declarations; unresolved stays explicit."""
    sources = {v.path: v for v in getattr(snapshot, side) if v.data is not None}
    parsed = {path: parse_source(v.data, suffix=Path(path).suffix.lower(),
                                repository_id=snapshot.repository_id, relative_path=path)
              for path, v in sources.items()}
    retained = set(retained_symbols) | set(retained_files)
    edges, unresolved, omitted = [], [], 0
    for event in events:
        path, start, end = event.get('file'), event.get('startByte'), event.get('endByte')
        if path not in sources or not isinstance(start, int) or not isinstance(end, int):
            raise ValueError('Semantic event points outside captured source')
        data = sources[path].data
        if start < 0 or end < start or end > len(data):
            raise ValueError('Semantic event has invalid byte span')
        kind = event.get('kind')
        if kind == 'unresolved':
            unresolved.append((path, data.count(b'\n', 0, start) + 1,
                               event.get('relationship', 'UNKNOWN'), event.get('reason', 'unresolved')))
            continue
        if kind not in ('call', 'import', 'export'):
            continue
        if parsed[path].status != 'parsed':
            unresolved.append((path, data.count(b'\n', 0, start) + 1, kind, 'partial_syntax'))
            continue
        line = data.count(b'\n', 0, start) + 1
        nodes = parsed[path].symbols
        enclosing = [s for s in nodes if s['start_byte'] <= start and s['end_byte'] >= end]
        scope = min(enclosing, key=lambda s: s['end_byte'] - s['start_byte'])['id'] if enclosing else None
        source_id = file_id(snapshot.repository_id, path) if kind in ('import', 'export') else scope
        target_id = None
        relationship = {'call': 'CALLS', 'import': 'IMPORTS', 'export': 'EXPORTS'}[kind]
        if kind == 'import':
            target_path = (event.get('targetModule') or {}).get('file')
            if target_path in sources:
                target_id = file_id(snapshot.repository_id, target_path)
        else:
            descriptor = event.get('target') or {}
            target_path = descriptor.get('file')
            if target_path in parsed and isinstance(descriptor.get('startByte'), int):
                matches = [s for s in parsed[target_path].symbols
                           if s['start_byte'] <= descriptor.get('endByte', descriptor['startByte'])
                           and s['end_byte'] >= descriptor['startByte']
                           and s['name'] == descriptor.get('name')]
                if matches:
                    best = min(abs(s['start_byte'] - descriptor['startByte'])
                               + abs(s['end_byte'] - descriptor.get('endByte', s['end_byte'])) for s in matches)
                    unique = [s for s in matches if abs(s['start_byte'] - descriptor['startByte'])
                              + abs(s['end_byte'] - descriptor.get('endByte', s['end_byte'])) == best]
                    if len(unique) == 1:
                        target_id = unique[0]['id']
        if target_path in parsed and parsed[target_path].status != 'parsed':
            target_id = None
        if not source_id or not target_id:
            unresolved.append((path, line, relationship, 'unresolved_target_or_scope'))
            continue
        if source_id not in retained or target_id not in retained:
            omitted += 1
            continue
        edge_id = hashlib.sha256(json.dumps([side, path, start, end, relationship,
                                             source_id, target_id]).encode()).hexdigest()
        edges.append({'id': edge_id, 'source_id': source_id, 'target_id': target_id,
                      'relationship': relationship, 'path': path, 'line': line,
                      'start_byte': start, 'end_byte': end})
    return sorted(edges, key=lambda e: (e['path'], e['start_byte'], e['relationship'], e['id'])), sorted(set(unresolved)), omitted
