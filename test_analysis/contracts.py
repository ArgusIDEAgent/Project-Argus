"""Versioned planning contracts; execution and coverage cannot be claimed here."""
from typing import Literal
from pydantic import Field, model_validator
from query_system.contracts import ContractModel, RepositoryId, SourceHash
from knowledge_base.identity import relative_path


class SourceRef(ContractModel):
    path: str
    source_hash: SourceHash
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    version: Literal['after'] = 'after'

    @model_validator(mode='after')
    def location(self):
        relative_path(self.path)
        if self.end_line < self.start_line:
            raise ValueError('Reversed planning source range')
        return self


class TestProfile(ContractModel):
    profile_id: SourceHash
    package_root: str
    framework: Literal['node:test', 'vitest', 'unsupported', 'unknown']
    runtime_requirement: str | None
    dependency_identity: SourceHash | None
    dependency_state: Literal['lockfile_captured', 'no_lockfile', 'unknown']
    manifests: list[SourceRef]
    configuration: list[SourceRef]
    command: list[str] | None  # adapter-owned descriptive command, never a shell script
    environment_policy: Literal['offline_isolation_required'] = 'offline_isolation_required'
    execution_available: Literal[False] = False
    runtime_status: Literal['unverified'] = 'unverified'
    execution_limits: dict[str, int] | None = None
    limitations: list[str]


class Assertion(ContractModel):
    source: SourceRef
    expression: str


class DiscoveredTest(ContractModel):
    test_id: SourceHash
    name: str
    source: SourceRef
    framework: Literal['node:test', 'vitest', 'unsupported', 'unknown']
    profile_id: SourceHash
    assertions: list[Assertion]
    imports: list[str]
    enabled: bool
    limitations: list[str]


class Scenario(ContractModel):
    scenario_id: SourceHash
    category: Literal['existing_test', 'input', 'errors', 'permissions', 'state', 'contracts']
    title: str
    evidence: list[SourceRef] = Field(min_length=1)
    evidence_basis: Literal['resolved_reference', 'filename_association', 'source_observation']
    related_test_ids: list[SourceHash]
    expectation_source: Assertion | None
    expected_outcome: str | None
    preconditions: list[str]
    eligibility: Literal['existing_test_candidate', 'needs_expectation', 'unsupported']
    unknowns: list[str]

    @model_validator(mode='after')
    def expectation(self):
        if (self.expectation_source is None) != (self.expected_outcome is None):
            raise ValueError('An expected outcome requires an expectation source')
        if self.expectation_source is None and not self.unknowns:
            raise ValueError('Missing expectations must remain explicit')
        return self


class ProposedCheck(ContractModel):
    check_id: SourceHash
    test_id: SourceHash
    profile_id: SourceHash
    scenario_ids: list[SourceHash]
    argv: list[str] | None
    eligibility: Literal['reviewable_existing_test', 'unsupported']
    reason: str
    execution: Literal['not_run'] = 'not_run'


class TestPlan(ContractModel):
    schema_version: Literal['test-plan-v1'] = 'test-plan-v1'
    plan_id: SourceHash
    capture_id: str
    capture_digest: SourceHash
    repository_id: RepositoryId
    catalog_id: SourceHash
    profiles: list[TestProfile]
    tests: list[DiscoveredTest]
    scenarios: list[Scenario]
    proposed_checks: list[ProposedCheck]
    selected_check_ids: list[SourceHash]
    search_scope: list[str]
    no_matching_test: list[str]
    omitted_tests: int = Field(ge=0)
    omitted_scenarios: int = Field(ge=0)
    limitations: list[str]
    execution_available: Literal[False] = False

    @model_validator(mode='after')
    def ownership(self):
        tests = {t.test_id: t for t in self.tests}
        profiles = {p.profile_id for p in self.profiles}
        scenarios = {s.scenario_id for s in self.scenarios}
        checks = {c.check_id for c in self.proposed_checks}
        if len(tests) != len(self.tests) or len(profiles) != len(self.profiles) or len(scenarios) != len(self.scenarios) or len(checks) != len(self.proposed_checks):
            raise ValueError('Duplicate planning identities')
        for test in self.tests:
            if test.profile_id not in profiles:
                raise ValueError('Test profile is not plan-owned')
        for scenario in self.scenarios:
            if scenario.expectation_source is not None and not any(scenario.expectation_source in tests[test_id].assertions for test_id in scenario.related_test_ids if test_id in tests):
                raise ValueError('Expectation source is not owned by a related captured test')
            if set(scenario.related_test_ids) - tests.keys():
                raise ValueError('Scenario refers to a missing test')
        for check in self.proposed_checks:
            if check.test_id not in tests or check.profile_id != tests[check.test_id].profile_id or set(check.scenario_ids) - scenarios:
                raise ValueError('Check ownership mismatch')
        if len(set(self.selected_check_ids)) != len(self.selected_check_ids) or set(self.selected_check_ids) - checks:
            raise ValueError('Selected checks must belong to this plan')
        if len(self.model_dump_json().encode()) > 4000000:
            raise ValueError('Serialized test plan exceeds the 4 MB planning budget')
        return self
