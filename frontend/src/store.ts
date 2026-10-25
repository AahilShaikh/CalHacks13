import { create } from 'zustand';
import * as api from './lib/api';
import type { FeaJob, FeaSettings } from './lib/fea';
import {
  argSummary,
  deviationFrom,
  featuresFrom,
  phaseOf,
  resultSummary,
  viewOf,
  type HeatScale,
  type AgentView,
  type Deviation,
  type ModelState,
  type Phase,
  type ServerEvent,
  type SessionStatus,
} from './lib/events';

export type ToolItem = {
  kind: 'tool';
  id: number;
  name: string;
  phase: Phase;
  args: Record<string, unknown>;
  argText: string;
  status: 'running' | 'done' | 'failed';
  result?: string;
  startedAt: number;
  endedAt?: number;
  image?: string;
  code?: string;
  /** What this call looked at, for the 3D viewer (slice planes, camera). */
  view?: AgentView;
};

export type FeedItem =
  | { kind: 'agent'; id: number; text: string }
  | { kind: 'user'; id: number; text: string }
  | { kind: 'note'; id: number; text: string; tone: 'muted' | 'error' | 'ok' }
  | ToolItem;

export type Revision = {
  revision: number;
  url: string;
  generated: boolean;
  triangles: number;
  extents: number[];
  /** The reference coloured by distance to this revision, once computed. */
  heatmap?: HeatScale & { url: string };
};

export type ViewMode = 'overlay' | 'side' | 'heatmap' | 'fea-stress' | 'fea-displacement';
export type Tab = 'activity' | 'model' | 'fea';
export type AppStatus = 'idle' | 'starting' | SessionStatus;

type State = {
  // Before a session: what the user picked.
  meshFile: File | null;
  meshPreviewUrl: string | null;
  images: File[];
  /** Units of the uploaded mesh; '' tells the agent not to claim physical sizes. */
  units: 'mm' | '';
  // Session.
  sessionId: string | null;
  sessionUnits: 'mm' | null;
  feaJob: FeaJob | null;
  feaStarting: boolean;
  feaError: string | null;
  runFea: (settings: FeaSettings) => Promise<void>;
  status: AppStatus;
  stage: string;
  error: string | null;
  startedAt: number | null;
  endedAt: number | null;
  cost: number;
  feed: FeedItem[];
  revisions: Revision[];
  deviations: Deviation[];
  features: string[];
  /** Latest parametric model snapshot; null until the agent first touches the model. */
  model: ModelState | null;
  /** Parameters whose value or source changed in the latest snapshot (highlighted briefly). */
  changedParams: string[];
  pendingCode: string | null;
  // View.
  shownRevision: number | null; // null follows the latest generated revision
  viewMode: ViewMode;
  tab: Tab;
  /** A tool card the user clicked to see in 3D; null follows the agent (if followAgent). */
  focusId: number | null;
  followAgent: boolean;
  /** Playing back a recorded session; null when live. */
  replay: ReplayState | null;
  /** Time (unix s) of the latest applied event; the replay's clock. */
  lastEventAt: number | null;

  setMesh: (file: File | null) => void;
  setUnits: (units: 'mm' | '') => void;
  stop: () => Promise<void>;
  addImages: (files: File[]) => void;
  removeImage: (index: number) => void;
  submit: (prompt: string) => Promise<void>;
  apply: (events: ServerEvent[]) => void;
  showRevision: (revision: number | null) => void;
  setViewMode: (mode: ViewMode) => void;
  setTab: (tab: Tab) => void;
  setFocus: (id: number | null) => void;
  setFollow: (follow: boolean) => void;
  startReplay: (id: string) => Promise<void>;
  setReplay: (change: Partial<Pick<ReplayState, 'playing' | 'speed'>>) => void;
  skipReplay: () => void;
  exitReplay: () => void;
};

export type ReplayState = { id: string; index: number; total: number; playing: boolean; speed: number };

let unsubscribe: (() => void) | null = null;
let nextId = 1;
let replayEvents: ServerEvent[] = [];
let replayTimer = 0;
// Longest pause kept between recorded events; long agent thinking is shortened.
const REPLAY_MAX_GAP_S = 3;

/** Session fields reset when a live session or a replay starts. */
const freshSession = {
  error: null, feed: [], revisions: [], deviations: [], features: [], cost: 0, model: null,
  changedParams: [], pendingCode: null, shownRevision: null, focusId: null, followAgent: true,
  stage: '', startedAt: null, endedAt: null, lastEventAt: null,
  sessionUnits: null, feaJob: null, feaStarting: false, feaError: null, viewMode: 'overlay',
} satisfies Partial<State>;

