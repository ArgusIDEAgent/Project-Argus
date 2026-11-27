"""Deterministic presentation from retained validated evidence; no model required."""


def explain(snapshot, analysis, mode='simple'):
    if mode not in ('simple', 'detailed', 'flow'):
        raise ValueError('Unsupported presentation mode')
    evidence = analysis.evidence
    sections = []
    changes = [c for c in snapshot.changes if c.status != 'unanalyzed']
    sections.append({'title': 'Saved changes', 'text': f'{len(changes)} changed files against captured HEAD. Only saved files are included.',
                     'items': [{'text': f'{c.status}: {c.path}', 'reference': None} for c in changes[:100]]})
    sources = {}
    versions = [('before', evidence.before), ('after', evidence.after)]
    for side, version in versions:
        for key, citation in version.package.citations.items():
            proof = citation.source
            sources[key] = {'version': side, 'path': proof.file_path, 'start_line': proof.start_line,
                            'end_line': proof.end_line, 'record_id': citation.record_id}
        items = []
        for symbol in version.symbols:
            if symbol.changed:
                reference = next((key for key, c in version.package.citations.items() if c.record_id == symbol.record_id), None)
                items.append({'text': f'{symbol.qualified_name} — {symbol.path}:{symbol.start_line}', 'reference': reference})
        sections.append({'title': f'{side.capitalize()} declarations', 'text': 'Declarations intersecting saved change ranges.', 'items': items[:100]})
        if mode in ('detailed', 'flow'):
            labels = {s.symbol_id: s.qualified_name for s in version.symbols}
            labels.update({r.proof.file_id: r.proof.file_path for r in version.package.evidence.values() if r.proof.file_path})
            items = []
            for edge in version.relationships:
                reference = next(key for key, c in version.package.citations.items() if c.record_id == edge.record_id)
                items.append({'text': f'{labels.get(edge.source_id, edge.source_id)} {edge.relationship.lower()} {labels.get(edge.target_id, edge.target_id)}', 'reference': reference})
            sections.append({'title': f'{side.capitalize()} structural flow', 'text': 'Source-resolved relationships; runtime reach is unverified.', 'items': items[:200]})
    items = []
    for path in analysis.impact.caller_paths:
        version = evidence.before if path.version == 'before' else evidence.after
        names = {s.symbol_id: s.qualified_name for s in version.symbols}
        reference = next((key for key, c in version.package.citations.items() if c.record_id == path.proof_record_ids[-1]), None)
        items.append({'text': path.version + ': ' + ' → '.join(names.get(i, i) for i in reversed([path.origin_id, *path.caller_ids])), 'reference': reference})
    if mode == 'detailed':
        symbols = {s.symbol_id: s for s in evidence.before.symbols}
        mapping_items = []
        for mapping in evidence.correspondence:
            symbol = symbols[mapping.before_id]
            if not symbol.changed and mapping.state == 'matched':
                continue
            reference = next((key for key, c in evidence.before.package.citations.items() if c.record_id == symbol.record_id), None)
            mapping_items.append({'text': f'{symbol.qualified_name}: {mapping.state}, {len(mapping.after_candidates)} candidates ({mapping.basis}).', 'reference': reference})
        sections.append({'title': 'Symbol correspondence', 'text': 'Structural matches do not prove equivalent behavior.', 'items': mapping_items[:200]})
        delta_items = []
        for change in analysis.impact.relationship_changes:
            version = evidence.before if change.version == 'before' else evidence.after
            reference = next((key for key, c in version.package.citations.items() if c.record_id == change.proof_record_id), None)
            delta_items.append({'text': f'{change.version}: {change.status} {change.relationship.lower()} relationship.', 'reference': reference})
        sections.append({'title': 'Relationship changes', 'text': f'{analysis.impact.uncertain_relationships} relationships remain uncertain. Absence from incomplete evidence is not proof of removal.', 'items': delta_items})
    sections.append({'title': 'Possible caller impact', 'text': f'Analysis completeness: {analysis.impact.completeness}. Static callers may be affected; no tests have run.', 'items': items})
    limitations = list(snapshot.notices) + list(evidence.limitations) + list(analysis.impact.limitations)
    for side, version in versions:
        limitations += version.limitations
        limitations.append(f'{side}: {len(version.unresolved)} unresolved references, {version.omitted_records} omitted source records, {version.omitted_relationships} omitted relationships.')
    if len(changes) > 100:
        limitations.append(f'Changed file list omitted {len(changes) - 100} files.')
    limitations.append(f'Caller paths omitted: {analysis.impact.omitted_caller_paths}; depth frontiers: {analysis.impact.depth_limited_frontiers}.')
    if len(limitations) > 250:
        limitations = limitations[:249] + [f'{len(limitations) - 249} further limitation entries omitted.']
    return {'capture_id': snapshot.id, 'mode': mode, 'sections': sections, 'sources': sources,
            'limitations': limitations[:250], 'model_status': 'disabled_deterministic', 'freshness': 'historical_capture'}
