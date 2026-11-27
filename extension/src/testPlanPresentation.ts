import { escapeChange } from './changeAnalysisPresentation';

export type PlanSource = { reference: string; version: string; path: string; start_line: number; end_line: number; source_hash: string };
export type TestPlanReply = { plan_id: string; capture_id: string; stale_for_workspace: boolean; execution_available: false;
  profiles: { profile_id: string; framework: string; runtime_requirement: string | null; dependency_state: string; limitations: string[] }[];
  tests: { test_id: string; name: string; source: { path: string }; framework: string }[];
  scenarios: { scenario_id: string; title: string; category: string; evidence_basis: string; eligibility: string;
    evidence: { path: string; source_hash: string; start_line: number; end_line: number }[];
    expected_outcome: string | null; preconditions: string[]; unknowns: string[] }[];
  proposed_checks: { check_id: string; test_id: string; profile_id: string; argv: string[] | null; eligibility: string; reason: string; execution: 'not_run' }[];
  selected_check_ids: string[]; search_scope: string[]; no_matching_test: string[]; omitted_tests: number; omitted_scenarios: number;
  limitations: string[]; sources: Record<string, PlanSource> };

export function renderTestPlan(plan: TestPlanReply, sourceActions: Map<string, string>, checkActions: Map<string, string>): string {
  checkActions.clear();
  const escape = escapeChange;
  const sourceButton = (source: {path: string; source_hash: string; start_line: number; end_line: number}) => {
    const found = Object.entries(plan.sources).find(([, value]) => value.path === source.path && value.source_hash === source.source_hash && value.start_line === source.start_line && value.end_line === source.end_line);
    if (!found) return '';
    const key = 'plan-source-' + sourceActions.size;
    sourceActions.set(key, found[0]);
    return `<button data-source="${key}">View captured evidence</button>`;
  };
  const profiles = plan.profiles.map(profile => `<li>${escape(profile.framework)} · Node requirement: ${escape(profile.runtime_requirement ?? 'unknown')} · Dependencies: ${escape(profile.dependency_state)}<ul>${profile.limitations.map(item => `<li>${escape(item)}</li>`).join('')}</ul></li>`).join('');
  const checks = plan.proposed_checks.map((check, index) => {
    const key = 'check-' + index;
    checkActions.set(key, check.check_id);
    const test = plan.tests.find(item => item.test_id === check.test_id);
    const selected = plan.selected_check_ids.includes(check.check_id);
    return `<li><strong>${escape(test?.name ?? check.test_id)}</strong> — ${escape(test?.source.path ?? '')}<p>${escape(check.reason)}</p><pre>${escape(JSON.stringify(check.argv))}</pre><button data-check="${key}" ${plan.stale_for_workspace || check.eligibility !== 'reviewable_existing_test' ? 'disabled' : ''}>${selected ? 'Deselect' : 'Select for review'}</button> Status: not run</li>`;
  }).join('');
  const scenarios = plan.scenarios.map(scenario => `<li><strong>${escape(scenario.title)}</strong><p>Evidence: ${escape(scenario.evidence_basis)} · Eligibility: ${escape(scenario.eligibility)}</p><p>${escape(scenario.expected_outcome ?? 'Expected outcome unknown — confirm intended behavior before using this as a defect check.')}</p>${scenario.evidence.map(sourceButton).join('')}<ul>${[...scenario.preconditions, ...scenario.unknowns].map(item => `<li>${escape(item)}</li>`).join('')}</ul></li>`).join('');
  return `<section><h2>Review test plan</h2><p>${plan.selected_check_ids.length} checks selected for review. No checks have run from review selection alone; isolated execution requires separate authorization.${plan.stale_for_workspace ? ' This plan is stale; capture again before selecting checks.' : ' Plan belongs to the historical capture.'}</p><h3>Requirements</h3><ul>${profiles}</ul><h3>Proposed existing checks</h3><ul>${checks || '<li>No supported related test checks found.</li>'}</ul><h3>Scenarios</h3><ul>${scenarios}</ul><h3>Discovery scope and limitations</h3><p>${plan.search_scope.length} candidate files inspected. ${plan.omitted_tests} test declarations and ${plan.omitted_scenarios} scenarios omitted.</p><ul>${[...plan.no_matching_test, ...plan.limitations].map(item => `<li>${escape(item)}</li>`).join('')}</ul></section>`;
}
