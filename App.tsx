import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Markdown from 'react-markdown';
import { Network, ShieldCheck, Terminal } from 'lucide-react';
import { apiClient } from './api/client';
import type { EvidenceClaim, KernelResult, Run, TelemetryRow } from './types';
import KnowledgeGraph from './components/KnowledgeGraph';

type Tab = 'console' | 'evidence' | 'graph';

const STAGES = ['plan', 'search', 'ingest', 'retrieve', 'analyze',
  'verify', 'arbitrate', 'report', 'qa_eval', 'publish'] as const;

const STATUS_STAGE: Record<string, number> = {
  planning: 0, searching: 1, ingesting: 2, analyzing: 4, verifying: 6,
  reporting: 7, evaluating: 8, paused_approval: 9, paused_budget: 9,
  paused_storage: 9, published: 10,
};

const WORKING = new Set(['planning', 'searching', 'ingesting', 'analyzing',
  'verifying', 'reporting', 'evaluating']);

function statusClass(s: string): string {
  if (WORKING.has(s)) return 'working';
  return s;
}

interface Health { status: string; model_mode: boolean; mode_detail: string; gate4_mode: string }
interface DegradedPrompt { question: string; sensitivity: string; reason: string; minutes: number }

export default function App() {
  const [tab, setTab] = useState<Tab>('console');
  const [health, setHealth] = useState<Health | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ run: Run; telemetry: TelemetryRow[] } | null>(null);
  const [evidence, setEvidence] = useState<{ claims: EvidenceClaim[]; kernel_results: KernelResult[] } | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [sensitivity, setSensitivity] = useState('open');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [degraded, setDegraded] = useState<DegradedPrompt | null>(null);

  const refreshRuns = useCallback(() => {
    apiClient.listRuns().then((r) => setRuns(r.runs)).catch(() => undefined);
  }, []);

  useEffect(() => {
    const ping = () => apiClient.health().then(setHealth).catch(() => setHealth(null));
    ping();
    refreshRuns();
    const h = window.setInterval(ping, 10000);
    const r = window.setInterval(refreshRuns, 4000);
    return () => { window.clearInterval(h); window.clearInterval(r); };
  }, [refreshRuns]);

  // Selected-run polling: run row + telemetry every 2s while it works.
  useEffect(() => {
    if (!selectedId) { setDetail(null); setEvidence(null); setReport(null); return; }
    let alive = true;
    const pull = () => {
      apiClient.getRun(selectedId)
        .then((d) => { if (alive) setDetail(d as { run: Run; telemetry: TelemetryRow[] }); })
        .catch((e) => { if (alive) setError(String(e.message ?? e)); });
    };
    pull();
    const t = window.setInterval(pull, 2000);
    return () => { alive = false; window.clearInterval(t); };
  }, [selectedId]);

  // Evidence + report follow the run into its readable states.
  const status = detail?.run.status;
  useEffect(() => {
    if (!selectedId || !status) return;
    if (['verifying', 'reporting', 'evaluating', 'paused_approval', 'published',
      'insufficient_evidence', 'failed', 'cancelled'].includes(status)) {
      apiClient.getEvidence(selectedId).then(setEvidence).catch(() => setEvidence(null));
    }
    if (['evaluating', 'paused_approval', 'published'].includes(status)) {
      apiClient.getReport(selectedId).then(setReport).catch(() => setReport(null));
    }
  }, [selectedId, status]);

  const start = async (ack = false) => {
    const q = question.trim();
    if (q.length < 3 || busy) return;
    setBusy(true); setError(null);
    try {
      const res = await apiClient.startResearch(q, sensitivity, ack);
      setDegraded(null);
      setQuestion('');
      refreshRuns();
      if (res.run_id) setSelectedId(res.run_id);
    } catch (e) {
      const msg = String((e as Error).message ?? e);
      if (msg.includes('DegradedModeDetected')) {
        // FR18 — surface the explicit choice; never proceed silently.
        let reason = 'local accelerated generation unavailable';
        let minutes = 180;
        try {
          const parsed = JSON.parse(msg);
          reason = parsed.reason ?? reason;
          minutes = parsed.estimated_minutes ?? minutes;
        } catch { /* keep defaults */ }
        setDegraded({ question: q, sensitivity, reason, minutes });
      } else {
        setError(msg);
      }
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!selectedId) return;
    try { await apiClient.cancelRun(selectedId); } catch (e) { setError(String((e as Error).message)); }
  };

  const decide = async (decision: 'approved' | 'rejected') => {
    if (!selectedId) return;
    try {
      await apiClient.approveRun(selectedId, decision);
      refreshRuns();
    } catch (e) { setError(String((e as Error).message)); }
  };

  return (
    <div className="shell">
      <aside className="rail">
        <div className="brand"><b>EROS</b> <small>research operating system · v3.2</small></div>
        <nav aria-label="Sections">
          <button className={`nav ${tab === 'console' ? 'active' : ''}`} onClick={() => setTab('console')}>
            <Terminal size={15} /> Console
          </button>
          <button className={`nav ${tab === 'evidence' ? 'active' : ''}`} onClick={() => setTab('evidence')}>
            <ShieldCheck size={15} /> Evidence
          </button>
          <button className={`nav ${tab === 'graph' ? 'active' : ''}`} onClick={() => setTab('graph')}>
            <Network size={15} /> Graph
          </button>
        </nav>
        <div className="spacer" />
        <div className="health" aria-live="polite">
          <span><span className={`dot ${health ? '' : 'down'}`}>●</span> LIL {health ? 'connected' : 'unreachable'}</span>
          <span>models: {health ? (health.model_mode ? health.mode_detail : `off — ${health.mode_detail}`) : '—'}</span>
          <span>gate 4: {health?.gate4_mode ?? '—'}</span>
        </div>
      </aside>

      {tab === 'graph' ? (
        <main className="main flush"><GraphTab /></main>
      ) : (
        <main className="main">
          {tab === 'console' && (
            <ConsoleTab
              question={question} setQuestion={setQuestion}
              sensitivity={sensitivity} setSensitivity={setSensitivity}
              busy={busy} onStart={() => start(false)}
              degraded={degraded}
              onDegradedProceed={() => { setQuestion(degraded?.question ?? question); void start(true); }}
              onDegradedAbort={() => setDegraded(null)}
              error={error}
              runs={runs} selectedId={selectedId} onSelect={setSelectedId}
              detail={detail} evidence={evidence} report={report}
              onCancel={cancel} onDecide={decide}
            />
          )}
          {tab === 'evidence' && (
            <EvidenceTab runs={runs} selectedId={selectedId} onSelect={setSelectedId} evidence={evidence} />
          )}
        </main>
      )}
    </div>
  );
}

