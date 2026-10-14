"""Read-only preview/citation verification bridge. Never runs engines or models."""

import argparse
import json
import sys

from knowledge_base.identity import repository_id
from knowledge_base.synchronization import SnapshotStore
from query_system.evidence_aggregation import EvidenceAggregator, FAILURES
from query_system.executor import MultiEngineExecutor
from query_system.presentation_contracts import PreviewInput
from query_system.response_presentation import format_response

MAX_INPUT_BYTES = 3 * 1024 * 1024


def _scope_unavailable(view, state, reason):
    for source in view.sources.values():
        source.state, source.reason = state, reason
    for item in view.items.values():
        item.authoritative = False
    for requirement in view.requirements:
        requirement.state = "stale" if state == "stale" else "error"
        requirement.limitations.append(reason)
    view.follow_ups = []
    view.status = "stale_index" if state == "stale" else "error"
    view.limitations.append(reason)
    return view


def verify_preview(store, packet: PreviewInput, reference=None):
    """Recheck a supplied backend result, not retrieve or regenerate an answer.

    Exported JSON is not authenticated. Fresh verification grants authority only
    to canonical source navigation, not arbitrary supplied model/rule prose.
    """
    packet = PreviewInput.model_validate(packet.model_dump(), strict=True)
    package = packet.package
    view = format_response(package, packet.generation, depth=packet.depth)
    if store.repo_id != package.repository_id:
        return _scope_unavailable(view, "unauthorized", "This packet belongs to a different authorized repository."), None
    if package.validation_status in ("invalid", "stale"):
        return _scope_unavailable(view, "unavailable", "The supplied evidence package was not accepted."), None
    try:
        with MultiEngineExecutor.pinned(store) as executor:
            if executor.context.snapshot_id != package.snapshot_id or not store.matches(package.snapshot_id):
                return _scope_unavailable(view, "stale", "Published generation/source changed; reopen a current backend packet."), None
            verifier = EvidenceAggregator.from_executor(executor)
            infos, bad = {}, set()
            for key, record in package.evidence.items():
                try:
                    infos[record.proof.evidence_id] = verifier.verify(record.proof)
                except FAILURES:
                    bad.add(key)
            rejected = set()
            for finding in package.findings:
                if set(finding.evidence_record_ids) & bad:
                    rejected.add(finding.finding_id)
                    continue
                try:
                    operation = next(item for item in package.operations if item.operation_id == finding.operation_id)
                    verifier._finding(finding.original, operation, infos)
                except FAILURES:
                    rejected.add(finding.finding_id)
            for source in view.sources.values():
                if source.citation.record_id in bad:
                    source.state, source.reason = "unavailable", "Canonical source provenance does not match this packet."
                else:
                    source.state, source.reason = "verified", "Canonical provenance and saved bytes verified; recheck before opening."
            rejected_items = set(rejected)
            if packet.generation:
                for section in packet.generation.sections:
                    for claim in section.claims:
                        if set(claim.record_ids) & bad or claim.finding_id in rejected:
                            rejected_items.add(claim.claim_id)
            for key in rejected_items:
                view.items.pop(key, None)
            for requirement in view.requirements:
                touched = False
                for section in requirement.sections:
                    touched |= bool(set(section.item_ids) & rejected_items)
                    section.item_ids = [key for key in section.item_ids if key not in rejected_items]
                if touched:
                    requirement.state = "partially_satisfied" if any(section.item_ids for section in requirement.sections) else "error"
                    requirement.limitations.append("Invalid supplied findings excluded after canonical verification.")
            if rejected or bad:
                view.status = "partial" if view.items else "error"
                view.limitations.append("Invalid supplied source/finding data was excluded; independent verified branches remain available.")
            view.follow_ups = [item for item in view.follow_ups if next(req for req in view.requirements if req.requirement_id == item.requirement_id).state not in ("error", "stale")
                and (item.citation_id is None or view.sources[item.citation_id].state == "verified")]
            if not store.matches(package.snapshot_id):
                return _scope_unavailable(view, "stale", "Source changed during preview verification."), None
            source = view.sources.get(reference)
            approved = source.citation if source and source.state == "verified" else None
            # Validate our projection again after exclusions/status changes.
            return type(view).model_validate(view.model_dump(), strict=True), approved
    except (RuntimeError, *FAILURES):
        return _scope_unavailable(view, "stale", "The current pinned knowledge snapshot is stale or unavailable."), None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--reference")
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("Preview packet is too large")
        packet = PreviewInput.model_validate_json(raw)
        store = SnapshotStore(args.root, repository_id(args.root), args.data_dir)
        view, source = verify_preview(store, packet, args.reference)
        print(json.dumps({"presentation": view.model_dump(mode="json"), "source": source.model_dump(mode="json") if source else None}, ensure_ascii=False))
    except (RuntimeError, *FAILURES):
        # Do not echo untrusted packet text, filesystem paths or exception secrets.
        print(json.dumps({"error": "Preview packet rejected or verification unavailable; no navigation is authorized."}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
