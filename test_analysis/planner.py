"""Bounded, deterministic scenario plans from source relationships and test metadata."""
from collections import OrderedDict
import threading
from pathlib import PurePosixPath
import json
import posixpath

from change_analysis.capture import CaptureError
from .catalog import TestCatalog
from .contracts import TestPlan, Scenario, ProposedCheck
from .discovery import digest, source_ref

PLANNER_VERSION = 'planner-v1'


class TestPlanner:
    def __init__(self):
        self.lock = threading.RLock()
        self.catalog = TestCatalog()
        self.plans = OrderedDict()
        self.build_count = 0

    def clear(self):
        with self.lock:
            self.plans.clear()
            self.catalog.clear()

    def build(self, snapshot, analysis):
        with self.lock:
            return self._build(snapshot, analysis)

    def _build(self, snapshot, analysis):
        if snapshot.consistency != 'atomic_apfs' or snapshot.conflicts:
            raise CaptureError('planning_capture_required', 'Planning requires a conflict-free atomic capture')
        if (snapshot.id, snapshot.digest, snapshot.repository_id) != (analysis.capture_id, analysis.evidence.capture_digest, analysis.evidence.repository_id):
            raise ValueError('Planning evidence belongs to another capture')
        evidence_identity = digest(analysis.evidence.model_dump_json())
        key = (snapshot.id, snapshot.digest, evidence_identity, PLANNER_VERSION)
        if key in self.plans:
            return TestPlan.model_validate(self.plans[key])
        catalog_id, profiles, tests, scope, omitted, limitations = self.catalog.discover(snapshot)
        plan_id = digest(json.dumps([key, catalog_id]))
        source_map = {v.path: v for v in snapshot.after if v.data is not None}
        changes = [c for c in snapshot.changes if c.status != 'unanalyzed']
        changed_paths = {c.path for c in changes}
        symbols = {s.symbol_id: s for s in analysis.evidence.after.symbols}
        file_ids = {record.proof.file_id: record.proof.file_path for record in analysis.evidence.after.package.evidence.values()}
        related = {}
        for edge in analysis.evidence.after.relationships:
            target = symbols[edge.target_id].path if edge.target_id in symbols else file_ids.get(edge.target_id)
            if target in changed_paths:
                related.setdefault(edge.path, set()).add(target)
        scenarios, matched = [], set()
        for test in tests:
            targets = set(related.get(test.source.path, set()))
            # Node's explicit relative JS imports name an exact captured file.
            # TypeScript's .js-to-.ts substitution is not Node runtime reach.
            for specifier in test.imports:
                if not specifier.startswith('.'):
                    continue
                resolved = posixpath.normpath(posixpath.join(str(PurePosixPath(test.source.path).parent), specifier))
                if not resolved.startswith('../') and resolved in source_map and resolved in changed_paths:
                    targets.add(resolved)
            associated = {path for path in changed_paths if PurePosixPath(path).stem == PurePosixPath(test.source.path).name.split('.')[0]}
            if not targets and not associated and test.source.path not in changed_paths:
                continue
            matched |= targets | associated | ({test.source.path} & changed_paths)
            expectation = test.assertions[0] if test.assertions else None
            basis = 'resolved_reference' if targets else 'filename_association' if associated else 'source_observation'
            unknowns = ['Static test/file association does not establish runtime coverage or a relevant assertion for the changed behavior.',
                        'Required fixtures, environment variables, setup hooks and service dependencies must be reviewed.']
            if expectation is None: unknowns.append('No supported expectation source found; confirm intended behavior before treating this as a defect check.')
            scenarios.append(Scenario(scenario_id=digest(plan_id + test.test_id), category='existing_test',
                title='Review existing test: ' + test.name,
                evidence=[test.source] + [source_ref(source_map[p]) for p in sorted(targets) if p in source_map],
                evidence_basis=basis, related_test_ids=[test.test_id], expectation_source=expectation,
                expected_outcome='Existing assertion expression (not evaluated): ' + expectation.expression if expectation else None,
                preconditions=['Captured runtime requirement and dependency identity must be prepared in isolation.'],
                eligibility='existing_test_candidate' if expectation and test.enabled else 'needs_expectation' if test.enabled else 'unsupported',
                unknowns=unknowns))
        for change in changes:
            source = source_map.get(change.path)
            if source is None: continue
            # Prompt categories are review questions with explicit unknown outcomes,
            # not invented HTTP codes, permissions or business rules.
            for category, question in (
                    ('input', 'Which valid, empty and boundary inputs must this change accept?'),
                    ('errors', 'What errors should this change report, and how should callers handle them?'),
                    ('permissions', 'Does this change require an authorization rule? Confirm the intended policy.'),
                    ('state', 'Which repeated calls, prior state or concurrent updates should be checked?'),
                    ('contracts', 'Which caller or exported contract must remain compatible?')):
                scenarios.append(Scenario(scenario_id=digest(plan_id + change.path + category), category=category,
                    title=question, evidence=[source_ref(source)], evidence_basis='source_observation',
                    related_test_ids=[], expectation_source=None, expected_outcome=None,
                    preconditions=['Confirm this category applies to the changed source.'], eligibility='needs_expectation',
                    unknowns=['Expected behavior has not been established by a validated specification or user-confirmed requirement.']))
        omitted_scenarios = max(0, len(scenarios) - 100)
        scenarios = scenarios[:100]
        checks = []
        profile_map = {p.profile_id: p for p in profiles}
        for test in tests:
            related_scenarios = [s.scenario_id for s in scenarios if test.test_id in s.related_test_ids]
            if not related_scenarios: continue
            profile = profile_map[test.profile_id]
            native = PurePosixPath(test.source.path).suffix in ('.js', '.mjs', '.cjs')
            reviewable = test.framework == 'node:test' and test.enabled and profile.command is not None and native
            checks.append(ProposedCheck(check_id=digest(plan_id + test.test_id + 'check'), test_id=test.test_id,
                profile_id=profile.profile_id, scenario_ids=related_scenarios,
                argv=[*profile.command, '--', test.source.path] if reviewable else None,
                eligibility='reviewable_existing_test' if reviewable else 'unsupported',
                reason='Adapter-owned proposal runs the captured test file; all tests/hooks in that file may run. Execution is unavailable in Phase 4.' if reviewable
                       else 'Unsupported framework, disabled declaration, nested package or TypeScript execution adapter required.'))
        no_match = [f'{path}: No matching test found within {len(scope)} inspected captured candidate test files.' for path in sorted(changed_paths - matched)]
        if any(c.status == 'deleted' for c in changes): limitations.append('Deleted source is preserved in baseline evidence; executable baseline comparisons belong to a later phase.')
        limitations += ['Suggested scenario categories are review prompts. Outcomes are unknown until an expectation source is established.',
                        'A selected proposal is not authorization to execute. Phase 4 has no execution route.',
                        'The omitted_tests count covers catalog-level omissions; per-file and AST budget limitations are reported separately.']
        if omitted_scenarios: limitations.append(f'{omitted_scenarios} scenarios omitted by the 100-scenario budget.')
        plan = TestPlan(plan_id=plan_id, capture_id=snapshot.id, capture_digest=snapshot.digest,
            repository_id=snapshot.repository_id, catalog_id=catalog_id, profiles=profiles, tests=tests,
            scenarios=scenarios, proposed_checks=checks, selected_check_ids=[], search_scope=scope,
            no_matching_test=no_match, omitted_tests=omitted, omitted_scenarios=omitted_scenarios,
            limitations=limitations)
        self.plans[key] = plan.model_dump()
        if len(self.plans) > 4: self.plans.popitem(last=False)
        self.build_count += 1
        return TestPlan.model_validate(plan.model_dump())
