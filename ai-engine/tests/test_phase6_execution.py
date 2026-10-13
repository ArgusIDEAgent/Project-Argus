"""Real one-pin integration plus controlled concurrent/failure edge cases."""

import asyncio
import hashlib
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import api
import config
from phase5_fixture import fixture
from query_system.contracts import IntentRequirement, QueryRequest
from query_system.execution_contracts import ExecutionLimits, ExecutionReport
from query_system.executor import MultiEngineExecutor, existing_engines
from query_system.semantic_engine import existing_api_channels


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.repo = self.fixture.store, self.fixture.repo
        for guard in (patch.dict(api._REGISTERED_REPOS,{self.repo:{"rootPath":str(self.fixture.root)}}),
                      patch.object(api,"get_qdrant_client",return_value=self.fixture.client),
                      patch.object(api,"embed_texts",side_effect=lambda texts:[[1.]+[0.]*383 for _ in texts]),
                      patch.object(api,"DEFAULT_DATA_DIR",str(self.fixture.data))):
            guard.start(); self.addCleanup(guard.stop)
        self.channels = existing_api_channels(api,config.EMBEDDING_MODEL_NAME)
        self.native_factory = existing_engines(self.channels)

    def request(self,question):
        return QueryRequest(repository_id=self.repo,question=question)

    def session(self,factory=None):
        return MultiEngineExecutor.pinned(self.store,self.channels,engine_factory=factory)

    def run_query(self,question,limits=None,factory=None):
        with self.session(factory) as executor:
            normalized=executor.understand(self.request(question))
            plan=executor.plan(normalized,limits)
            return asyncio.run(executor.execute(plan))

    def typed_requirements(self,executor,intents,targets,dependencies=None):
        question="Where is login defined and where is entry defined?"
        query=executor.understand(self.request(question))
        query.intents=list(dict.fromkeys(intents))
        query.primary_intent=query.intents[0]; query.secondary_intents=query.intents[1:]
        query.requirements=[IntentRequirement(intent=intent,order=index,entity_indices=target,
            depends_on=(dependencies or {}).get(index,[])) for index,(intent,target) in enumerate(zip(intents,targets))]
        return query

    def test_single_routing_and_source_proof_uses_one_pin(self):
        with patch.object(self.store,"pin_view",wraps=self.store.pin_view) as pins, self.session() as executor:
            query=executor.understand(self.request("Who calls login?"))
            report=asyncio.run(executor.execute(executor.plan(query)))
        self.assertEqual(pins.call_count,1)
        self.assertEqual(report.status,"success")
        self.assertEqual(report.operations[0].result.engine,"graph")
        self.assertEqual({proof.snapshot_id for proof in report.evidence.values()},{report.snapshot_id})
        self.assertGreater(len({proof.index_snapshot_id for proof in report.evidence.values()}),1)
        self.assertEqual(ExecutionReport.model_validate_json(report.model_dump_json()),report)

    def test_all_supported_intents_execute_existing_entry_points(self):
        for question,operation in (("Where is login defined?","find_definition"),("Who calls login?","find_callers"),
            ("What does login call?","find_callees"),("Dependency path from entry to validateToken","find_paths"),
            ("Dependencies of login","dependency_summary"),("Contract of validateToken","function_contract"),
            ("Impact of changing login","change_impact"),("Overview of auth.ts","module_overview"),
            ("What tests cover login?","reference_test_analysis"),("Find code that checks access","hybrid_search")):
            with self.subTest(question=question):
                report=self.run_query(question)
                self.assertEqual(report.plan.operations[0].request.name,operation)
                self.assertIsNotNone(report.operations[0].result)
                self.assertIn(report.operations[0].status,("success","partial","not_found"))

    def test_module_paths_and_repeated_intents_remain_distinct(self):
        paths=self.run_query("Dependency path from entry.ts to auth.ts")
        self.assertEqual(paths.plan.operations[0].request.name,"find_dependency_paths")
        self.assertEqual(paths.operations[0].status,"success")
        repeated=self.run_query("Who calls login and who calls entry?")
        self.assertEqual(len(repeated.requirements),2)
        self.assertEqual(len(repeated.operations),2)
        self.assertEqual(len({operation.operation_id for operation in repeated.plan.operations}),2)

    def test_compound_execution_respects_sequential_dependencies(self):
        starts=[]
        def factory(name,*args):
            engine=self.native_factory(name,*args)
            original=engine.execute
            async def wrapped(operation,context):
                starts.append(operation.name)
                return await original(operation,context)
            engine.execute=wrapped
            return engine
        report=self.run_query("Find the login function, show its callers, and tell me what might be affected if I change it.",factory=factory)
        self.assertEqual(starts,["find_definition","find_callers","change_impact"])
        self.assertEqual(len(report.requirements),3)

    def test_independent_operations_really_run_concurrently_and_are_bounded(self):
        barrier=threading.Barrier(2)
        def factory(name,*args):
            engine=self.native_factory(name,*args)
            original=engine.execute
            async def wrapped(operation,context):
                barrier.wait(timeout=3)
                return await original(operation,context)
            engine.execute=wrapped
            return engine
        report=self.run_query("Who calls login and what does entry call?",ExecutionLimits(max_concurrent=2),factory)
        self.assertEqual(report.peak_concurrency,2)
        self.assertTrue(all(outcome.result for outcome in report.operations))

    def test_semantic_discovery_blocks_graph_until_explicit_selection(self):
        with self.session() as executor:
            query=executor.understand(self.request("Find code that validates a token and show its callers"))
            report=asyncio.run(executor.execute(executor.plan(query)))
            self.assertEqual(len(report.operations),1)
            self.assertEqual(report.requirements[1].status,"ambiguous")
            self.assertTrue(report.clarifications[0].candidates)
            choice=report.clarifications[0].candidates[0]
            selected=asyncio.run(executor.execute(executor.plan(query,selections={0:choice})))
            self.assertEqual(len(selected.operations),2)
            self.assertIsNotNone(selected.operations[1].result)
            self.assertEqual(selected.plan.operations[1].request.parameters["selected_symbol_id"],choice.symbol_id)

    def test_a_single_semantic_hit_is_still_not_promoted(self):
        report=self.run_query("Find code that validates a token in auth.ts and show its callers",ExecutionLimits(semantic_candidates=1))
        self.assertEqual(report.requirements[1].status,"ambiguous")
        self.assertEqual(len(report.operations),1)
        self.assertLessEqual(len(report.clarifications[0].candidates),1)

    def test_ambiguous_missing_and_unsupported_target_gates(self):
        ambiguous=self.run_query("Who calls sharedName?")
        self.assertEqual(ambiguous.status,"ambiguous")
        self.assertFalse(ambiguous.operations)
        self.assertEqual(len(ambiguous.clarifications[0].candidates),2)
        missing=self.run_query("Who calls missingFunction?")
        self.assertEqual(missing.status,"not_found")
        self.assertFalse(missing.operations)
        self.assertTrue(missing.clarifications)
        unsupported=self.run_query("Who calls validate_token in legacy.py?")
        self.assertEqual(unsupported.status,"unsupported")
        self.assertFalse(unsupported.operations)

    def test_explanation_workflow_behavior_and_comparison_are_deferred(self):
        for question in ("Explain login","Explain the login workflow","Why does login fail?","Compare login and entry"):
            with self.subTest(question=question):
                report=self.run_query(question)
                self.assertFalse(report.operations)
                self.assertEqual(report.requirements[0].status,"deferred")
                self.assertEqual(report.status,"unsupported")

    def test_independent_success_is_preserved_on_engine_failure(self):
        def factory(name,*args):
            engine=self.native_factory(name,*args)
            original=engine.execute
            async def wrapped(operation,context):
                if operation.name=="find_callers":
                    raise RuntimeError("graph offline")
                return await original(operation,context)
            engine.execute=wrapped
            return engine
        report=self.run_query("Who calls login and what does entry call?",factory=factory)
        self.assertEqual(report.status,"partial")
        self.assertEqual(report.operations[0].status,"error")
        self.assertEqual(report.operations[1].status,"success")
        self.assertTrue(report.evidence)

    def test_failed_dependency_is_skipped_not_empty_success(self):
        def factory(name,*args):
            engine=self.native_factory(name,*args)
            async def fail(operation,context):
                raise RuntimeError("offline")
            engine.execute=fail
            return engine
        report=self.run_query("Find login and show its callers",factory=factory)
        self.assertEqual(report.operations[0].status,"error")
        self.assertEqual(report.operations[1].status,"skipped")
        self.assertIsNone(report.operations[1].result)

    def test_unavailable_graph_is_not_masked_by_semantic_similarity(self):
        report=self.run_query("Who calls login?",factory=lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
        self.assertEqual(report.operations[0].status,"error")
        self.assertEqual(report.plan.operations[0].request.engine,"graph")
        self.assertEqual(len(report.operations),1)

    def test_mixed_generation_engine_payload_is_rejected_per_branch(self):
        def factory(name,*args):
            engine=self.native_factory(name,*args)
            original=engine.execute
            async def wrapped(operation,context):
                result=await original(operation,context)
                return result.model_copy(update={"snapshot_id":"f"*32}) if operation.name=="find_callers" else result
            engine.execute=wrapped
            return engine
        report=self.run_query("Who calls login and what does entry call?",factory=factory)
        self.assertEqual(report.operations[0].status,"stale_index")
        self.assertEqual(report.operations[1].status,"success")
        self.assertEqual({proof.snapshot_id for proof in report.evidence.values()},{report.snapshot_id})

    def test_stale_saved_sources_discard_the_complete_report(self):
        with self.session() as executor:
            plan=executor.plan(executor.understand(self.request("Who calls login?")))
            (self.fixture.root/"auth.ts").write_text("// changed\n"+self.fixture.files["auth.ts"])
            report=asyncio.run(executor.execute(plan))
        self.assertEqual(report.status,"stale_index")
        self.assertFalse(report.evidence)
        self.assertTrue(all(outcome.result is None for outcome in report.operations))

    def test_operation_depth_results_and_evidence_limits_are_explicit(self):
        for limits in (ExecutionLimits(max_operations=1),ExecutionLimits(max_dependency_depth=1)):
            report=self.run_query("Find login, show its callers, and show its change impact",limits)
            self.assertEqual(len(report.requirements),3)
            self.assertEqual(len(report.operations),1)
            self.assertTrue(report.resource_limits)
        limited=self.run_query("Who calls validateToken?",ExecutionLimits(graph_results=1))
        self.assertLessEqual(len(limited.operations[0].result.findings),1)
        evidence=self.run_query("Who calls validateToken?",ExecutionLimits(max_evidence=1))
        self.assertEqual(evidence.operations[0].status,"partial")
        self.assertIsNone(evidence.operations[0].result)
        self.assertLessEqual(len(evidence.evidence),1)
        self.assertIn("returned_evidence",evidence.resource_limits)

    def test_cooperative_timeout_drains_workers_before_pin_release(self):
        finished=threading.Event()
        def factory(name,*args):
            engine=self.native_factory(name,*args)
            original=engine.execute
            async def slow(operation,context):
                await asyncio.sleep(.08)
                result=await original(operation,context)
                finished.set()
                return result
            engine.execute=slow
            return engine
        report=self.run_query("Where is login defined?",ExecutionLimits(deadline_ms=30),factory)
        self.assertTrue(finished.is_set())
        self.assertGreaterEqual(report.execution_ms,80)
        self.assertEqual(report.operations[0].status,"partial")
        self.assertIsNone(report.operations[0].result)
        self.assertTrue(report.resource_limits)

    def test_semantic_channel_loss_remains_explicit_partial(self):
        with patch.object(api,"embed_texts",side_effect=RuntimeError("model offline")):
            report=self.run_query("Find code that validates a token")
        self.assertEqual(report.operations[0].result.engine,"semantic")
        self.assertEqual(report.operations[0].status,"partial")
        self.assertTrue(report.operations[0].result.warnings)

    def test_tampered_plan_is_rejected_before_engine_dispatch(self):
        with self.session() as executor:
            plan=executor.plan(executor.understand(self.request("Who calls login?")))
            body=plan.model_dump(); body["operations"][0]["request"]["name"]="run_sql"
            from query_system.execution_contracts import ExecutionPlan
            tampered=ExecutionPlan.model_validate(body)
            with self.assertRaises(ValueError):
                asyncio.run(executor.execute(tampered))

    def test_caller_cancellation_drains_before_reusing_the_session(self):
        started, finished = threading.Event(), threading.Event()
        def factory(name, *args):
            engine = self.native_factory(name, *args)
            original = engine.execute
            async def slow(operation, context):
                started.set()
                await asyncio.sleep(.08)
                result = await original(operation, context)
                finished.set()
                return result
            engine.execute = slow
            return engine
        with self.session(factory) as executor:
            plan = executor.plan(executor.understand(self.request("Where is login defined?")))
            async def cancel():
                task = asyncio.create_task(executor.execute(plan))
                while not started.is_set():
                    await asyncio.sleep(.001)
                task.cancel()
                await asyncio.sleep(.005)
                with self.assertRaises(ValueError):
                    await executor.execute(plan)
                task.cancel()  # Repeated cancellation cannot prematurely release the guard.
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(finished.is_set())
                return await executor.execute(plan)
            self.assertEqual(asyncio.run(cancel()).status, "success")

    def test_shared_semantic_provider_is_serialized(self):
        running, peak = 0, 0
        lock = threading.Lock()
        def factory(name, *args):
            engine = self.native_factory(name, *args)
            original = engine.execute
            async def slow(operation, context):
                nonlocal running, peak
                with lock:
                    running += 1
                    peak = max(peak, running)
                try:
                    await asyncio.sleep(.01)
                    return await original(operation, context)
                finally:
                    with lock:
                        running -= 1
            engine.execute = slow
            return engine
        with self.session(factory) as executor:
            query = self.typed_requirements(executor, ["FEATURE_DISCOVERY"] * 2, [[], []])
            report = asyncio.run(executor.execute(executor.plan(query)))
        self.assertEqual(len(report.operations), 2)
        self.assertEqual(peak, 1)
        self.assertTrue(all(outcome.result for outcome in report.operations))

    def test_invalid_source_hash_and_expired_pin_are_rejected(self):
        def factory(name, *args):
            engine = self.native_factory(name, *args)
            original = engine.execute
            async def corrupt(operation, context):
                result = await original(operation, context)
                key = result.evidence_ids[0]
                engine.evidence[key] = engine.evidence[key].model_copy(update={"source_hash": "0" * 64})
                return result
            engine.execute = corrupt
            return engine
        report = self.run_query("Where is login defined?", factory=factory)
        self.assertEqual(report.status, "stale_index")
        self.assertFalse(report.evidence)
        with self.session() as executor:
            plan = executor.plan(executor.understand(self.request("Where is login defined?")))
        self.assertEqual(asyncio.run(executor.execute(plan)).status, "stale_index")

    def test_deferred_parent_skips_graph_and_retains_all_requirements(self):
        with self.session() as executor:
            query = self.typed_requirements(executor, ["FUNCTION_EXPLANATION", "DIRECT_CALLERS"], [[0], [0]], {1: [0]})
            report = asyncio.run(executor.execute(executor.plan(query)))
        self.assertEqual([item.status for item in report.requirements], ["deferred", "skipped"])
        self.assertIsNone(report.operations[0].result)

    def test_report_rejects_counterfeit_proof_or_requirement_mapping(self):
        from pydantic import ValidationError
        report = self.run_query("Where is login defined?")
        for field in ("proof", "requirement", "owner", "concurrency"):
            body = report.model_dump()
            if field == "proof":
                next(iter(body["evidence"].values()))["snapshot_id"] = "f" * 32
            elif field == "requirement":
                body["requirements"][0]["requirement"]["intent"] = "DIRECT_CALLERS"
            elif field == "owner":
                body["operations"][0]["requirement_id"] = "req-forged"
            else:
                body["peak_concurrency"] = 8
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ExecutionReport.model_validate(body)

    def test_explicit_selection_cannot_escape_the_requested_file_scope(self):
        from query_system.contracts import EntityCandidate
        with self.session() as executor:
            query = executor.understand(self.request("Find code that validates a token in auth.ts and show its callers"))
            other = executor.understand(self.request("Where is login defined?" )).entities[0]
            choice = EntityCandidate(entity_id=other.entity_id, name=other.name, kind=other.kind,
                file_path=other.file_path, symbol_id=other.symbol_id, evidence=other.evidence)
            with self.assertRaises(ValueError):
                executor.plan(query, selections={0: choice})

    def test_deterministic_results_read_only_and_no_llm(self):
        with self.session() as executor, patch("answers.answer_question",side_effect=AssertionError("LLM")):
            plan=executor.plan(executor.understand(self.request("Who calls login?")))
            digest=hashlib.sha256(executor.database.read_bytes()).hexdigest()
            first=asyncio.run(executor.execute(plan)); second=asyncio.run(executor.execute(plan))
            self.assertEqual(first.plan,second.plan)
            self.assertEqual(first.requirements,second.requirements)
            self.assertEqual(first.evidence,second.evidence)
            self.assertEqual(first.operations[0].result.model_dump(exclude={"execution_ms"}),second.operations[0].result.model_dump(exclude={"execution_ms"}))
            self.assertEqual(hashlib.sha256(executor.database.read_bytes()).hexdigest(),digest)


if __name__=="__main__":
    unittest.main()