/* ── Console ──────────────────────────────────────────────────────────── */
function ConsoleTab(props: {
  question: string; setQuestion: (v: string) => void;
  sensitivity: string; setSensitivity: (v: string) => void;
  busy: boolean; onStart: () => void;
  degraded: DegradedPrompt | null; onDegradedProceed: () => void; onDegradedAbort: () => void;
  error: string | null;
  runs: Run[]; selectedId: string | null; onSelect: (id: string) => void;
  detail: { run: Run; telemetry: TelemetryRow[] } | null;
  evidence: { claims: EvidenceClaim[]; kernel_results: KernelResult[] } | null;
  report: string | null;
  onCancel: () => void; onDecide: (d: 'approved' | 'rejected') => void;
}) {
  const { runs, selectedId, onSelect, detail } = props;
  return (
    <>
      <div className="eyebrow">console</div>
      <h1 className="title">Ask the machine</h1>
      <p className="subtitle">
        Every answer it publishes is welded to stored evidence by database
        constraints — the four gates below are enforced in PostgreSQL, not in prompts.
      </p>

      <div className="console">
        <span className="prompt">❯</span>
        <input
          type="text"
          value={props.question}
          placeholder="What should EROS investigate?"
          onChange={(e) => props.setQuestion(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') props.onStart(); }}
          aria-label="Research question"
        />
        <select value={props.sensitivity} onChange={(e) => props.setSensitivity(e.target.value)}
          aria-label="Sensitivity">
          <option value="open">open</option>
          <option value="restricted">restricted</option>
          <option value="sensitive">sensitive</option>
        </select>
        <button className="primary" onClick={props.onStart} disabled={props.busy || props.question.trim().length < 3}>
          {props.busy ? 'Starting…' : 'Start run'}
        </button>
      </div>

      {props.degraded && (
        <div className="degraded" role="alertdialog" aria-label="Degraded mode detected">
          <div className="eyebrow">degraded mode detected</div>
          <p>
            Local accelerated generation is unavailable ({props.degraded.reason}). A CPU run of
            this question is estimated at ~{props.degraded.minutes} minutes. Nothing has started.
          </p>
          <div className="actions">
            <button className="primary" onClick={props.onDegradedProceed}>
              Proceed in degraded mode (~{props.degraded.minutes} min)
            </button>
            <button className="ghost" onClick={props.onDegradedAbort}>Abort</button>
          </div>
        </div>
      )}

      {props.error && <div className="error-line">{props.error}</div>}

      <div className="runs">
        {runs.length === 0 && (
          <div className="empty">No runs yet. The machine is idle — ask it something.</div>
        )}
        {runs.map((r) => (
          <button key={r.id} className={`run-card ${selectedId === r.id ? 'selected' : ''}`}
            onClick={() => onSelect(r.id)}>
            <span className="q">{r.question}</span>
            <span className="meta">
              <span className={`status ${statusClass(r.status)}`}>{r.status.replace('_', ' ')}</span>
              <span>{r.computed_sensitivity}</span>
              <span>{new Date(r.created_at).toLocaleString()}</span>
            </span>
          </button>
        ))}
      </div>

      {detail && (
        <RunDetail detail={detail} evidence={props.evidence} report={props.report}
          onCancel={props.onCancel} onDecide={props.onDecide} />
      )}
    </>
  );
}

function RunDetail(props: {
  detail: { run: Run; telemetry: TelemetryRow[] };
  evidence: { claims: EvidenceClaim[]; kernel_results: KernelResult[] } | null;
  report: string | null;
  onCancel: () => void; onDecide: (d: 'approved' | 'rejected') => void;
}) {
  const { run, telemetry } = props.detail;
  const stageIdx = STATUS_STAGE[run.status] ?? -1;
  const terminalBad = ['failed', 'cancelled', 'insufficient_evidence'].includes(run.status);
  const gates = useMemo(() => deriveGates(run, props.evidence, props.report), [run, props.evidence, props.report]);

  return (
    <section style={{ marginTop: 34 }}>
      <div className="eyebrow">run · {run.id.slice(0, 8)}</div>
      <h1 className="title" style={{ fontSize: 19 }}>{run.question}</h1>

      <div className="pipeline" aria-label="Pipeline">
        {STAGES.map((s, i) => (
          <span key={s} style={{ display: 'contents' }}>
            {i > 0 && <span className="sep">→</span>}
            <span className={`stage ${i < stageIdx ? 'done' : ''} ${i === stageIdx && !terminalBad ? 'live' : ''}`}>
              {s}
            </span>
          </span>
        ))}
      </div>

      <div className="gates" aria-label="Trust-chain gates">
        {gates.map((g) => (
          <div key={g.id} className={`gate ${g.state}`}>
            <span className="g">{g.id}</span>
            <span className="name">{g.name}</span>
            {g.state !== 'pending' && <span className="stamp">{g.state.toUpperCase()}</span>}
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
        {WORKING.has(run.status) && (
          <button className="ghost danger" onClick={props.onCancel}>Cancel run</button>
        )}
        {run.status === 'paused_approval' && (
          <>
            <button className="primary" onClick={() => props.onDecide('approved')}>Approve publish</button>
            <button className="ghost danger" onClick={() => props.onDecide('rejected')}>Reject</button>
          </>
        )}
      </div>

      <div className="ledger" aria-label="Event ledger">
        {telemetry.length === 0 && <div className="row"><span className="t">—</span><span className="e">Awaiting first event…</span><span /></div>}
        {telemetry.slice(-80).map((t, i) => (
          <div className="row" key={i}>
            <span className="t">{new Date(t.created_at).toLocaleTimeString()}</span>
            <span className={`e ${eventTone(t.event_type)}`}>{t.event_type}</span>
            <span className="m">
              {t.model_name ?? ''}{t.latency_ms != null ? ` ${t.latency_ms}ms` : ''}
              {t.token_count != null ? ` ${t.token_count}tok` : ''}
              {t.cost_estimate != null ? ` $${Number(t.cost_estimate).toFixed(4)}` : ''}
            </span>
          </div>
        ))}
      </div>

      {props.report && (
        <div className="report"><Markdown>{props.report}</Markdown></div>
      )}
    </section>
  );
}

function eventTone(type: string): string {
  if (/violation|rejected|failed|orphan|divergence/.test(type)) return 'bad';
  if (/quarantin|degraded|replan|insufficient|queued/.test(type)) return 'warn';
  if (/published|verified|sufficient|reconciled/.test(type)) return 'good';
  return '';
}

type GateState = 'pending' | 'pass' | 'hold' | 'fail';

function deriveGates(
  run: Run,
  evidence: { claims: EvidenceClaim[]; kernel_results: KernelResult[] } | null,
  report: string | null,
): { id: string; name: string; state: GateState }[] {
  const published = run.status === 'published';
  const bad = ['failed', 'cancelled'].includes(run.status);
  const claims = evidence?.claims ?? [];
  const kernel = evidence?.kernel_results ?? [];

  const g1: GateState = published ? 'pass'
    : bad ? 'fail'
      : claims.length === 0 ? 'pending'
        : claims.every((c) => c.status !== 'draft') ? 'pass' : 'hold';

  const g2: GateState = published ? 'pass'
    : bad ? 'fail'
      : claims.length === 0 ? 'pending'
        : claims.some((c) => c.status === 'contested') ? 'hold' : 'pass';

  const g3: GateState = published ? 'pass'
    : report ? (run.status === 'paused_approval' ? 'hold' : 'pass')
      : 'pending';

  const ungrounded = kernel.filter((k) => k.verdict === 'UNGROUNDED').length;
  const g4: GateState = published ? 'pass'
    : kernel.length === 0 ? 'pending'
      : ungrounded > 0 ? 'fail' : 'pass';

  return [
    { id: 'G1', name: 'evidence-bound', state: g1 },
    { id: 'G2', name: 'verified only', state: g2 },
    { id: 'G3', name: 'provenance 1.0', state: g3 },
    { id: 'G4', name: 'kernel grounded', state: g4 },
  ];
}

/* ── Evidence ─────────────────────────────────────────────────────────── */
function EvidenceTab(props: {
  runs: Run[]; selectedId: string | null; onSelect: (id: string) => void;
  evidence: { claims: EvidenceClaim[]; kernel_results: KernelResult[] } | null;
}) {
  const { runs, selectedId, onSelect, evidence } = props;
  const kernelByClaim = useMemo(() => {
    const m = new Map<string, KernelResult>();
    (evidence?.kernel_results ?? []).forEach((k) => m.set(k.claim_id, k));
    return m;
  }, [evidence]);

  return (
    <>
      <div className="eyebrow">evidence browser</div>
      <h1 className="title">Claims and their chains</h1>
      <p className="subtitle">
        Each claim resolves to a stored chunk, its artifact hash, and the kernel’s
        deterministic verdict. Verification kind and confidence are shown exactly
        as recorded — nothing is summarized away.
      </p>

      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {runs.slice(0, 12).map((r) => (
          <button key={r.id} className={`ghost ${selectedId === r.id ? 'primary' : ''}`}
            onClick={() => onSelect(r.id)} style={{ maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {r.question}
          </button>
        ))}
      </div>

      {!selectedId && <div className="empty">Select a run to open its evidence chains.</div>}
      {selectedId && !evidence && <div className="empty">No evidence recorded yet for this run.</div>}
      {selectedId && evidence && evidence.claims.length === 0 && (
        <div className="empty">This run holds no claims — it either found insufficient evidence or was stopped early.</div>
      )}

      <div className="claims">
        {(evidence?.claims ?? []).map((c) => {
          const k = kernelByClaim.get(c.id);
          return (
            <article key={c.id} className={`claim ${c.status}`}>
              <div className="text">{c.text}</div>
              <div className="prov">
                <span className={`status ${c.status}`}>{c.status}</span>
                <span className="kind">{c.verification_kind} @ {Number(c.confidence).toFixed(2)}</span>
                <span>{c.locator}</span>
                <span>{c.source}</span>
                <span>{c.url ? new URL(c.url).hostname : `artifact:${c.hash.slice(0, 12)}`}</span>
                <span>{c.computed_sensitivity}</span>
              </div>
              {k && k.verdict === 'UNGROUNDED' && (
                <div className="kernel-verdict">
                  DGK: UNGROUNDED — missing{' '}
                  {[
                    k.missing_numbers?.length ? `numbers ${JSON.stringify(k.missing_numbers)}` : null,
                    k.missing_dates?.length ? `dates ${JSON.stringify(k.missing_dates)}` : null,
                    k.missing_entities?.length ? `entities ${JSON.stringify(k.missing_entities)}` : null,
                    k.missing_quotations?.length ? 'quotations' : null,
                  ].filter(Boolean).join('; ')}
                </div>
              )}
              <details>
                <summary>Evidence chunk</summary>
                <div className="chunk">{c.chunk_text}</div>
              </details>
            </article>
          );
        })}
      </div>
    </>
  );
}

/* ── Graph ────────────────────────────────────────────────────────────── */
function GraphTab() {
  const wrapRef = useRef<HTMLDivElement>(null);
  return (
    <div ref={wrapRef} style={{ height: '100%', width: '100%', position: 'relative' }}>
      <div style={{
        position: 'absolute', top: 12, right: 14, zIndex: 20,
        display: 'flex', gap: 8,
      }}>
        <button className="ghost" onClick={() => window.dispatchEvent(new Event('compile-report'))}>
          Compile report
        </button>
        <button className="ghost danger" onClick={() => window.dispatchEvent(new Event('clear-graph'))}>
          Clear graph
        </button>
      </div>
      <KnowledgeGraph />
    </div>
  );
}
