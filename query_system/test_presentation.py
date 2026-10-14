"""Pure display tests require no model, index, database or VS Code process."""

import copy
import unittest
from unittest.mock import patch

from knowledge_base.identity import file_id
from query_system.contracts import Evidence, GraphFinding, IntentRequirement, OperationRequest, QueryEntity, RuleFinding, SemanticFinding
from query_system.evidence_contracts import (
    Citation, EvidenceLimits, EvidencePackage, EvidenceRecord, EvidenceUse, OperationCoverage,
    RequirementCoverage, RequirementTarget, SupportedFinding,
)
from query_system.generation_contracts import (
    AnswerClaim, AnswerCoverage, AnswerSection, GenerationResult, PreparedContext, PreparedSource,
)
from query_system.presentation_contracts import PresentationResult
from query_system.response_presentation import TEMPLATES, format_response


def package(intent="SYMBOL_LOCATION", kind="graph"):
    repo, snapshot, rid = "a" * 24, "b" * 32, "req-000"
    proof = Evidence(evidence_id="symbol", repository_id=repo, snapshot_id=snapshot, index_snapshot_id="symbols-stage",
        evidence_type="code_chunk" if kind == "semantic" else "symbol_definition", file_id=file_id(repo, "login.ts"),
        symbol_id="symbol", file_path="login.ts", start_line=1, end_line=2, source_hash="c" * 64,
        resolution="syntax_only", source_origin="disk", schema_version=1)
    if kind == "semantic":
        original = SemanticFinding(chunk_id="symbol", rank=1, retrieval_methods=["semantic", "keyword"], name="login", evidence_ids=["symbol"])
        engine, operation, classification, authoritative = "semantic", "hybrid_search", "verified_source_observation", True
    elif kind == "rule":
        hypothetical = intent == "CHANGE_IMPACT"
        original = RuleFinding(statement="Possible review location" if hypothetical else "Indexed function declaration", rule_id="change_impact" if hypothetical else "function_contract",
            classification="hypothesis" if hypothetical else "verified_observation", category="possible_impact" if hypothetical else "function_contract",
            details={"parameters": [{"name": "token", "type": "string"}], "return_behavior": "not_inferred"}, evidence_ids=["symbol"])
        engine, operation, classification, authoritative = "rules", original.rule_id, "hypothetical_consequence" if hypothetical else "verified_source_observation", not hypothetical
    else:
        original = GraphFinding(entity_id="symbol", entity_name="login", target_symbol_id="symbol", relationship="DEFINITION", evidence_ids=["symbol"])
        engine, operation, classification, authoritative = "graph", "find_definition", "verified_source_observation", True
    finding = SupportedFinding(finding_id="finding", operation_id="op", requirement_id=rid, engine=engine, original=original,
        classification=classification, authoritative=authoritative, claim_scope="static", evidence_record_ids=["record"], citation_ids=["cm7:package:record"], limitations=["producer limitation"])
    requirement = IntentRequirement(intent=intent, order=0, entity_indices=[0])
    target = QueryEntity(kind="function", name="login", resolution="resolved", symbol_id="symbol", entity_id="symbol", file_id=proof.file_id, file_path=proof.file_path)
    record = EvidenceRecord(record_id="record", proof=proof, provenance={"kind": "symbol"}, verification="source_verified", context_status="not_requested",
        uses=[EvidenceUse(operation_id="op", requirement_id=rid, engine=engine, finding_id="finding", role="claim")])
    operation_coverage = OperationCoverage(operation_id="op", requirement_id=rid,
        request=OperationRequest(engine=engine, name=operation, repository_id=repo, snapshot_id=snapshot), execution_status="success", state="satisfied",
        finding_ids=["finding"], evidence_record_ids=["record"], missing_evidence=[], limitations=[])
    coverage = RequirementCoverage(requirement_id=rid, original=requirement, execution_status="success", state="satisfied", operation_ids=["op"],
        finding_ids=["finding"], evidence_record_ids=["record"], missing_evidence=[], limitations=[], clarification_required=False, truncated=False,
        targets=[RequirementTarget(entity_index=0, reference=target)])
    return EvidencePackage(package_id="package", query_id="support-query", execution_id="execution", repository_id=repo, snapshot_id=snapshot, question="Where is login?",
        limits=EvidenceLimits(), evidence={"record": record}, findings=[finding], requirements=[coverage], operations=[operation_coverage],
        requirement_evidence={rid: ["record"]}, citations={"cm7:package:record": Citation(reference="cm7:package:record", record_id="record", source=proof)},
        conflicts=[], missing_evidence=[], clarifications=[], omissions=[], resource_limits=[], status="success", validation_status="valid", limitations=["static evidence only"])


