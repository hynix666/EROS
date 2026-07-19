// Shared graph types — the contract KnowledgeGraph.tsx imports.
export interface Node {
  id: string;
  type?: string;
  description?: string;
  group?: number;
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
  isLocked?: boolean;   // KnowledgeGraph: pinned-in-place flag
  rationale?: string;   // KnowledgeGraph: model justification surfaced on hover
}

export interface Link {
  source: string | Node;
  target: string | Node;
  value: number;
  label?: string;
  rationale?: string;
  agent?: string;
}

export interface GraphData {
  nodes: Node[];
  links: Link[];
}

// LIL run surface.
export interface Run {
  id: string;
  question: string;
  status: string;
  computed_sensitivity: string;
  escalated?: boolean;
  created_at: string;
  updated_at: string;
}

export interface TelemetryRow {
  event_type: string;
  latency_ms: number | null;
  token_count: number | null;
  model_name: string | null;
  cost_estimate: number | null;
  created_at: string;
}

export interface EvidenceClaim {
  id: string;
  text: string;
  status: 'draft' | 'verified' | 'contested' | 'stale';
  verification_kind: string;
  confidence: number;
  computed_sensitivity: string;
  locator: string;
  chunk_text: string;
  url: string | null;
  source: string;
  hash: string;
}

export interface KernelResult {
  claim_id: string;
  verdict: 'UNGROUNDED' | 'INDETERMINATE';
  missing_numbers: string[] | null;
  missing_dates: string[] | null;
  missing_entities: string[] | null;
  missing_quotations: string[] | null;
}
