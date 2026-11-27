"""Phase 2 library entry point; no index locks, model calls or source writes."""
from dataclasses import dataclass

from .evidence import build_change_evidence, ChangeEvidencePackage
from .impact import analyze_impact, ImpactResult


@dataclass(frozen=True)
class ChangeAnalysis:
    capture_id: str
    evidence: ChangeEvidencePackage
    impact: ImpactResult
    framework_adapters: tuple = ()
    freshness: str = 'historical_capture'


def analyze_change(capture_store, capture_id, *, max_records_per_version=200,
                   max_depth=2, max_relationships=200, max_paths=100, framework_adapters=()):
    """Resolve a capture through its owning store, never accept webview paths.

    The capture may outlive the mount: its source bytes and provenance are owned
    in memory. Workspace freshness is intentionally not inferred from index state.
    """
    if framework_adapters:
        raise ValueError('No framework metadata adapters have been qualified; static JS/TS analysis is available')
    capture = capture_store.get(capture_id)
    evidence = build_change_evidence(capture, max_records_per_version=max_records_per_version)
    impact = analyze_impact(evidence, capture, max_depth=max_depth,
                            max_relationships=max_relationships, max_paths=max_paths)
    return ChangeAnalysis(capture.id, evidence, impact)