def generated(packet):
    original = IntentRequirement(intent="FUNCTION_EXPLANATION", order=0, entity_indices=[0])
    claim = AnswerClaim(claim_id="claim", requirement_orders=[0], classification="model_interpretation", authoritative=False,
        text="<script>alert('source')</script> A supplied explanation.", finding_id=None, fact_id=None, record_ids=["record"],
        citation_ids=["cm7:package:record"], verification="citation_linked_not_fact_checked")
    context = PreparedContext(context_id="context", package_id=packet.package_id,
        sources=[PreparedSource(record_id="record", data={}, excerpt="source")], facts=[], omissions=[], gaps={0: []}, core_ready={0: True}, messages=[], input_bytes=0, source_bytes=6)
    return GenerationResult(generation_id="generated", package_id=packet.package_id, query_id="original-query", support_query_id=packet.query_id,
        repository_id=packet.repository_id, snapshot_id=packet.snapshot_id, explanation_type="function", sections=[AnswerSection(title="explanation", claims=[claim])],
        coverage=[AnswerCoverage(order=0, original=original, supporting_requirement_ids=["req-000"], state="addressed", claim_ids=["claim"], limitations=[])],
        citations=packet.citations, unsupported_assertions=[], status="success", model_status="success", fallback_status="not_used", failure_category=None,
        context=context, model_calls=1, preparation_ms=0., model_ms=0., total_ms=0., limitations=["model prose not fact-checked"])


