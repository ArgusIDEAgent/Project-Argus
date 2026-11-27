"""Conservative correspondence derived only from the two captured versions."""
from collections import Counter
from hashlib import sha256
from pathlib import PurePosixPath
import re

from knowledge_base.parser import parse_source


def correspondence(snapshot, before, after):
    fingerprints = {}
    for side in ('before', 'after'):
        for source in getattr(snapshot, side):
            if source.data is None:
                continue
            parsed = parse_source(source.data, suffix=PurePosixPath(source.path).suffix.lower(),
                                  repository_id=snapshot.repository_id, relative_path=source.path)
            for symbol in parsed.symbols:
                # Exact declaration bytes except the first declaration-name occurrence.
                # Restrict rename heuristics to leaf named declarations to avoid
                # renaming an enclosing class/function based on nested code.
                if any(s['parent_symbol_id'] == symbol['id'] for s in parsed.symbols):
                    continue
                data = source.data[symbol['start_byte']:symbol['end_byte']].decode('utf-8')
                data = re.sub(r'\b' + re.escape(symbol['name']) + r'\b', '__name__', data, count=1)
                fingerprints[side, symbol['id']] = sha256(data.encode()).hexdigest()
    renames = {c.path: c.rename_candidates for c in snapshot.changes if c.rename_candidates}
    mappings = []
    for old in before.symbols:
        exact = [new for new in after.symbols if
                 (old.kind, old.qualified_name) == (new.kind, new.qualified_name)
                 and (old.path == new.path or old.path in renames.get(new.path, ()))]
        basis = 'declaration_key' if all(n.path == old.path for n in exact) else 'exact_file_rename'
        candidates = exact
        if not candidates and ('before', old.symbol_id) in fingerprints:
            # Structural association is limited to the same enclosing scope or
            # removed-to-added files. Similarity thresholds cannot prove identity.
            candidates = [new for new in after.symbols if new.kind == old.kind
                and fingerprints.get(('after', new.symbol_id)) == fingerprints['before', old.symbol_id]
                and ((new.path == old.path and new.qualified_name.rpartition('.')[0] == old.qualified_name.rpartition('.')[0])
                     or (old.path not in after.manifest and new.path not in before.manifest))]
            basis = 'structural_content'
        ids = sorted(n.symbol_id for n in candidates)
        mappings.append(dict(before_id=old.symbol_id, after_candidates=ids,
                             state='unmatched' if not ids else 'matched' if len(ids) == 1 else 'ambiguous',
                             basis=basis if ids else 'unresolved'))
    # Two old declarations competing for one new declaration cannot each claim
    # a unique correspondence. Preserve candidate lists and explicit ambiguity.
    counts = Counter(i for m in mappings for i in m['after_candidates'])
    for mapping in mappings:
        if any(counts[i] > 1 for i in mapping['after_candidates']):
            mapping['state'] = 'ambiguous'
    return mappings
