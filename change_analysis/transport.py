"""Private same-user capture handoff; JSON only, never executable serialization."""
import base64
from dataclasses import asdict
import hashlib
from .contracts import ChangeSnapshot, SourceVersion, FileChange
from knowledge_base.identity import repository_id, relative_path
from .metadata_policy import is_metadata, POLICY


def encode(snapshot):
    value = asdict(snapshot)
    for side in ('before', 'after', 'before_metadata', 'after_metadata'):
        for source in value[side]:
            if source['data'] is not None:
                source['data'] = base64.b64encode(source['data']).decode('ascii')
    return value


def decode(value, root):
    if value['root'] != root or value['repository_id'] != repository_id(root):
        raise ValueError('Capture ownership mismatch')
    if value['consistency'] != 'atomic_apfs' or not value['atomic_snapshot_id']:
        raise ValueError('Atomic capture required')
    if value.get('metadata_policy', 'none') not in ('none', POLICY):
        raise ValueError('Unsupported capture metadata policy')
    total = 0
    for side in ('before', 'after', 'before_metadata', 'after_metadata'):
        value.setdefault(side, [])
        sources = []
        seen = set()
        if len(value[side]) > 10000:
            raise ValueError('Capture inventory too large')
        for source in value[side]:
            relative_path(source['path'])
            if side.endswith('_metadata') and not is_metadata(source['path']):
                raise ValueError('Metadata is outside the captured allowlist')
            if source['path'] in seen:
                raise ValueError('Duplicate capture source')
            seen.add(source['path'])
            if source['data'] is not None:
                data = base64.b64decode(source['data'], validate=True)
                total += len(data)
                if len(data) > 1000000 or total > 32000000 or hashlib.sha256(data).hexdigest() != source['digest']:
                    raise ValueError('Capture byte budget/hash mismatch')
                source['data'] = data
            sources.append(SourceVersion(**source))
        value[side] = tuple(sources)
    value['changes'] = tuple(FileChange(**{**c, 'ranges': tuple(tuple(r) for r in c['ranges']),
                                          'rename_candidates': tuple(c['rename_candidates'])}) for c in value['changes'])
    value['staging'] = tuple(tuple(s) for s in value['staging'])
    value['conflicts'] = tuple(value['conflicts'])
    value['notices'] = tuple(value['notices'])
    if total != value['size_bytes']:
        raise ValueError('Capture size mismatch')
    return ChangeSnapshot(**value)
