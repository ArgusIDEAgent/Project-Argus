"""Compare captured bytes only; never invoke Git diff drivers or textconv."""
from difflib import SequenceMatcher
from .contracts import FileChange


def compare(before, after, max_lines):
    old, new = {v.path: v for v in before}, {v.path: v for v in after}
    removed = [v for v in before if v.path not in new and v.data is not None]
    changes = []
    for path in sorted(old.keys() | new.keys()):
        a, b = old.get(path), new.get(path)
        # Excluded entries remain visible even when equality cannot be established.
        limitation = next((v.exclusion for v in (a, b) if v and v.exclusion), None)
        if a and b and not limitation and (a.digest, a.mode) == (b.digest, b.mode):
            continue
        status = ('unanalyzed' if limitation else
                  'added' if a is None else 'deleted' if b is None else 'modified')
        ranges, candidates = (), ()
        if not limitation:
            left = a.data.decode('utf-8').splitlines(keepends=True) if a else []
            right = b.data.decode('utf-8').splitlines(keepends=True) if b else []
            if len(left) + len(right) > max_lines:
                limitation = 'diff_line_budget'
            else:
                ranges = tuple((i + 1, j + 1, k + 1, l + 1)
                               for op, i, j, k, l in SequenceMatcher(None, left, right).get_opcodes()
                               if op != 'equal')
            if a is None:
                candidates = tuple(v.path for v in removed if v.digest == b.digest)
        changes.append(FileChange(path, status, ranges, limitation, candidates))
    return tuple(changes)
