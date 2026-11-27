export type ChangePresentation = { capture_id: string; mode: string; stale_for_workspace: boolean;
  freshness: string; model_status: string;
  sections: { title: string; text: string; items: { text: string; reference: string | null }[] }[];
  sources: Record<string, { version: string; path: string; start_line: number; end_line: number; record_id: string }>;
  limitations: string[] };

export function escapeChange(value: string): string {
  return value.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
}

export function renderChange(p: ChangePresentation, actions: Map<string, string>): string {
  actions.clear();
  let count = 0;
  const sections = p.sections.map(section => `<section><h2>${escapeChange(section.title)}</h2><p>${escapeChange(section.text)}</p><ul>${section.items.map(item => {
    let button = '';
    if (item.reference && p.sources[item.reference]) {
      const id = `source-${count++}`;
      actions.set(id, item.reference);
      button = ` <button data-source="${id}">View captured source</button>`;
    }
    return `<li>${escapeChange(item.text)}${button}</li>`;
  }).join('')}</ul></section>`).join('');
  return `<p>Captured historical evidence${p.stale_for_workspace ? ' — stale for the current workspace' : ''}. Mode: ${escapeChange(p.mode)}.</p>${sections}<h2>Limitations</h2><ul>${p.limitations.map(item => `<li>${escapeChange(item)}</li>`).join('')}</ul>`;
}