export const MESH_EXTENSIONS = ['.stl', '.obj', '.ply', '.glb'];
export const isMeshFile = (file: File) => MESH_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext));

export const useStore = create<State>((set, get) => ({
  meshFile: null,
  meshPreviewUrl: null,
  images: [],
  units: 'mm',
  sessionId: null,
  sessionUnits: null,
  feaJob: null,
  feaStarting: false,
  feaError: null,
  status: 'idle',
  stage: '',
  error: null,
  startedAt: null,
  endedAt: null,
  cost: 0,
  feed: [],
  revisions: [],
  deviations: [],
  features: [],
  model: null,
  changedParams: [],
  pendingCode: null,
  shownRevision: null,
  viewMode: 'overlay',
  tab: 'activity',
  focusId: null,
  followAgent: true,
  replay: null,
  lastEventAt: null,

  runFea: async (settings) => {
    const s = get();
    const revision = selectedRevision(s)?.revision;
    if (!s.sessionId || s.replay || revision === undefined || s.feaStarting
      || s.feaJob?.status === 'running' || s.feaJob?.status === 'queued') return;
    const sessionId = s.sessionId;
    set({ feaStarting: true, feaError: null });
    let job: FeaJob;
    try {
      job = await api.createFea(sessionId, revision, settings);
    } catch (err) {
      if (get().sessionId === sessionId) set({ feaStarting: false, feaError: (err as Error).message });
      return;
    }
    if (get().sessionId !== sessionId) return;
    set({ feaJob: job, feaStarting: false,
      ...(get().viewMode.startsWith('fea-') ? { viewMode: 'overlay' } : {}) });
    while (get().sessionId === sessionId && get().feaJob?.analysis_id === job.analysis_id) {
      try {
        job = await api.getFea(sessionId, job.analysis_id);
        if (get().sessionId !== sessionId || get().feaJob?.analysis_id !== job.analysis_id) return;
        set({ feaJob: job, feaError: job.error });
        if (job.status === 'succeeded') {
          if (selectedRevision(get())?.revision === revision) {
            set({ viewMode: 'fea-stress', followAgent: false, focusId: null });
          }
          return;
        }
        if (job.status === 'failed') return;
      } catch (err) {
        if (get().sessionId !== sessionId) return;
        set({ feaError: `Reconnecting to FEA: ${(err as Error).message}` });
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  },

  setMesh: (file) => {
    const previous = get().meshPreviewUrl;
    if (previous) URL.revokeObjectURL(previous);
    const preview = file && file.name.toLowerCase().endsWith('.stl') ? URL.createObjectURL(file) : null;
    set({ meshFile: file, meshPreviewUrl: preview, error: null });
  },
  setUnits: (units) => set({ units }),
  stop: async () => {
    const { sessionId } = get();
    if (!sessionId) return;
    try {
      await api.interrupt(sessionId);
    } catch (err) {
      set((s) => ({ feed: [...s.feed, note(`Could not stop: ${(err as Error).message}`, 'error')] }));
    }
  },
  addImages: (files) => set((s) => ({ images: [...s.images, ...files].slice(0, 8) })),
  removeImage: (index) => set((s) => ({ images: s.images.filter((_, i) => i !== index) })),

  submit: async (prompt) => {
    const { sessionId, status, meshFile, images, units } = get();
    const text = prompt.trim();
    if (!text) return;
    const userItem: FeedItem = { kind: 'user', id: nextId++, text };
    if (sessionId && (status === 'running' || status === 'queued')) {
      set((s) => ({ feed: [...s.feed, userItem] }));
      try {
        await api.sendTurn(sessionId, text);
      } catch (err) {
        set((s) => ({ feed: [...s.feed, note(`Follow-up not sent: ${(err as Error).message}`, 'error')] }));
      }
      return;
    }
    if (!meshFile) {
      set({ error: 'Add a mesh first: drop it on the viewer or use Attach.' });
      return;
    }
    set({ ...freshSession, status: 'starting', feed: [userItem],
      focusId: null, followAgent: true });
    try {
      const { session_id } = await api.createSession(meshFile, text, images, units || undefined);
      unsubscribe?.();
      set({ sessionId: session_id, sessionUnits: units || null, status: 'queued', startedAt: Date.now() / 1000, endedAt: null, shownRevision: null });
      unsubscribe = api.subscribe(
        session_id,
        (events) => get().apply(events),
        () => {
          if (get().status === 'running') set({ stage: 'reconnecting' });
        },
      );
    } catch (err) {
      set({ status: 'idle', error: (err as Error).message });
    }
  },

  apply: (events) =>
    set((s) => {
      let { feed, revisions, deviations, features, model, changedParams, pendingCode, cost, status, stage, error, endedAt } = s;
      feed = [...feed];
      const updateLastTool = (name: string | null, change: (t: ToolItem) => ToolItem) => {
        for (let i = feed.length - 1; i >= 0; i--) {
          const item = feed[i];
          if (item.kind === 'tool' && item.status === 'running' && (name === null || item.name === name)) {
            feed[i] = change(item);
            return;
          }
        }
      };
      for (const e of events) {
        switch (e.type) {
          case 'agent_text':
            if (e.text.trim()) feed.push({ kind: 'agent', id: nextId++, text: e.text.trim() });
            break;
          case 'code':
            pendingCode = e.code;
            break;
          case 'tool_call':
            feed.push({
              kind: 'tool',
              id: nextId++,
              name: e.name,
              phase: phaseOf(e.name),
              args: e.args ?? {},
              argText: argSummary(e.name, e.args),
              status: 'running',
              startedAt: e.at,
              code: e.name === 'execute' ? pendingCode ?? undefined : undefined,
              view: viewOf(e.name, e.args),
            });
            if (e.name === 'execute') pendingCode = null;
            break;
          case 'render':
            updateLastTool(null, (t) => ({ ...t, image: e.url }));
            break;
          case 'tool_result': {
            updateLastTool(e.name, (t) => ({
              ...t,
              status: e.ok ? 'done' : 'failed',
              result: resultSummary(e.name, e.text),
              endedAt: e.at,
              view: e.ok ? viewOf(t.name, t.args, e.text) ?? t.view : t.view,
            }));
            // Without model_state events (older servers), read checks from tool results.
            const dev = e.ok && !model ? deviationFrom(e.name, e.text) : null;
            if (dev) deviations = [...deviations, dev];
            const f = e.ok ? featuresFrom(e.text) : null;
            if (f) features = f;
            break;
          }
          case 'mesh':
            revisions = [...revisions.filter((r) => r.revision !== e.revision), e].sort((a, b) => a.revision - b.revision);
            break;
          case 'model_state': {
            const { type: _type, seq: _seq, at: _at, ...next } = e;
            const before = model?.params ?? {};
            changedParams = Object.keys(next.params).filter((name) => {
              const old = before[name];
              return !old || old.value !== next.params[name].value || old.source !== next.params[name].source;
            });
            model = next;
            features = next.features.map((f) => `${f.id} ${f.name}`);
            // The model's own history is the authoritative accuracy record.
            deviations = next.history
              .filter((h) => h.deviation)
              .map((h) => ({ ...h.deviation!, trend: h.trend, revision: h.revision }));
            break;
          }
          case 'heatmap': {
            const { type: _t, seq: _s, at: _a, revision, ...heat } = e;
            revisions = revisions.map((r) => (r.revision === revision ? { ...r, heatmap: heat } : r));
            break;
          }
          case 'heatmap_skipped':
          case 'mesh_skipped':
            break; // expected while the agent experiments; not worth a feed entry
          case 'stage':
            stage = e.stage;
            break;
          case 'status':
            status = e.status;
            if (e.error) error = e.error;
            if (e.status === 'done' || e.status === 'failed') endedAt = e.at;
            break;
          case 'error':
            feed.push(note(e.message, 'error'));
            break;
          case 'turn_end':
            if (typeof e.cost_usd === 'number') cost += e.cost_usd;
            feed.push(note(turnLine(e), e.is_error ? 'error' : 'ok'));
            break;
          case 'session_done':
            feed.push(note('Session finished', 'muted'));
            break;
          case 'user_prompt':
            // Live sessions already show the prompt the user typed; replays need it.
            if (s.replay) feed.push({ kind: 'user', id: nextId++, text: e.text });
            break;
          case 'interrupted':
            feed.push(note('Stopped. Send a prompt to continue.', 'muted'));
            break;
        }
      }
      const lastEventAt = events.length ? events[events.length - 1].at : s.lastEventAt;
      const viewMode = s.viewMode.startsWith('fea-') && !activeFea({ ...s, revisions }) ? 'overlay' : s.viewMode;
      return { feed, revisions, deviations, features, model, changedParams, pendingCode, cost, status, stage, error,
        endedAt, lastEventAt, viewMode };
    }),

  showRevision: (shownRevision) => set((s) => ({ shownRevision,
    ...(s.viewMode.startsWith('fea-') && !activeFea({ ...s, shownRevision }) ? { viewMode: 'overlay' as const } : {}),
  })),
  setViewMode: (viewMode) => set({ viewMode }),
  setTab: (tab) => set({ tab }),
  setFocus: (focusId) => set({ focusId }),
  setFollow: (followAgent) => set({ followAgent, ...(followAgent ? { focusId: null } : {}) }),

  startReplay: async (id) => {
    get().exitReplay();
    unsubscribe?.();
    unsubscribe = null;
    try {
      replayEvents = await api.replayEvents(id);
    } catch (err) {
      set({ error: `Replay not available: ${(err as Error).message}` });
      return;
    }
    set({
      ...freshSession,
      sessionId: null,
      status: 'queued',
      startedAt: replayEvents[0]?.at ?? null,
      replay: { id, index: 0, total: replayEvents.length, playing: true, speed: 4 },
    });
    scheduleReplay();
  },
  setReplay: (change) => {
    const replay = get().replay;
    if (!replay) return;
    set({ replay: { ...replay, ...change } });
    scheduleReplay();
  },
  skipReplay: () => {
    const replay = get().replay;
    if (!replay || replay.index >= replayEvents.length) return;
    clearTimeout(replayTimer);
    get().apply(replayEvents.slice(replay.index));
    set({ replay: { ...replay, index: replayEvents.length, playing: false } });
  },
  exitReplay: () => {
    clearTimeout(replayTimer);
    if (!get().replay) return;
    replayEvents = [];
    set({ ...freshSession, replay: null, status: 'idle', sessionId: null });
  },
}));

/** Apply the next recorded events, then wait the (shortened) recorded gap at the replay's speed. */
function scheduleReplay() {
  clearTimeout(replayTimer);
  const { replay, apply } = useStore.getState();
  if (!replay || !replay.playing) return;
  if (replay.index >= replayEvents.length) {
    useStore.setState({ replay: { ...replay, playing: false } });
    return;
  }
  // Events recorded together (one burst) are applied together.
  const first = replayEvents[replay.index];
  let end = replay.index + 1;
  while (end < replayEvents.length && replayEvents[end].at - first.at < 0.05) end++;
  apply(replayEvents.slice(replay.index, end));
  const done = end >= replayEvents.length;
  useStore.setState({ replay: { ...replay, index: end, playing: !done } });
  if (done) return;
  const gap = Math.min(replayEvents[end].at - replayEvents[end - 1].at, REPLAY_MAX_GAP_S);
  replayTimer = window.setTimeout(scheduleReplay, Math.max(16, (gap * 1000) / replay.speed));
}

function note(text: string, tone: 'muted' | 'error' | 'ok'): FeedItem {
  return { kind: 'note', id: nextId++, text, tone };
}

function turnLine(e: Extract<ServerEvent, { type: 'turn_end' }>) {
  if (e.subtype === 'error_max_turns') return 'Turn limit reached. Ask Aida to continue.';
  if (e.is_error) return e.subtype === 'success' ? 'The turn failed; see the message above.' : `Turn stopped (${e.subtype})`;
  const parts = ['Turn complete'];
  if (e.turns) parts.push(`${e.turns} steps`);
  if (e.duration_s) parts.push(`${Math.round(e.duration_s)}s`);
  return parts.join(' · ');
}

// Derived values used by several components.

/**
 * The tool call the 3D viewer should mirror: the clicked card, else (when following) the
 * agent's latest call that has something to show. Inspection calls without a view yet
 * (a running view_mesh, mesh_frame) keep the previous view up; once the agent moves on to
 * building or verifying, the slice planes clear so they do not hide the CAD.
 */
export function activeTool(s: Pick<State, 'feed' | 'focusId' | 'followAgent'>): ToolItem | null {
  if (s.focusId !== null) {
    const item = s.feed.find((i) => i.id === s.focusId);
    return item?.kind === 'tool' ? item : null;
  }
  if (!s.followAgent) return null;
  for (let i = s.feed.length - 1; i >= 0; i--) {
    const item = s.feed[i];
    if (item.kind !== 'tool') continue;
    if (item.view) return item;
    if (item.phase !== 'inspect') return null;
  }
  return null;
}

export const generatedRevisions = (revisions: Revision[]) => revisions.filter((r) => r.generated);
export const referenceRevision = (revisions: Revision[]) => revisions.find((r) => !r.generated) ?? null;

export function selectedRevision(s: Pick<State, 'revisions' | 'shownRevision'>) {
  const generated = generatedRevisions(s.revisions);
  return s.shownRevision === null ? generated.at(-1) : generated.find((r) => r.revision === s.shownRevision);
}

/** Results are visible only on the exact CAD revision that was tested. */
export function activeFea(s: Pick<State, 'feaJob' | 'revisions' | 'shownRevision'>) {
  const result = s.feaJob?.result;
  return result && result.revision === selectedRevision(s)?.revision ? result : null;
}
