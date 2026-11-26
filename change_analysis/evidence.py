"""P2 foundation: two validated source-evidence packages from captured bytes.

Syntax definitions are not a resolved call graph. This first P2 slice deliberately
publishes no relationship claims; later impact analysis must supply canonical
relationship evidence without weakening EvidencePackage integrity.
"""
from hashlib import sha256
from importlib.metadata import version as distribution_version
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, model_validator

from knowledge_base.identity import file_id, relative_path
from knowledge_base.parser import parse_source, SYNTAX_SCHEMA_VERSION
from query_system.contracts import ContractModel, Evidence, RepositoryId, SourceHash
from query_system.evidence_contracts import Citation, EvidenceLimits, EvidencePackage, EvidenceRecord
from .capture import CaptureError
from .structure import semantic_events, canonical_relationships


# Resolve trusted parser distribution versions once when loading the adapter.
_ADAPTER_VERSIONS = {'syntax_schema': str(SYNTAX_SCHEMA_VERSION), 'change_evidence': '1',
                     **{name: distribution_version(name) for name in
                        ('tree-sitter', 'tree-sitter-javascript', 'tree-sitter-typescript')}}


def version_id(capture_digest, side):
    return sha256(f'{capture_digest}:{side}'.encode()).hexdigest()[:32]


class CapturedSymbol(ContractModel):
    symbol_id: str
    path: str
    name: str
    qualified_name: str
    kind: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    record_id: str
    changed: bool


class CapturedRelationship(ContractModel):
    relationship_id: str
    source_id: str
    target_id: str
    relationship: Literal['CALLS', 'IMPORTS', 'EXPORTS']
    path: str
    line: int = Field(ge=1)
    start_byte: int = Field(ge=0)
    end_byte: int = Field(ge=0)
    record_id: str


class VersionEvidence(ContractModel):
    version: Literal['before', 'after']
    package: EvidencePackage
    manifest: dict[str, SourceHash]
    symbols: list[CapturedSymbol]
    relationships: list[CapturedRelationship] = Field(default_factory=list)
    unresolved: list[str] = Field(default_factory=list)
    omitted_relationships: int = Field(default=0, ge=0)
    omitted_records: int = Field(ge=0)
    limitations: list[str]

    @model_validator(mode='after')
    def integrity(self):
        # Revalidate even pre-constructed nested models at the wrapper boundary.
        self.package = EvidencePackage.model_validate(self.package.model_dump())
        for path in self.manifest:
            relative_path(path)
        seen = set()
        for symbol in self.symbols:
            if symbol.symbol_id in seen:
                raise ValueError('Duplicate symbol identity within one source version')
            seen.add(symbol.symbol_id)
            proof = self.package.evidence[symbol.record_id].proof
            if (proof.symbol_id, proof.file_path, proof.start_line, proof.end_line) != (
                    symbol.symbol_id, symbol.path, symbol.start_line, symbol.end_line):
                raise ValueError('Symbol differs from its version-owned proof')
        for record in self.package.evidence.values():
            proof = record.proof
            if proof.file_path not in self.manifest or self.manifest[proof.file_path] != proof.source_hash:
                raise ValueError('Source proof differs from its version manifest')
            if proof.file_id != file_id(self.package.repository_id, proof.file_path):
                raise ValueError('Source file identity differs from repository ownership')
        nodes = seen | {file_id(self.package.repository_id, path) for path in self.manifest}
        edges = set()
        for edge in self.relationships:
            if edge.relationship_id in edges or edge.source_id not in nodes or edge.target_id not in nodes:
                raise ValueError('Relationship has duplicate identity or dangling endpoint')
            edges.add(edge.relationship_id)
            proof = self.package.evidence[edge.record_id].proof
            if (proof.evidence_id, proof.evidence_type, proof.resolution, proof.file_path,
                    proof.start_line) != (edge.relationship_id, 'graph_relationship', 'resolved',
                                          edge.path, edge.line):
                raise ValueError('Relationship lacks its version-owned source proof')
        return self


