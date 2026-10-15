import type { ResponsePresentation } from './responsePresentation';
import type { HybridReply, SearchResult } from './serverManager';

// Developer-only display projection. Never add this string to an answer or API envelope.
export function debugAnswerEngineFooter(
  view?: ResponsePresentation,
  operations?: HybridReply['operations'],
  generations?: HybridReply['generations'],
): string {
  const visible = new Set(view?.requirements.flatMap(requirement =>
    requirement.sections.flatMap(section => section.item_ids)) ?? []);
  const labels = new Set<string>();
  const names: Record<string, string> = { graph: 'Graph Query', semantic: 'Semantic Search', rules: 'Rule-Based' };
  for (const operation of operations ?? []) {
    if (!Array.isArray(operation.finding_ids) || !operation.finding_ids.some(id => visible.has(id))) continue;
    const name = names[operation.engine];
    if (name) labels.add(name);
  }
  let fallback = false;
  for (const generation of generations ?? []) {
    const displayed = new Set<string>();
    for (const requirement of view?.requirements ?? []) {
      if (!generation.original_orders?.includes(requirement.order)) continue;
      for (const section of requirement.sections) for (const id of section.item_ids) {
        if (view?.items[id]?.origin === 'generation') displayed.add(id);
      }
    }
    if (!displayed.size) continue;
    for (const id of displayed) for (const owner of generation.claim_operation_ids?.[id] ?? []) {
      const engine = operations?.find(operation => operation.operation_id === owner)?.engine;
      if (engine && names[engine]) labels.add(names[engine]);
    }
    if (generation.model_status === 'success' && generation.fallback_status === 'not_used') {
      labels.add('Graph RAG'); labels.add('Local LLM');
    } else if (generation.fallback_status === 'evidence_summary') {
      labels.add('Graph RAG'); fallback = true;
    }
  }
  const ordered = ['Graph Query', 'Semantic Search', 'Rule-Based', 'Graph RAG', 'Local LLM']
    .filter(name => labels.has(name));
  const answer = ordered.length ? ordered.join(' + ') : 'Unknown';
  return '<footer class="debug-answer-engine" aria-label="Developer answer engine attribution">Answer by: '
    + answer + (fallback ? ' (deterministic fallback)' : '') + '</footer>';
}

// The legacy /ask envelope reports the methods used for its returned evidence and
// whether a local model successfully wrote the answer. It is not Phase 8 Graph RAG.
export function debugLegacyAnswerEngineFooter(reply: SearchResult): string {
  const names: Record<string, string> = {
    exact: 'Exact Source Lookup', literal: 'File Lookup', keyword: 'Keyword Search',
    semantic: 'Semantic Search', graph: 'Graph Query',
  };
  const methods = new Set(reply.results.flatMap(result => result.retrievalMethods ?? []));
  const labels = ['exact', 'literal', 'keyword', 'semantic', 'graph']
    .filter(method => methods.has(method)).map(method => names[method]);
  if (reply.answerMode === 'local-llm') labels.push('Local LLM');
  else if (reply.answerMode === 'evidence' && reply.results.length) labels.push('Evidence Summary');
  return '<footer class="debug-answer-engine" aria-label="Developer answer engine attribution">Answer by: '
    + (labels.length ? labels.join(' + ') : 'Unknown') + '</footer>';
}
