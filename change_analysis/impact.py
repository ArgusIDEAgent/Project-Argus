"""Bounded, evidence-backed relationship changes and direct caller impact."""
from collections import Counter, defaultdict, deque
from pydantic import Field

from knowledge_base.identity import file_id
from query_system.contracts import ContractModel
from .evidence import ChangeEvidencePackage, validate_against_capture


class RelationshipChange(ContractModel):
    status: str
    version: str
    relationship_id: str
    relationship: str
    source_id: str
    target_id: str
    proof_record_id: str


class CallerPath(ContractModel):
    version: str
    origin_id: str
    caller_ids: list[str]
    proof_record_ids: list[str]


class ImpactResult(ContractModel):
    capture_id: str
    relationship_changes: list[RelationshipChange]
    caller_paths: list[CallerPath]
    uncertain_relationships: int = Field(ge=0)
    omitted_relationship_changes: int = Field(ge=0)
    omitted_caller_paths: int = Field(ge=0)
    depth_limited_frontiers: int = Field(default=0, ge=0)
    completeness: str = 'partial'
    limitations: list[str]


def analyze_impact(package: ChangeEvidencePackage, snapshot, *, max_depth=2,
                   max_relationships=200, max_paths=100):
    """Only matched endpoints can establish a before/after relationship change."""
    if any(type(v) is not int or v < 1 for v in (max_depth, max_relationships, max_paths)):
        raise ValueError('Impact budgets must be positive integers')
    if max_depth > 4 or max_relationships > 500 or max_paths > 500:
        raise ValueError('Impact budgets exceed supported bounds')
    package = validate_against_capture(package, snapshot)
    old_to_new = {m.before_id: m.after_candidates[0] for m in package.correspondence
                  if m.state == 'matched'}
    for path in package.before.manifest:
        if path in package.after.manifest:
            old_to_new[file_id(package.repository_id, path)] = file_id(package.repository_id, path)
    # Exact content renames can map file nodes, but ambiguous candidates cannot.
    for change in snapshot.changes:
        if len(change.rename_candidates) == 1 and change.path in package.after.manifest:
            old = change.rename_candidates[0]
            if old in package.before.manifest:
                old_to_new[file_id(package.repository_id, old)] = file_id(package.repository_id, change.path)
    before_edges = package.before.relationships
    after_edges = package.after.relationships
    comparable_after_nodes = set(old_to_new.values())
    old_keys = Counter((e.relationship, old_to_new[e.source_id], old_to_new[e.target_id])
                       for e in before_edges if e.source_id in old_to_new and e.target_id in old_to_new)
    after_keys = Counter((e.relationship, e.source_id, e.target_id) for e in after_edges
                         if e.source_id in comparable_after_nodes and e.target_id in comparable_after_nodes)
    incomplete = any(v.omitted_records or v.omitted_relationships or v.unresolved
                     or any('partial' in item or 'diagnostics' in item for item in v.limitations)
                     for v in (package.before, package.after))
    common = old_keys & after_keys
    old_left, new_left = old_keys - common, after_keys - common
    changes = []
    uncertain = (sum(e.source_id not in old_to_new or e.target_id not in old_to_new for e in before_edges)
                 + sum(e.source_id not in comparable_after_nodes or e.target_id not in comparable_after_nodes
                       for e in after_edges))
    for version, edges, remainder, remap, status in (
            ('before', before_edges, old_left, old_to_new, 'removed'),
            ('after', after_edges, new_left, None, 'added')):
        for edge in edges:
            source = remap.get(edge.source_id) if remap is not None else edge.source_id
            target = remap.get(edge.target_id) if remap is not None else edge.target_id
            if source is None or target is None:
                continue
            key = (edge.relationship, source, target)
            if remainder[key] <= 0:
                continue
            remainder[key] -= 1
            changes.append(RelationshipChange(status=status, version=version,
                relationship_id=edge.relationship_id, relationship=edge.relationship,
                source_id=edge.source_id, target_id=edge.target_id, proof_record_id=edge.record_id))
    if incomplete:
        uncertain += len(changes)
        changes = []
    changes.sort(key=lambda c: (c.status, c.relationship, c.source_id, c.target_id, c.relationship_id))
    omitted_changes = max(0, len(changes) - max_relationships)
    changes = changes[:max_relationships]

    # A changed declaration is an origin. Reverse CALLS traversal reports only
    # structural callers, never runtime reach or a proven regression.
    paths = []
    omitted_paths = 0
    depth_frontiers = 0
    for side, version in (('before', package.before), ('after', package.after)):
        callers = defaultdict(list)
        for edge in version.relationships:
            if edge.relationship == 'CALLS':
                callers[edge.target_id].append(edge)
        for entries in callers.values():
            entries.sort(key=lambda e: (e.source_id, e.relationship_id))
        for symbol in sorted(version.symbols, key=lambda s: (s.path, s.start_line, s.symbol_id)):
            if not symbol.changed:
                continue
            queue = deque([(symbol.symbol_id, (), ())])
            seen = {symbol.symbol_id}
            while queue:
                node, chain, proofs = queue.popleft()
                if len(chain) >= max_depth:
                    depth_frontiers += sum(e.source_id not in seen for e in callers[node])
                    continue
                for edge in callers[node]:
                    if edge.source_id in seen:
                        continue
                    seen.add(edge.source_id)
                    new_chain = chain + (edge.source_id,)
                    new_proofs = proofs + (edge.record_id,)
                    if len(paths) < max_paths:
                        paths.append(CallerPath(version=side, origin_id=symbol.symbol_id,
                            caller_ids=list(new_chain), proof_record_ids=list(new_proofs)))
                    else:
                        omitted_paths += 1
                    queue.append((edge.source_id, new_chain, new_proofs))
    limitations = ['Caller paths are static relationships, not runtime reach or behavioral proof.']
    if incomplete or uncertain or package.before.omitted_relationships or package.after.omitted_relationships:
        limitations.append('Some relationships could not be compared because correspondence or evidence was incomplete.')
    if omitted_changes or omitted_paths or depth_frontiers:
        limitations.append('Display budgets omitted relationship changes or caller paths; counts are explicit.')
    return ImpactResult(capture_id=package.capture_id, relationship_changes=changes,
        caller_paths=paths, uncertain_relationships=uncertain,
        omitted_relationship_changes=omitted_changes, omitted_caller_paths=omitted_paths,
        depth_limited_frontiers=depth_frontiers,
        completeness='partial' if incomplete or uncertain or omitted_changes or omitted_paths or depth_frontiers else 'complete_within_supported_scope',
        limitations=limitations)
