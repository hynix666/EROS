// Typed client for the LIL. Every UI capability is a LIL capability —
// the interface layer is the boundary (ADR-021), the UI never goes around it.
import type { EvidenceClaim, KernelResult, Run, TelemetryRow } from '../types';

async function j<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const apiClient = {
  health: () => fetch('/health').then((r) => j<{ status: string; model_mode: boolean; mode_detail: string; gate4_mode: string }>(r)),

  startResearch: (question: string, sensitivity: string, degradedAck = false) =>
    fetch('/research', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, sensitivity, degraded_ack: degradedAck }),
    }).then((r) => j<{ status: string; run_id?: string; queue_id?: string }>(r)),

  listRuns: () => fetch('/runs').then((r) => j<{ runs: Run[] }>(r)),

  getRun: (id: string) =>
    fetch(`/research/${id}`).then((r) => j<{ run: Run & Record<string, unknown>; telemetry: TelemetryRow[] }>(r)),

  cancelRun: (id: string) => fetch(`/research/${id}`, { method: 'DELETE' }).then((r) => j(r)),

  approveRun: (id: string, decision: 'approved' | 'rejected') =>
    fetch(`/research/${id}/approve?decision=${decision}`, { method: 'POST' }).then((r) => j(r)),

  getReport: async (id: string) => {
    const r = await fetch(`/research/${id}/report`);
    if (!r.ok) throw new Error((await r.json().catch(() => ({ detail: r.statusText }))).detail);
    return r.text();
  },

  getEvidence: (id: string) =>
    fetch(`/research/${id}/evidence`).then((r) => j<{ claims: EvidenceClaim[]; kernel_results: KernelResult[] }>(r)),

  // Knowledge-graph assists (used by KnowledgeGraph.tsx flows).
  smartLink: (sourceNode: unknown, targetNode: unknown) =>
    fetch('/api/smart-link', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sourceNode, targetNode }),
    }).then((r) => j<{ label: string; justification: string }>(r)),

  inferRelationship: (sourceNode: unknown, targetNode: unknown) =>
    fetch('/api/infer-relationship', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sourceNode, targetNode }),
    }).then((r) => j<{ label: string }>(r)),

  queryGraph: (query: string, graphData: unknown) =>
    fetch('/api/query-graph', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, graphData }),
    }).then((r) => j<{ nodeIds: string[] }>(r)),
};