class SymbolCorrespondence(ContractModel):
    before_id: str
    after_candidates: list[str]
    state: Literal['matched', 'ambiguous', 'unmatched']
    basis: Literal['declaration_key', 'exact_file_rename', 'unresolved']

    @model_validator(mode='after')
    def cardinality(self):
        count = len(self.after_candidates)
        if len(set(self.after_candidates)) != count:
            raise ValueError('Duplicate correspondence candidate')
        expected = 'unmatched' if count == 0 else 'matched' if count == 1 else 'ambiguous'
        if self.state != expected:
            raise ValueError('Ambiguous correspondence must remain visible')
        return self


class ChangeEvidencePackage(ContractModel):
    schema_version: Literal[1] = 1
    capture_id: str
    capture_digest: SourceHash
    repository_id: RepositoryId
    atomic_snapshot_id: str
    before: VersionEvidence
    after: VersionEvidence
    correspondence: list[SymbolCorrespondence]
    adapter_versions: dict[str, str]
    limitations: list[str]

    @model_validator(mode='after')
    def integrity(self):
        for side in ('before', 'after'):
            value = VersionEvidence.model_validate(getattr(self, side).model_dump())
            setattr(self, side, value)
            if value.version != side or value.package.repository_id != self.repository_id:
                raise ValueError('Evidence version or repository ownership differs')
            if value.package.snapshot_id != version_id(self.capture_digest, side):
                raise ValueError('Evidence belongs to another capture or source version')
            for record in value.package.evidence.values():
                if record.proof.index_snapshot_id != f'capture:{self.capture_id}:{side}':
                    raise ValueError('Evidence stage belongs to another source version')
                if record.provenance != {'capture_id': self.capture_id, 'version': side}:
                    raise ValueError('Evidence provenance differs from capture ownership')
        old = {s.symbol_id for s in self.before.symbols}
        new = {s.symbol_id for s in self.after.symbols}
        keys = [m.before_id for m in self.correspondence]
        if len(set(keys)) != len(keys) or set(keys) != old:
            raise ValueError('Correspondence must account for every retained baseline symbol')
        for mapping in self.correspondence:
            if set(mapping.after_candidates) - new:
                raise ValueError('Correspondence has a dangling candidate')
        return self


def _changed(snapshot, side, path, start, end):
    for change in snapshot.changes:
        if change.path != path:
            continue
        if change.status in ('added', 'deleted') or change.limitation:
            return True
        for a, b, c, d in change.ranges:
            low, high = (a, b) if side == 'before' else (c, d)
            if low == high:
                if start <= low <= end:
                    return True
            elif start < high and end >= low:
                return True
    return False


