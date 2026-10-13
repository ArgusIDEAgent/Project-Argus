"""Pure policy/DAG validation independent of any answering engines."""

import unittest

from pydantic import ValidationError

from query_system.contracts import Evidence, IntentRequirement, NormalizedQuery, QueryEntity, SnapshotContext
from query_system.execution_contracts import ExecutionLimits, ExecutionPlan
from query_system.router import build_plan


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.context = SnapshotContext(repository_id="a"*24, snapshot_id="b"*32,
            ready_stage_ids=frozenset({"symbols", "graph", "semantic"}), indexed_files=frozenset({"login.ts", "entry.ts"}))
        self.entities = [self.entity("login"), self.entity("entry")]

    def entity(self, name):
        proof = Evidence(evidence_id=name, symbol_id=name, file_id="file:"+name,
            file_path=name+".ts", source_hash="c"*64, start_line=1, end_line=3,
            repository_id=self.context.repository_id, snapshot_id=self.context.snapshot_id,
            index_snapshot_id="symbols", resolution="syntax_only", evidence_type="symbol_definition")
        return QueryEntity(kind="function", name=name, resolution="resolved", symbol_id=name,
            entity_id=name, file_id=proof.file_id, file_path=proof.file_path, evidence=proof)

    def query(self, requirements):
        intents = list(dict.fromkeys(item.intent for item in requirements))
        return NormalizedQuery(repository_id=self.context.repository_id, snapshot_id=self.context.snapshot_id,
            original_question="compound", normalized_question="compound", intents=intents, primary_intent=intents[0],
            secondary_intents=intents[1:], entities=self.entities, requirements=requirements)

    def test_all_deterministic_policy_mappings(self):
        for intent, engine, operation in (
            ("SYMBOL_LOCATION","graph","find_definition"), ("DIRECT_CALLERS","graph","find_callers"),
            ("DIRECT_CALLEES","graph","find_callees"), ("FUNCTION_CONTRACT","rules","function_contract"),
            ("CHANGE_IMPACT","rules","change_impact"), ("MODULE_OVERVIEW","rules","module_overview"),
            ("TEST_DISCOVERY","rules","reference_test_analysis"), ("DEPENDENCY_PATH","rules","dependency_summary")):
            with self.subTest(intent=intent):
                plan = build_plan(self.query([IntentRequirement(intent=intent,order=0,entity_indices=[0])]), self.context)
                self.assertEqual((plan.operations[0].request.engine, plan.operations[0].request.name), (engine, operation))
        path = build_plan(self.query([IntentRequirement(intent="DEPENDENCY_PATH",order=0,entity_indices=[0,1])]), self.context)
        self.assertEqual(path.operations[0].request.name, "find_paths")
        feature = build_plan(self.query([IntentRequirement(intent="FEATURE_DISCOVERY",order=0,entity_indices=[])]), self.context)
        self.assertEqual(feature.operations[0].request.name, "hybrid_search")

    def test_deferred_and_unresolved_requirements_never_disappear(self):
        requirements = [IntentRequirement(intent=intent,order=index,entity_indices=[0]) for index,intent in enumerate(
            ("FUNCTION_EXPLANATION","WORKFLOW_EXPLANATION","BEHAVIOR_INVESTIGATION","CODE_COMPARISON","UNRESOLVED"))]
        plan = build_plan(self.query(requirements), self.context)
        self.assertEqual(len(plan.requirements), 5)
        self.assertFalse(plan.operations)
        self.assertEqual([item.disposition for item in plan.requirements], ["deferred"]*4+["blocked"])

    def test_ids_order_and_dependencies_are_deterministic(self):
        query = self.query([IntentRequirement(intent="SYMBOL_LOCATION",order=0,entity_indices=[0]),
            IntentRequirement(intent="DIRECT_CALLERS",order=1,entity_indices=[0],depends_on=[0])])
        first, second = build_plan(query,self.context), build_plan(query,self.context)
        self.assertEqual(first,second)
        self.assertEqual(first.operations[1].dependencies,[first.operations[0].operation_id])
        self.assertEqual(ExecutionPlan.model_validate_json(first.model_dump_json()), first)

    def test_cycle_unknown_dependency_and_duplicate_ids_are_rejected(self):
        plan = build_plan(self.query([IntentRequirement(intent="DIRECT_CALLERS",order=0,entity_indices=[0]),
            IntentRequirement(intent="DIRECT_CALLEES",order=1,entity_indices=[1])]), self.context)
        for mutate in ("cycle", "missing", "duplicate"):
            body = plan.model_dump()
            if mutate == "cycle":
                body["operations"][0]["dependencies"]=[plan.operations[1].operation_id]
                body["operations"][1]["dependencies"]=[plan.operations[0].operation_id]
            elif mutate == "missing":
                body["operations"][0]["dependencies"]=["missing"]
            else:
                body["operations"][1]["operation_id"]=plan.operations[0].operation_id
            with self.subTest(mutate=mutate), self.assertRaises(ValidationError):
                ExecutionPlan.model_validate(body)

    def test_operation_and_depth_limits_preserve_requirement_decisions(self):
        query = self.query([IntentRequirement(intent="SYMBOL_LOCATION",order=0,entity_indices=[0]),
            IntentRequirement(intent="DIRECT_CALLERS",order=1,entity_indices=[0],depends_on=[0]),
            IntentRequirement(intent="CHANGE_IMPACT",order=2,entity_indices=[0],depends_on=[1])])
        for limits in (ExecutionLimits(max_operations=1),ExecutionLimits(max_dependency_depth=1)):
            plan = build_plan(query,self.context,limits)
            self.assertEqual(len(plan.operations),1)
            self.assertEqual(len(plan.requirements),3)
            self.assertEqual([item.disposition for item in plan.requirements], ["planned","limited","limited"])

    def test_limits_and_snapshot_mismatch_are_strict(self):
        for values in ({"max_operations":0},{"graph_results":101},{"semantic_candidates":26},{"max_concurrent":9},
                       {"max_evidence":0},{"deadline_ms":0},{"max_operations":"2"}):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                ExecutionLimits(**values)
        query = self.query([IntentRequirement(intent="DIRECT_CALLERS",order=0,entity_indices=[0])])
        with self.assertRaises(ValueError):
            build_plan(query,self.context.model_copy(update={"snapshot_id":"e"*32}))


if __name__ == "__main__":
    unittest.main()
