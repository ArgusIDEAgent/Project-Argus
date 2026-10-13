"""Pure classification and package shape invariants; not a provenance authority."""

import unittest

from pydantic import ValidationError

from query_system.contracts import GraphFinding, IntentRequirement, RuleFinding, SemanticFinding
from query_system.evidence_aggregation import classify, stable_id
from query_system.evidence_contracts import (
    Citation, EvidenceLimits, EvidencePackage, EvidenceRecord, EvidenceUse, OperationCoverage,
    RequirementCoverage, SupportedFinding,
)
import query_system.test_execution as planning


class EvidenceContractTests(unittest.TestCase):
    def setUp(self):
        source = planning.PlanningTests()
        source.setUp()
        requirement = IntentRequirement(intent="SYMBOL_LOCATION", order=0, entity_indices=[0])
        plan = source.query([requirement])
        operation = planning.build_plan(plan, source.context).operations[0]
        proof = source.entities[0].evidence
        rid, fid, pid = stable_id(proof.model_dump(mode="json")), "claim-1", "a" * 32
        reference = f"cm7:{pid}:{rid}"
        use = EvidenceUse(operation_id=operation.operation_id, requirement_id=operation.requirement_id, engine="graph", finding_id=fid, role="claim")
        record = EvidenceRecord(record_id=rid, proof=proof, provenance={"kind": "symbol"}, verification="source_verified", context_status="not_requested", uses=[use])
        original = GraphFinding(relationship="DEFINITION", entity_id=proof.evidence_id, evidence_ids=[proof.evidence_id])
        finding = SupportedFinding(finding_id=fid, operation_id=operation.operation_id, requirement_id=operation.requirement_id,
            engine="graph", original=original, classification="verified_source_observation", authoritative=True,
            claim_scope="indexed_declaration", evidence_record_ids=[rid], citation_ids=[reference])
        coverage = dict(execution_status="success", state="satisfied", finding_ids=[fid], evidence_record_ids=[rid], missing_evidence=[], limitations=[])
        self.package = EvidencePackage(package_id=pid, query_id="query-1", execution_id="b" * 32,
            repository_id=proof.repository_id, snapshot_id=proof.snapshot_id, question="Where is login?", limits=EvidenceLimits(),
            evidence={rid: record}, findings=[finding],
            requirements=[RequirementCoverage(requirement_id=operation.requirement_id, original=requirement,
                operation_ids=[operation.operation_id], clarification_required=False, truncated=False, **coverage)],
            operations=[OperationCoverage(operation_id=operation.operation_id, requirement_id=operation.requirement_id, request=operation.request, **coverage)],
            requirement_evidence={operation.requirement_id: [rid]}, citations={reference: Citation(reference=reference, record_id=rid, source=proof)},
            conflicts=[], missing_evidence=[], clarifications=[], omissions=[], resource_limits=[], status="success", validation_status="valid", limitations=[])

    def test_claim_classes_preserve_semantic_hypothetical_and_unresolved_limits(self):
        cases = [
            (SemanticFinding(chunk_id="chunk", rank=1, evidence_ids=["chunk"]), "verified_source_observation"),
            (GraphFinding(relationship="CALLS", source_node_id="a", target_node_id="b", evidence_ids=["edge"]), "verified_graph_relationship"),
            (GraphFinding(relationship="CALLS", source_node_id="a", target_node_id="b", path_node_ids=["a", "b"], path_names=["a", "b"], evidence_ids=["edge"]), "derived_analysis"),
            (RuleFinding(statement="Possible impact", classification="hypothesis", evidence_ids=["edge"]), "hypothetical_consequence"),
            (RuleFinding(statement="Unknown target", classification="verified_observation", category="unresolved_relationship", evidence_ids=["edge"]), "unresolved_assertion"),
            (RuleFinding(statement="Computed path", classification="derived_finding", evidence_ids=["edge"]), "derived_analysis"),
        ]
        for finding, expected in cases:
            self.assertEqual(classify(finding)[0], expected)

    def test_citations_are_package_scoped_and_cannot_change_source_locations(self):
        for change in ("reference", "range", "missing"):
            body = self.package.model_dump()
            reference = next(iter(body["citations"]))
            if change == "reference":
                body["citations"][reference]["reference"] = "model-generated-1"
            elif change == "range":
                body["citations"][reference]["source"]["end_line"] += 1
            else:
                body["citations"] = {}
            with self.subTest(change=change), self.assertRaises(ValidationError):
                EvidencePackage.model_validate(body)

    def test_no_dangling_support_or_hypothesis_promotion(self):
        for change in ("proof", "ownership", "hypothesis"):
            body = self.package.model_dump()
            if change == "proof":
                body["findings"][0]["original"]["evidence_ids"] = ["different-canonical-proof"]
            elif change == "ownership":
                next(iter(body["evidence"].values()))["uses"][0]["requirement_id"] = "missing"
            else:
                body["findings"][0]["classification"] = "hypothetical_consequence"
            with self.subTest(change=change), self.assertRaises(ValidationError):
                EvidencePackage.model_validate(body)

    def test_strict_limits_and_serialized_size(self):
        for values in ({"max_records": 0}, {"max_records": "2"}, {"max_context_bytes": -1}, {"max_package_bytes": 1}, {"max_relationship_items": 201}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                EvidenceLimits(**values)
        body = self.package.model_dump()
        body["limits"]["max_package_bytes"] = 4096
        body["question"] = "x" * 5000
        with self.assertRaises(ValidationError):
            EvidencePackage.model_validate(body)

    def test_structural_sources_have_no_fabricated_citation(self):
        body = self.package.model_dump()
        body["citations"] = {}
        body["findings"][0]["citation_ids"] = []
        record = next(iter(body["evidence"].values()))
        for field in ("file_id", "file_path", "start_line", "end_line", "source_hash"):
            record["proof"][field] = None
        record["verification"] = "structural_metadata_verified"
        record["context_status"] = "structural"
        package = EvidencePackage.model_validate(body)
        self.assertFalse(package.citations)

    def test_ids_and_roundtrip_are_stable(self):
        self.assertEqual(stable_id({"a": 1, "b": 2}), stable_id({"b": 2, "a": 1}))
        self.assertEqual(EvidencePackage.model_validate_json(self.package.model_dump_json()), self.package)


if __name__ == "__main__":
    unittest.main()