def _version(snapshot, side, limit):
    sources = getattr(snapshot, side)
    records, citations, symbols, manifest, limitations = {}, {}, [], {}, []
    sid = version_id(snapshot.digest, side)
    pid = f'change:{snapshot.id}:{side}'
    omitted = 0
    seen_paths = set()

    def record(proof):
        nonlocal omitted
        if len(records) >= limit:
            omitted += 1
            return False
        key = proof.evidence_id
        records[key] = EvidenceRecord(record_id=key, proof=proof,
            provenance={'capture_id': snapshot.id, 'version': side}, verification='source_verified',
            context_status='not_requested', uses=[])
        ref = f'cm7:{pid}:{key}'
        citations[ref] = Citation(reference=ref, record_id=key, source=proof)
        return True

    source_limit = limit - min(40, limit // 5)
    for source in sorted(sources, key=lambda v: v.path):
        relative_path(source.path)
        if source.path in seen_paths:
            raise ValueError('Duplicate path in capture manifest')
        seen_paths.add(source.path)
        if source.data is None:
            limitations.append(f'{side}:{source.path}: excluded ({source.exclusion})')
            continue
        if sha256(source.data).hexdigest() != source.digest:
            raise ValueError('Captured source bytes differ from their digest')
        manifest[source.path] = source.digest
        fid = file_id(snapshot.repository_id, source.path)
        common = dict(repository_id=snapshot.repository_id, snapshot_id=sid,
                      index_snapshot_id=f'capture:{snapshot.id}:{side}', file_id=fid,
                      file_path=source.path, source_hash=source.digest, schema_version=SYNTAX_SCHEMA_VERSION)
        lines = max(1, len(source.data.splitlines()))
        if len(records) < source_limit:
            record(Evidence(evidence_id=fid, evidence_type='source_range', start_line=1,
                            end_line=lines, resolution='not_applicable', **common))
        else:
            omitted += 1
        parsed = parse_source(source.data, suffix=PurePosixPath(source.path).suffix.lower(),
                              repository_id=snapshot.repository_id, relative_path=source.path)
        if parsed.status != 'parsed':
            limitations.append(f'{side}:{source.path}: syntax is partial; definitions are syntax observations only')
        for symbol in sorted(parsed.symbols, key=lambda s: (s['start_byte'], s['end_byte'], s['id'])):
            end = min(lines, symbol['end_line'])
            proof = Evidence(evidence_id=symbol['id'], symbol_id=symbol['id'],
                             evidence_type='symbol_definition', resolution='syntax_only',
                             start_line=symbol['start_line'], end_line=end, **common)
            if len(records) < source_limit and record(proof):
                symbols.append(CapturedSymbol(symbol_id=symbol['id'], path=source.path,
                    name=symbol['name'], qualified_name=symbol['qualified_name'], kind=symbol['kind'],
                    start_line=symbol['start_line'], end_line=end, record_id=proof.evidence_id,
                    changed=_changed(snapshot, side, source.path, symbol['start_line'], end)))
            else:
                omitted += 1
    semantic = semantic_events(snapshot, side)
    edge_data, unresolved, omitted_nodes = canonical_relationships(
        snapshot, side, {s.symbol_id for s in symbols},
        {record.proof.file_id for record in records.values()
         if record.proof.evidence_type == 'source_range'}, semantic['events'])
    relationships = []
    for edge in edge_data:
        if len(records) >= limit:
            omitted_nodes += 1
            continue
        source = next(v for v in sources if v.path == edge['path'])
        proof = Evidence(evidence_id=edge['id'], evidence_type='graph_relationship',
                         repository_id=snapshot.repository_id, snapshot_id=sid,
                         index_snapshot_id=f'capture:{snapshot.id}:{side}',
                         file_id=file_id(snapshot.repository_id, source.path),
                         file_path=source.path, source_hash=source.digest,
                         start_line=edge['line'], end_line=edge['line'],
                         resolution='resolved', schema_version=SYNTAX_SCHEMA_VERSION)
        if record(proof):
            relationships.append(CapturedRelationship(relationship_id=edge['id'],
                source_id=edge['source_id'], target_id=edge['target_id'],
                relationship=edge['relationship'], path=edge['path'], line=edge['line'],
                start_byte=edge['start_byte'], end_byte=edge['end_byte'], record_id=edge['id']))
    if omitted_nodes or semantic.get('omittedEvents'):
        limitations.append(f'{side}: omitted {omitted_nodes} relationships due to record/endpoint budget '
                           f'and {semantic.get("omittedEvents", 0)} semantic events due to resolver budget')
    if semantic['diagnostics']:
        limitations.append(f'{side}: semantic resolver reported {len(semantic["diagnostics"])} diagnostics')
    if omitted:
        limitations.append(f'{side}: record budget omitted {omitted} file/definition records')
    package = EvidencePackage(package_id=pid, query_id=f'change:{snapshot.id}', execution_id=pid,
        repository_id=snapshot.repository_id, snapshot_id=sid, question=None,
        limits=EvidenceLimits(max_records=limit, max_package_bytes=1048576), evidence=records, findings=[], requirements=[], operations=[],
        requirement_evidence={}, citations=citations, conflicts=[], missing_evidence=[], clarifications=[],
        omissions=[], resource_limits=['record_budget'] if omitted or omitted_nodes else [], status='partial',
        validation_status='partial', limitations=['Only uniquely resolved captured calls/imports/exports are graph relationships.'])
    return VersionEvidence(version=side, package=package, manifest=manifest, symbols=symbols,
                           relationships=relationships,
                           unresolved=[f'{path}:{line}:{kind}:{reason}' for path, line, kind, reason in unresolved],
                           omitted_relationships=omitted_nodes + semantic.get('omittedEvents', 0),
                           omitted_records=omitted, limitations=limitations)


def build_change_evidence(snapshot, *, max_records_per_version=200):
    """Accept a store-owned atomic capture; never touch live source or index locks."""
    if snapshot.consistency != 'atomic_apfs' or not snapshot.atomic_snapshot_id:
        raise CaptureError('atomic_capture_required', 'Change evidence requires a verified atomic capture')
    if snapshot.conflicts or not snapshot.definitive:
        raise CaptureError('merge_conflicts', 'Resolve merge conflicts before definitive change analysis')
    if type(max_records_per_version) is not int or not 1 <= max_records_per_version <= 200:
        raise ValueError('Evidence record limit must be between 1 and 200')
    before = _version(snapshot, 'before', max_records_per_version)
    after = _version(snapshot, 'after', max_records_per_version)
    mappings = []
    renames = {c.path: c.rename_candidates for c in snapshot.changes if c.rename_candidates}
    for old in before.symbols:
        candidates = [new for new in after.symbols if
                      (old.kind, old.qualified_name) == (new.kind, new.qualified_name)
                      and (old.path == new.path or old.path in renames.get(new.path, ()))]
        ids = sorted(new.symbol_id for new in candidates)
        state = 'unmatched' if not ids else 'matched' if len(ids) == 1 else 'ambiguous'
        basis = ('unresolved' if not ids else 'declaration_key' if all(new.path == old.path for new in candidates)
                 else 'exact_file_rename')
        mappings.append(SymbolCorrespondence(before_id=old.symbol_id, after_candidates=ids, state=state, basis=basis))
    return ChangeEvidencePackage(capture_id=snapshot.id, capture_digest=snapshot.digest,
        repository_id=snapshot.repository_id, atomic_snapshot_id=snapshot.atomic_snapshot_id,
        before=before, after=after, correspondence=mappings,
        adapter_versions=dict(_ADAPTER_VERSIONS),
        limitations=['Phase 2 progress: resolved captured calls/imports/exports; caller traversal and framework adapters pending.',
                     'Unmatched symbols may be removed, renamed or omitted; unmatched does not prove deletion.',
                     'Declaration correspondence is structural association, not proof of equivalent behavior.',
                     'Snapshot evidence is historical; current workspace freshness is not implied.'])


def validate_against_capture(package, snapshot):
    """Validate source ownership at consumption, including deleted baseline citations."""
    checked = ChangeEvidencePackage.model_validate(package.model_dump())
    if (checked.capture_id, checked.capture_digest, checked.repository_id, checked.atomic_snapshot_id) != (
            snapshot.id, snapshot.digest, snapshot.repository_id, snapshot.atomic_snapshot_id):
        raise ValueError('Change evidence belongs to a different capture')
    for side in ('before', 'after'):
        sources = {v.path: v for v in getattr(snapshot, side) if v.data is not None}
        expected = {path: sha256(v.data).hexdigest() for path, v in sources.items()}
        value = getattr(checked, side)
        if value.manifest != expected:
            raise ValueError('Version manifest differs from captured source')
        parsed = {}
        for record in value.package.evidence.values():
            proof = record.proof
            source = sources[proof.file_path]
            lines = max(1, len(source.data.splitlines()))
            if proof.evidence_type == 'source_range':
                expected = (file_id(snapshot.repository_id, source.path), None, 1, lines, 'not_applicable')
            elif proof.evidence_type == 'symbol_definition':
                if source.path not in parsed:
                    syntax = parse_source(source.data, suffix=PurePosixPath(source.path).suffix.lower(),
                                          repository_id=snapshot.repository_id, relative_path=source.path)
                    parsed[source.path] = {s['id']: s for s in syntax.symbols}
                symbol = parsed[source.path].get(proof.symbol_id)
                if symbol is None:
                    raise ValueError('Symbol is absent from captured source')
                expected = (symbol['id'], symbol['id'], symbol['start_line'], min(lines, symbol['end_line']), 'syntax_only')
            else:
                raise ValueError('Source-only adapter cannot establish graph relationships')
            if (proof.evidence_id, proof.symbol_id, proof.start_line, proof.end_line, proof.resolution) != expected:
                raise ValueError('Citation differs from canonical captured source evidence')
    return checked
