// Events streamed by the API (aida/api/cad_sessions.py) over SSE.
// Every event carries `seq` (monotonic per session) and `at` (unix seconds).

export type Stamp = { seq: number; at: number };

export type ServerEvent = Stamp &
  (
    | { type: 'agent_text'; text: string }
    | { type: 'code'; index: number; code: string }
    | { type: 'tool_call'; name: string; args: Record<string, unknown> }
    | { type: 'tool_result'; name: string; ok: boolean; text: string }
    | { type: 'render'; url: string }
    | {
        type: 'mesh';
        revision: number;
        url: string;
        generated: boolean;
        triangles: number;
        extents: number[];
      }
    | { type: 'mesh_skipped'; reason: string }
    | { type: 'error'; message: string }
    | { type: 'status'; status: SessionStatus; error: string | null }
    | { type: 'stage'; stage: string }
    | {
        type: 'turn_end';
        is_error: boolean;
        subtype: string;
        turns?: number;
        cost_usd?: number | null;
        duration_s?: number;
      }
    | { type: 'session_done'; status: string }
  );

export type SessionStatus = 'queued' | 'running' | 'done' | 'failed';

export type Phase = 'inspect' | 'build' | 'verify';

const BUILD = new Set(['execute', 'export']);
const VERIFY = new Set(['compare_to_mesh', 'validate', 'model_report']);

/** Which step of the inspect -> build -> verify loop a tool belongs to. */
export function phaseOf(tool: string): Phase {
  if (VERIFY.has(tool)) return 'verify';
  if (BUILD.has(tool) || tool.startsWith('model_')) return 'build';
  return 'inspect';
}

export const PHASE_VERB: Record<Phase, string> = {
  inspect: 'Inspecting',
  build: 'Building',
  verify: 'Verifying',
};

const fmt = (value: unknown) =>
  typeof value === 'number' ? String(Math.round(value * 1000) / 1000) : String(value);

/** A short, human summary of a tool call's arguments for the card header. */
export function argSummary(tool: string, args: Record<string, unknown>): string {
  const a = args ?? {};
  if (tool === 'execute') return 'python';
  if (a.slice_axis !== undefined && a.slice_at !== undefined) return `${a.slice_axis} = ${fmt(a.slice_at)}`;
  if (a.slice_axis !== undefined) return `along ${a.slice_axis}`;
  if (a.axis !== undefined) return `along ${a.axis}`;
  if (a.name !== undefined) return fmt(a.name);
  if (a.feature_id !== undefined) return fmt(a.feature_id);
  if (a.objects !== undefined) return fmt(a.objects);
  if (a.object_name !== undefined) return fmt(a.object_name);
  if (a.direction !== undefined) return fmt(a.direction);
  if (a.path !== undefined) return String(a.path).split(/[\\/]/).pop() ?? '';
  if (a.params && typeof a.params === 'object') return `${Object.keys(a.params).length} params`;
  return '';
}

export type Deviation = {
  p95: number;
  threshold: number;
  verdict: string;
  trend?: string;
  revision?: number;
};

function tryJson(text: string): Record<string, any> | null {
  const start = text.indexOf('{');
  if (start < 0) return null;
  try {
    return JSON.parse(text.slice(start));
  } catch {
    return null;
  }
}

/** Model tools and compare_to_mesh return JSON; pull out the deviation check if there is one. */
export function deviationFrom(tool: string, text: string): Deviation | null {
  const data = tryJson(text);
  if (!data) return null;
  const d = data.deviation;
  if (d && typeof d.p95 === 'number') {
    return { p95: d.p95, threshold: d.threshold, verdict: d.verdict, trend: data.trend, revision: data.revision };
  }
  if (tool === 'compare_to_mesh' && data.reference_to_cad && data.cad_to_reference) {
    return {
      p95: Math.max(data.reference_to_cad.p95_abs, data.cad_to_reference.p95_abs),
      threshold: data.threshold,
      verdict: data.verdict,
    };
  }
  return null;
}

/** Feature list ("1 base_plate") from a model tool result, when present. */
export function featuresFrom(text: string): string[] | null {
  const data = tryJson(text);
  return Array.isArray(data?.features) ? data.features.map(String) : null;
}

/** One line describing a tool result, for the card body. */
export function resultSummary(tool: string, text: string): string {
  const dev = deviationFrom(tool, text);
  if (dev) {
    const parts = [`p95 ${dev.p95.toFixed(3)}`, dev.verdict];
    if (dev.revision !== undefined) parts.unshift(`rev ${dev.revision}`);
    if (dev.trend && dev.trend !== 'first') parts.push(dev.trend);
    return parts.join(' · ');
  }
  const data = tryJson(text);
  if (data && typeof data.action === 'string') {
    const parts = [data.revision !== undefined ? `rev ${data.revision}` : null, data.action.replaceAll('_', ' ')];
    if (Array.isArray(data.features)) parts.push(`${data.features.length} features`);
    return parts.filter(Boolean).join(' · ');
  }
  const line = text
    .split('\n')
    .map((l) => l.trim())
    .find((l) => l && !l.startsWith('[SEND:'));
  return (line ?? '').slice(0, 220);
}