class PresentationTests(unittest.TestCase):
    def test_all_twelve_intent_templates_have_explicit_headings(self):
        self.assertEqual(len(TEMPLATES), 13)  # Callers/callees share one requested template family.
        for intent, title in TEMPLATES.items():
            with self.subTest(intent=intent):
                result = format_response(package(intent))
                self.assertEqual(result.requirements[0].title, title)
                self.assertEqual(result.requirements[0].original.intent, intent)

    def test_depth_changes_details_not_classification_or_original_coverage(self):
        packet = package("FUNCTION_CONTRACT", "rule")
        views = [format_response(packet, depth=depth) for depth in ("quick", "standard", "deep")]
        self.assertFalse(views[0].items["finding"].details)
        self.assertTrue(views[1].items["finding"].details)
        self.assertTrue(views[2].items["finding"].details)
        self.assertEqual({view.items["finding"].classification for view in views}, {"verified_source_observation"})
        self.assertEqual({view.requirements[0].state for view in views}, {"satisfied"})
        self.assertTrue(all("producer limitation" in view.requirements[0].limitations for view in views))

    def test_citations_reuse_original_proofs_and_are_not_clickable_without_verification(self):
        packet = package()
        result = format_response(packet)
        self.assertEqual(result.sources["cm7:package:record"].citation, packet.citations["cm7:package:record"])
        self.assertEqual(result.sources["cm7:package:record"].state, "requires_verification")
        self.assertEqual(result.items["finding"].citation_ids, ["cm7:package:record"])

    def test_hypothesis_and_similarity_never_become_dependencies(self):
        impact = format_response(package("CHANGE_IMPACT", "rule"))
        self.assertFalse(impact.items["finding"].authoritative)
        self.assertEqual(impact.items["finding"].classification, "hypothetical_consequence")
        semantic = format_response(package("FEATURE_DISCOVERY", "semantic"))
        self.assertIn("not a dependency", semantic.items["finding"].text)
        self.assertNotIn("CALLS", semantic.items["finding"].text)

    def test_generation_text_is_never_modified_or_promoted(self):
        packet = package()
        result = generated(packet)
        for depth in ("quick", "standard", "deep"):
            view = format_response(packet, result, depth=depth)
            self.assertEqual(view.items["claim"].text, result.sections[0].claims[0].text)
            self.assertFalse(view.items["claim"].authoritative)
            self.assertEqual(view.query_id, "original-query")
            self.assertEqual(view.support_query_id, "support-query")

    def test_unknown_or_foreign_generated_proofs_are_rejected(self):
        for change in ("repository", "citation", "context", "support"):
            packet = package(); result = generated(packet).model_copy(deep=True)
            if change == "repository": result.repository_id = "f" * 24
            elif change == "citation": result.citations["cm7:package:record"].source.source_hash = "f" * 64
            elif change == "context": result.context.sources = []
            else: result.coverage[0].supporting_requirement_ids = ["unknown"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                format_response(packet, result)

    def test_fallback_and_truncation_cannot_appear_complete(self):
        packet = package(); packet.requirements[0].truncated = True
        packet.requirements[0].state = "partially_satisfied"
        view = format_response(packet, depth="quick")
        self.assertEqual(view.status, "partial")
        self.assertIn("truncated", " ".join(view.requirements[0].limitations))
        result = generated(packet); result.model_status = "unavailable"; result.fallback_status = "evidence_summary"
        result.failure_category = "offline"; result.coverage[0].state = "partial"
        view = format_response(packet, result)
        self.assertEqual(view.status, "partial")
        self.assertEqual(view.model_status, "unavailable")
        self.assertIn("Generation failure: offline", view.limitations)

    def test_invalid_depth_and_extra_fields_are_rejected(self):
        with self.assertRaises(ValueError): format_response(package(), depth="full")
        view = format_response(package())
        raw = view.model_dump(); raw["commands"] = ["execute"]
        with self.assertRaises(ValueError): PresentationResult.model_validate(raw)

    def test_repeat_formatting_is_deterministic_and_does_not_mutate_input(self):
        packet = package(); before = copy.deepcopy(packet.model_dump())
        first, second = format_response(packet), format_response(packet)
        self.assertEqual(first.model_dump_json(), second.model_dump_json())
        self.assertEqual(packet.model_dump(), before)
        self.assertEqual(PresentationResult.model_validate_json(first.model_dump_json()), first)

    def test_pure_formatter_has_no_model_retrieval_or_database_access(self):
        with patch("query_system.local_ollama.LocalOllama.generate", side_effect=AssertionError("LLM")), patch("query_system.executor.MultiEngineExecutor.execute", side_effect=AssertionError("retrieval")), patch("sqlite3.connect", side_effect=AssertionError("database")), patch("pathlib.Path.read_bytes", side_effect=AssertionError("source read")):
            self.assertTrue(format_response(package()).items)

    def test_follow_ups_are_existing_copy_only_capabilities(self):
        view = format_response(package())
        suggestions = [item for item in view.follow_ups if item.mode == "copy_query"]
        self.assertTrue(suggestions)
        self.assertTrue(all(item.target_id == "symbol" for item in suggestions))
        self.assertTrue(any(item.operation == "dependency_summary" for item in suggestions))
        self.assertTrue(all("login.ts" in item.prompt for item in suggestions))
        packet = package(); packet.requirements[0].state = "unsupported"
        self.assertFalse(format_response(packet).follow_ups)


if __name__ == "__main__":
    unittest.main()
