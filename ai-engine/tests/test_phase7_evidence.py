"""Real Phase 6 -> Phase 7 integration in the same published reader pin."""

import asyncio
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import test_phase6_execution as phase6
from query_system.contracts import GraphFinding, IntentRequirement, QueryRequest
from query_system.evidence_aggregation import EvidenceAggregator
from query_system.evidence_contracts import EvidenceBudgetError, EvidenceLimits, EvidencePackage


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.owner = phase6.ExecutionTests()
        self.owner.setUp()
        self.addCleanup(self.owner.doCleanups)

    def execute(self, executor, question):
        return asyncio.run(executor.execute(executor.plan(executor.understand(self.owner.request(question)))))

    def run_package(self, question, limits=None, factory=None, mutate=None, source_filter=None):
        with self.owner.session(factory) as executor:
            report = self.execute(executor, question)
            if mutate:
                report = mutate(report)
            return EvidenceAggregator.from_executor(executor, source_filter=source_filter).aggregate(report, limits)

    def definitions(self, executor):
        query = self.owner.typed_requirements(executor, ["SYMBOL_LOCATION"] * 2, [[0], [1]])
        return asyncio.run(executor.execute(executor.plan(query)))

    def test_real_three_engine_package_one_pin_and_no_llm(self):
        with patch.object(self.owner.store, "pin_view", wraps=self.owner.store.pin_view) as pins, self.owner.session() as executor, patch("answers.answer_question", side_effect=AssertionError("LLM")), patch("answers.urlopen", side_effect=AssertionError("Ollama")):
            query = self.owner.typed_requirements(executor, ["DIRECT_CALLERS", "FEATURE_DISCOVERY", "FUNCTION_CONTRACT"], [[0], [], [0]])
            report = asyncio.run(executor.execute(executor.plan(query)))
            aggregator = EvidenceAggregator.from_executor(executor)
            package = aggregator.aggregate(report)
            self.assertEqual({item.engine for item in package.findings}, {"graph", "semantic", "rules"})
            self.assertFalse(package.conflicts)
            self.assertEqual(package.validation_status, "valid")
            self.assertEqual(len(package.requirements), 3)
            for record in package.evidence.values():
                aggregator.verify(record.proof)
            self.assertEqual(EvidencePackage.model_validate_json(package.model_dump_json()), package)
        self.assertEqual(pins.call_count, 1)

    def test_duplicate_source_supports_two_distinct_requirements(self):
        with self.owner.session() as executor:
            query = self.owner.typed_requirements(executor, ["SYMBOL_LOCATION"] * 2, [[0], [0]])
            report = asyncio.run(executor.execute(executor.plan(query)))
            package = EvidenceAggregator.from_executor(executor).aggregate(report)
        self.assertEqual(len(package.evidence), 1)
        self.assertEqual(len(package.findings), 2)
        self.assertEqual(package.requirements[0].evidence_record_ids, package.requirements[1].evidence_record_ids)
        record = next(iter(package.evidence.values()))
        self.assertEqual(len({use.requirement_id for use in record.uses}), 2)
        self.assertEqual(len({use.finding_id for use in record.uses if use.role == "claim"}), 2)

    def test_same_name_in_different_files_is_not_merged(self):
        with self.owner.session() as executor:
            query = executor.understand(QueryRequest(repository_id=self.owner.repo, question="Where is sharedName defined?", active_file="auth.ts"))
            second = executor.understand(QueryRequest(repository_id=self.owner.repo, question="Where is sharedName defined?", active_file="other.ts"))
            query.entities = [query.entities[0], second.entities[0]]
            query.requirements = [IntentRequirement(intent="SYMBOL_LOCATION", order=index, entity_indices=[index]) for index in range(2)]
            report = asyncio.run(executor.execute(executor.plan(query)))
            package = EvidenceAggregator.from_executor(executor).aggregate(report)
        self.assertEqual(len(package.evidence), 2)
        self.assertEqual({record.proof.file_path for record in package.evidence.values()}, {"auth.ts", "other.ts"})
        self.assertEqual(len({record.proof.symbol_id for record in package.evidence.values()}), 2)
        self.assertEqual({item.targets[0].reference.file_path for item in package.requirements}, {"auth.ts", "other.ts"})
        self.assertTrue(all(item.targets[0].validation == "input_only" and item.targets[0].reference.evidence is None for item in package.requirements))

    def test_legitimate_stage_differences_are_not_mixed_generations(self):
        package = self.run_package("Who calls login and what does entry call?")
        self.assertEqual({record.proof.snapshot_id for record in package.evidence.values()}, {package.snapshot_id})
        self.assertGreater(len({record.proof.index_snapshot_id for record in package.evidence.values()}), 1)
        self.assertFalse(package.conflicts)

    def test_identical_source_text_in_two_files_remains_distinct(self):
        root = self.owner.fixture.root
        for directory in ("left", "right"):
            path = root / directory / "clone.ts"
            path.parent.mkdir()
            path.write_text("export function identical() { return 1; }\n")
        self.owner.fixture.publish()
        with self.owner.session() as executor:
            query = executor.understand(QueryRequest(repository_id=self.owner.repo, question="Where is identical defined?", active_file="left/clone.ts"))
            second = executor.understand(QueryRequest(repository_id=self.owner.repo, question="Where is identical defined?", active_file="right/clone.ts"))
            query.entities = [query.entities[0], second.entities[0]]
            query.requirements = [IntentRequirement(intent="SYMBOL_LOCATION", order=index, entity_indices=[index]) for index in range(2)]
            report = asyncio.run(executor.execute(executor.plan(query)))
            package = EvidenceAggregator.from_executor(executor).aggregate(report)
        self.assertEqual(len(package.evidence), 2)
        self.assertEqual(len({record.proof.source_hash for record in package.evidence.values()}), 1)
        self.assertEqual(len({record.proof.symbol_id for record in package.evidence.values()}), 2)

    def test_mixed_generation_or_repository_envelope_fails_closed(self):
        for changes in ({"snapshot_id": "f" * 32}, {"repository_id": "f" * 24}):
            package = self.run_package("Where is login defined?", mutate=lambda report: report.model_copy(update=changes))
            self.assertEqual(package.validation_status, "invalid")
            self.assertFalse(package.findings)
            self.assertFalse(package.citations)
            self.assertEqual(package.conflicts[0].code, "execution_mismatch")

    def test_bad_hash_range_or_symbol_identity_rejects_only_independent_branch(self):
        for field, value, expected in (("source_hash", "0" * 64, "source_hash"), ("start_line", 1, "source_range"),
                                        ("symbol_id", "not-canonical", "canonical_identity"), ("index_snapshot_id", "not-ready", "stage_mismatch")):
            with self.subTest(field=field), self.owner.session() as executor:
                report = self.definitions(executor)
                key = report.operations[0].result.findings[0].entity_id
                proof = report.evidence[key]
                updates = {field: value}
                if field == "start_line":
                    updates = {"start_line": proof.start_line + 100, "end_line": proof.end_line + 100}
                report.evidence[key] = proof.model_copy(update=updates)
                package = EvidenceAggregator.from_executor(executor).aggregate(report)
            self.assertEqual(package.requirements[0].state, "stale")
            self.assertEqual(package.requirements[1].state, "satisfied")
            self.assertEqual(len(package.findings), 1)
            self.assertIn(expected, {conflict.code for conflict in package.conflicts})

    def test_a_definition_proof_does_not_support_a_graph_dependency(self):
        def mutate(report):
            finding = report.operations[0].result.findings[0]
            report.operations[0].result.findings = [GraphFinding(relationship="CALLS", source_symbol_id=finding.target_symbol_id,
                target_symbol_id=finding.target_symbol_id, evidence_ids=finding.evidence_ids)]
            return report
        package = self.run_package("Where is login defined?", mutate=mutate)
        self.assertFalse(package.findings)
        self.assertIn("claim_provenance", {item.code for item in package.conflicts})

    def test_conflicting_claims_sharing_one_edge_are_both_quarantined(self):
        with self.owner.session() as executor:
            query = self.owner.typed_requirements(executor, ["DIRECT_CALLERS"] * 2, [[0], [0]])
            report = asyncio.run(executor.execute(executor.plan(query)))
            original = report.operations[1].result.findings[0]
            conflicting_keys = set(original.evidence_ids)
            report.operations[1].result.findings[0] = original.model_copy(update={"target_symbol_id": "contradictory-target"})
            package = EvidenceAggregator.from_executor(executor).aggregate(report)
        self.assertFalse(any(set(item.original.evidence_ids) & conflicting_keys for item in package.findings))
        self.assertTrue(package.conflicts)
        self.assertEqual(len(package.conflicts[0].requirement_ids), 2)
        self.assertTrue(all(item.state == "partially_satisfied" for item in package.requirements))

    def test_impact_hypotheses_are_not_verified_runtime_outcomes(self):
        package = self.run_package("Impact of changing login")
        hypothetical = [finding for finding in package.findings if finding.classification == "hypothetical_consequence"]
        self.assertTrue(hypothetical)
        self.assertTrue(all(not item.authoritative and item.original.classification == "hypothesis" for item in hypothetical))
        self.assertFalse(package.conflicts)

    def test_semantic_similarity_is_source_relevance_not_dependency_proof(self):
        package = self.run_package("Find code that validates a token")
        self.assertTrue(package.findings)
        self.assertTrue(all(item.engine == "semantic" and item.classification == "verified_source_observation" for item in package.findings))
        self.assertTrue(all("Semantic similarity" in " ".join(item.limitations) for item in package.findings))

    def test_unresolved_static_assertions_are_explicit_and_non_authoritative(self):
        package = self.run_package("Dependencies of invoke")
        unresolved = [item for item in package.findings if item.classification == "unresolved_assertion"]
        self.assertTrue(unresolved)
        self.assertTrue(all(not item.authoritative for item in unresolved))
        self.assertTrue(package.operations[0].unresolved_items)
        self.assertEqual(package.requirements[0].state, "partially_satisfied")

    def test_indexed_signature_conflict_is_not_treated_as_verified_source_fact(self):
        def mutate(report):
            finding = report.operations[0].result.findings[0]
            details = dict(finding.details)
            details["parameters"] = [{"name": "invented_parameter"}]
            report.operations[0].result.findings[0] = finding.model_copy(update={"details": details})
            return report
        package = self.run_package("Contract of login", mutate=mutate)
        self.assertFalse(package.findings)
        self.assertIn("claim_provenance", {item.code for item in package.conflicts})

    def test_semantic_clarification_retains_candidates_without_promoting_targets(self):
        package = self.run_package("Find code that validates a token and show its callers")
        self.assertEqual([item.state for item in package.requirements], ["satisfied", "ambiguous"])
        self.assertTrue(package.clarifications[0].candidates)
        self.assertEqual(len(package.operations), 1)
        self.assertTrue(all(item.engine == "semantic" for item in package.findings))

    def test_redacted_context_preserves_canonical_citation_ranges(self):
        import config
        import indexer
        source_filter = lambda text: config.sanitize_text(indexer._redact_secrets(text))
        package = self.run_package("Where is login defined?", EvidenceLimits(include_source_context=True), source_filter=source_filter)
        record = next(iter(package.evidence.values()))
        self.assertIn("export function login", record.source_context)
        self.assertEqual(record.context_status, "included")
        self.assertEqual(next(iter(package.citations.values())).source, record.proof)

    def test_unicode_separator_inside_string_does_not_shift_source_context(self):
        path = self.owner.fixture.root / "unicode.ts"
        path.write_text('const banner = "a\u2028b";\nexport function unicodeLines() { return 1; }\n')
        self.owner.fixture.publish()
        package = self.run_package("Where is unicodeLines defined?", EvidenceLimits(include_source_context=True), source_filter=lambda text: text)
        record = next(iter(package.evidence.values()))
        self.assertEqual((record.proof.start_line, record.proof.end_line), (2, 2))
        self.assertEqual(record.source_context, "export function unicodeLines() { return 1; }")

    def test_partial_execution_preserves_independent_findings(self):
        def factory(name, *args):
            engine = self.owner.native_factory(name, *args)
            original = engine.execute
            async def fail_one(operation, context):
                if operation.name == "find_callers":
                    raise RuntimeError("offline")
                return await original(operation, context)
            engine.execute = fail_one
            return engine
        package = self.run_package("Who calls login and what does entry call?", factory=factory)
        self.assertEqual(package.status, "partial")
        self.assertEqual([item.state for item in package.requirements], ["error", "satisfied"])
        self.assertTrue(package.findings)

    def test_failed_dependents_remain_skipped(self):
        package = self.run_package("Find login and show its callers", factory=lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual([item.state for item in package.requirements], ["error", "skipped"])
        self.assertEqual([item.state for item in package.operations], ["error", "skipped"])
        self.assertFalse(package.findings)

    def test_ambiguity_missing_and_deferred_coverage_survive(self):
        for question, state in (("Who calls sharedName?", "ambiguous"), ("Who calls missingFunction?", "not_found"),
                                ("Explain login", "deferred"), ("Who calls validate_token in legacy.py?", "unsupported")):
            with self.subTest(question=question):
                package = self.run_package(question)
                self.assertEqual(package.requirements[0].state, state)
                if state == "ambiguous":
                    self.assertEqual(len(package.clarifications[0].candidates), 2)
                    self.assertTrue(package.requirements[0].clarification_required)

    def test_citations_map_to_exact_validated_sources(self):
        with self.owner.session() as executor:
            report = self.definitions(executor)
            aggregator = EvidenceAggregator.from_executor(executor)
            package = aggregator.aggregate(report)
            for reference, citation in package.citations.items():
                record = package.evidence[citation.record_id]
                self.assertEqual(reference, f"cm7:{package.package_id}:{record.record_id}")
                self.assertEqual(citation.source, record.proof)
                info = aggregator.verify(citation.source)
                self.assertEqual((citation.source.file_path, citation.source.start_line, citation.source.end_line), (info["path"], info["startLine"], info["endLine"]))

    def test_record_and_findings_budgets_do_not_leave_dangling_verified_claims(self):
        for limits in (EvidenceLimits(max_records=1), EvidenceLimits(findings_per_requirement=1)):
            package = self.run_package("Impact of changing login", limits)
            self.assertTrue(package.omissions)
            self.assertEqual(package.requirements[0].state, "partially_satisfied")
            self.assertLessEqual(len(package.evidence), limits.max_records)
            self.assertLessEqual(len(package.findings), limits.findings_per_requirement)
            for finding in package.findings:
                self.assertTrue(set(finding.evidence_record_ids) <= package.evidence.keys())

    def test_path_detail_is_omitted_atomically_when_over_budget(self):
        package = self.run_package("Dependency path from entry to validateToken", EvidenceLimits(max_relationship_items=1))
        self.assertFalse(package.findings)
        self.assertEqual(package.requirements[0].state, "partially_satisfied")
        self.assertIn("relationship_detail", package.resource_limits)

    def test_context_budget_and_source_filter_are_explicit(self):
        package = self.run_package("Where is login defined?", EvidenceLimits(include_source_context=True, max_context_bytes=0), source_filter=lambda text: text)
        self.assertTrue(package.findings)
        self.assertTrue(all(record.source_context is None for record in package.evidence.values()))
        self.assertEqual(package.requirements[0].state, "partially_satisfied")
        with self.assertRaises(ValueError):
            self.run_package("Where is login defined?", EvidenceLimits(include_source_context=True))

    def test_serialized_package_budget_is_enforced_without_orphan_findings(self):
        def inflate(report):
            finding = report.operations[0].result.findings[0]
            report.operations[0].result.findings[0] = finding.model_copy(update={"statement": "Declaration context " * 450})
            return report
        package = self.run_package("Contract of login", EvidenceLimits(max_package_bytes=9000), mutate=inflate)
        self.assertLessEqual(len(package.model_dump_json().encode()), 9000)
        self.assertIn("serialized_size", package.resource_limits)
        self.assertFalse(package.findings)

    def test_deterministic_aggregation_and_read_only_metadata(self):
        with self.owner.session() as executor, patch("answers.answer_question", side_effect=AssertionError("LLM")):
            report = self.definitions(executor)
            digest = hashlib.sha256(executor.database.read_bytes()).hexdigest()
            aggregator = EvidenceAggregator.from_executor(executor)
            first, second = aggregator.aggregate(report), aggregator.aggregate(report)
            self.assertEqual(first.model_dump_json(), second.model_dump_json())
            report.execution_ms += 20
            report.plan.normalized.normalization_ms += 20
            for outcome in report.operations:
                outcome.result.execution_ms += 20
            self.assertEqual(first.package_id, aggregator.aggregate(report).package_id)
            self.assertEqual(hashlib.sha256(executor.database.read_bytes()).hexdigest(), digest)

    def test_changed_source_or_expired_pin_returns_no_authoritative_findings(self):
        with self.owner.session() as executor:
            report = self.execute(executor, "Where is login defined?")
            aggregator = EvidenceAggregator.from_executor(executor)
            path = self.owner.fixture.root / "login.ts"
            path.write_text(path.read_text() + "// changed\n")
            package = aggregator.aggregate(report)
        self.assertEqual(package.validation_status, "stale")
        self.assertFalse(package.evidence)
        self.assertFalse(package.citations)
        self.assertFalse(aggregator.aggregate(report).findings)

    def test_source_changed_during_packaging_discards_the_package(self):
        def mutate_source(text):
            path = self.owner.fixture.root / "login.ts"
            path.write_text(path.read_text() + "// changed\n")
            return text
        package = self.run_package("Where is login defined?", EvidenceLimits(include_source_context=True), source_filter=mutate_source)
        self.assertEqual(package.validation_status, "stale")
        self.assertFalse(package.findings)
        self.assertFalse(package.citations)

    def test_irreducible_coverage_budget_is_an_explicit_error(self):
        with self.owner.session() as executor:
            query = executor.understand(self.owner.request("Where is login defined?"))
            query.requirements[0].limitations = ["x" * 12000]
            report = asyncio.run(executor.execute(executor.plan(query)))
            with self.assertRaises(EvidenceBudgetError):
                EvidenceAggregator.from_executor(executor).aggregate(report, EvidenceLimits(max_package_bytes=4096))


if __name__ == "__main__":
    unittest.main()
