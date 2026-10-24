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
  if (a.axis !== undefined && a.at !== undefined) return `${a.axis} = ${fmt(a.at)}`;
  if (a.fit_box !== undefined) return `${a.direction ?? 'iso'} · zoomed`;
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
  // Results are often a JSON line followed by "[SEND: path]"; try each JSON-looking line.
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed.startsWith('{')) continue;
    try {
      return JSON.parse(trimmed);
    } catch {
      /* truncated or not JSON; keep looking */
    }
  }
  return null;
}

// ------------------------------------------------------------ agent's view in 3D ---

export type Vec3 = [number, number, number];

/** What a tool call looked at, in reference-mesh coordinates (Z-up). */
export type AgentView = {
  /** A cutting plane; offset null means "through the mesh centre". */
  slice?: { normal: Vec3; offset: number | null; label: string };
  /** Several parallel planes, e.g. the transitions a slice_series found. */
  series?: { normal: Vec3; axis: string; offsets: number[] | null; label: string };
  /** The exact camera of a rendered view (orthographic in the renderer). */
  camera?: { focal: Vec3; dir: Vec3; up: Vec3; width: number; height: number };
};

const AXIS_NORMAL: Record<string, Vec3> = { x: [1, 0, 0], y: [0, 1, 0], z: [0, 0, 1] };
const NUM = '(-?\\d+(?:\\.\\d+)?(?:[eE][-+]?\\d+)?)';

// Results reach the UI cut to 4000 characters, so a big JSON result may not parse.
// These read single fields with patterns instead, which survives truncation.
function numField(text: string, key: string): number | null {
  const m = text.match(new RegExp(`"${key}":\\s*${NUM}`));
  return m ? Number(m[1]) : null;
}
function vecField(text: string, key: string): Vec3 | null {
  const m = text.match(new RegExp(`"${key}":\\s*\\[\\s*${NUM}\\s*,\\s*${NUM}\\s*,\\s*${NUM}\\s*\\]`));
  return m ? [Number(m[1]), Number(m[2]), Number(m[3])] : null;
}
function after(text: string, key: string): string {
  const at = text.indexOf(`"${key}"`);
  return at < 0 ? '' : text.slice(at);
}
const asVec = (value: unknown): Vec3 | null =>
  Array.isArray(value) && value.length === 3 && value.every((v) => typeof v === 'number') ? (value as Vec3) : null;
const round = (v: number) => Math.round(v * 1000) / 1000;

function planeFromArgs(axisValue: unknown, normalValue: unknown, atValue: unknown) {
  const axis = typeof axisValue === 'string' ? axisValue.toLowerCase() : null;
  const normal = (axis && AXIS_NORMAL[axis]) || asVec(normalValue);
  if (!normal) return null;
  const offset = typeof atValue === 'number' ? atValue : null;
  return { axis, normal, offset };
}

function planeLabel(axis: string | null, normal: Vec3, offset: number | null) {
  const where = offset === null ? 'centre' : String(round(offset));
  return axis ? `${axis} = ${where}` : `n [${normal.map(round).join(', ')}] at ${where}`;
}

/**
 * Where a tool call looked, from its arguments and (once finished) its result.
 * Called at tool_call with no result so a slice plane can show while the call runs.
 */
export function viewOf(tool: string, args: Record<string, unknown>, result?: string): AgentView | undefined {
  const a = args ?? {};
  const text = result ?? '';
  if (tool === 'view_mesh') {
    const view: AgentView = {};
    const plane = planeFromArgs(a.slice_axis, a.slice_normal, a.slice_at);
    if (plane) {
      const offset = plane.offset ?? numField(after(text, 'slice'), 'offset');
      view.slice = { normal: plane.normal, offset, label: planeLabel(plane.axis, plane.normal, offset) };
    }
    const focal = vecField(text, 'focal_point');
    const dir = vecField(text, 'view_dir');
    const up = vecField(text, 'screen_up');
    const width = numField(text, 'visible_width');
    const height = numField(text, 'visible_height');
    if (focal && dir && up && width && height) view.camera = { focal, dir, up, width, height };
    return view.slice || view.camera ? view : undefined;
  }
  if (tool === 'slice_mesh') {
    const plane = planeFromArgs(a.axis, a.normal, a.at);
    if (!plane) return undefined;
    const offset = plane.offset ?? numField(after(text, 'plane'), 'offset');
    return { slice: { normal: plane.normal, offset, label: planeLabel(plane.axis, plane.normal, offset) } };
  }
  if (tool === 'slice_series') {
    const axis = typeof a.axis === 'string' ? a.axis.toLowerCase() : '';
    const normal = AXIS_NORMAL[axis];
    if (!normal) return undefined;
    const between = [...text.matchAll(new RegExp(`"between":\\s*\\[\\s*${NUM}\\s*,\\s*${NUM}\\s*\\]`, 'g'))];
    const offsets = between.length ? between.map((m) => (Number(m[1]) + Number(m[2])) / 2) : null;
    const label = offsets
      ? `${offsets.length} transition${offsets.length === 1 ? '' : 's'} along ${axis}`
      : `scanning along ${axis}`;
    return { series: { normal, axis, offsets, label } };
  }
  return undefined;
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
  // Mesh tools: a few readable facts instead of the raw JSON (which may be cut off).
  const width = numField(text, 'visible_width');
  const height = numField(text, 'visible_height');
  if (tool === 'view_mesh' && width && height) {
    const view = text.match(/"view":\s*"([^"]+)"/)?.[1] ?? 'view';
    const units = text.match(/"units":\s*"([^"]+)"/)?.[1];
    return `${view} · ${round(width)} × ${round(height)}${units && units !== 'unknown' ? ` ${units}` : ''} in frame`;
  }
  const data = tryJson(text);
  if (tool === 'slice_mesh' && text.includes('"plane"')) {
    const offset = numField(after(text, 'plane'), 'offset');
    const axis = after(text, 'plane').match(/"axis":\s*"([xyz])"/)?.[1];
    const loops = Array.isArray(data?.loops) ? `${data.loops.length} loop${data.loops.length === 1 ? '' : 's'}` : null;
    return [axis && offset !== null ? `${axis} = ${round(offset)}` : 'section', loops].filter(Boolean).join(' · ');
  }
  if (tool === 'slice_series' && text.includes('"transitions"')) {
    const count = (text.match(/"between"/g) ?? []).length;
    return `${count} transition${count === 1 ? '' : 's'} found`;
  }
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
