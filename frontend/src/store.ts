import { create } from 'zustand';
import * as api from './lib/api';
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
  type PartState,
  type Phase,
  type ServerEvent,
  type SessionStatus,
  type WorkspaceState,
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
  /** Parts in this revision's scene. */
  parts?: string[];
  /** Each referenced part's scan coloured by distance to this revision, once computed. */
  heatmaps?: Record<string, HeatScale & { url: string }>;
};

export type ViewMode = 'overlay' | 'side' | 'heatmap';
export type AppStatus = 'idle' | 'starting' | SessionStatus;

type State = {
  // Meshes on their way to the workspace (each becomes a part), previewed while they upload.
  meshFiles: File[];
  meshPreviews: MeshPreview[];
  images: File[];
  /** Units of the uploaded mesh; '' tells the agent not to claim physical sizes. */
  units: 'mm' | '';
  // Session.
  sessionId: string | null;
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
  /** Latest workspace snapshot (every part and the assembly); null until the agent first touches it. */
  workspace: WorkspaceState | null;
  /** The part the user picked (or 'assembly'); null follows the agent's active part. */
  selectedPart: string | null;
  /** "<part>.<param>" keys whose value or source changed in the latest snapshot (highlighted briefly). */
  changedParams: string[];
  pendingCode: string | null;
  // View.
  shownRevision: number | null; // null follows the latest generated revision
  viewMode: ViewMode;
  /** The steps panel on the right is expanded (false: a thin rail). */
  stepsOpen: boolean;
  /** The agent message the user closed in the reply card; newer messages show again. */
  replyDismissedId: number | null;
  /** A tool card the user clicked to see in 3D; null follows the agent (if followAgent). */
  focusId: number | null;
  followAgent: boolean;
  /** Playing back a recorded session; null when live. */
  replay: ReplayState | null;
  /** Time (unix s) of the latest applied event; the replay's clock. */
  lastEventAt: number | null;

  openWorkspace: () => Promise<void>;
  addMeshes: (files: File[]) => Promise<void>;
  selectPart: (part: string | null) => void;
  setUnits: (units: 'mm' | '') => void;
  stop: () => Promise<void>;
  addImages: (files: File[]) => void;
  removeImage: (index: number) => void;
  submit: (prompt: string) => Promise<void>;
  apply: (events: ServerEvent[]) => void;
  showRevision: (revision: number | null) => void;
  setViewMode: (mode: ViewMode) => void;
  setStepsOpen: (open: boolean) => void;
  dismissReply: (id: number) => void;
  clearError: () => void;
  setFocus: (id: number | null) => void;
  setFollow: (follow: boolean) => void;
  startReplay: (id: string) => Promise<void>;
  setReplay: (change: Partial<Pick<ReplayState, 'playing' | 'speed'>>) => void;
  skipReplay: () => void;
  exitReplay: () => void;
};

export type ReplayState = { id: string; index: number; total: number; playing: boolean; speed: number };

let unsubscribe: (() => void) | null = null;
// The workspace being opened, so uploads and prompts made meanwhile wait for it.
let opening: Promise<void> | null = null;
let nextId = 1;
let replayEvents: ServerEvent[] = [];
let replayTimer = 0;
// Longest pause kept between recorded events; long agent thinking is shortened.
const REPLAY_MAX_GAP_S = 3;

/** Session fields reset when a live session or a replay starts. */
const freshSession = {
  error: null, feed: [], revisions: [], deviations: [], features: [], cost: 0, workspace: null, selectedPart: null,
  changedParams: [], pendingCode: null, shownRevision: null, focusId: null, followAgent: true, replyDismissedId: null,
  stage: '', startedAt: null, endedAt: null, lastEventAt: null,
} satisfies Partial<State>;

export const MESH_EXTENSIONS = ['.stl', '.obj', '.ply', '.glb'];
export type MeshPreview = { url: string; kind: 'stl' | 'obj' | 'ply' | 'glb' };
export const isMeshFile = (file: File) => MESH_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext));
export const MAX_MESHES = 12;

export const useStore = create<State>((set, get) => ({
  meshFiles: [],
  meshPreviews: [],
  images: [],
  units: 'mm',
  sessionId: null,
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
  workspace: null,
  selectedPart: null,
  changedParams: [],
  pendingCode: null,
  shownRevision: null,
  viewMode: 'overlay',
  stepsOpen: true,
  replyDismissedId: null,
  focusId: null,
  followAgent: true,
  replay: null,
  lastEventAt: null,

  openWorkspace: async () => {
    if (opening) return opening;
    opening = (async () => {
      set({ ...freshSession, status: 'starting', sessionId: null, meshFiles: [] });
      setPreviews([]);
      try {
        const { session_id } = await api.createSession(get().units || undefined);
        unsubscribe?.();
        set({ sessionId: session_id, status: 'queued', startedAt: Date.now() / 1000 });
        unsubscribe = api.subscribe(
          session_id,
          (events) => get().apply(events),
          () => {
            if (get().status === 'running') set({ stage: 'reconnecting' });
          },
        );
      } catch (err) {
        set({ status: 'failed', error: `Could not open the workspace: ${(err as Error).message}` });
      } finally {
        opening = null;
      }
    })();
    return opening;
  },
  addMeshes: async (files) => {
    const added = files.filter(isMeshFile).slice(0, MAX_MESHES);
    if (!added.length) return;
    const meshFiles = [...get().meshFiles, ...added];
    setPreviews(meshFiles);
    set({ meshFiles, error: null });
    if (opening) await opening;
    const { sessionId, units } = get();
    const done = () => {
      const rest = get().meshFiles.filter((f) => !added.includes(f));
      setPreviews(rest);
      set({ meshFiles: rest });
    };
    if (!sessionId) {
      done();
      set({ error: 'No workspace is open; reload the page.' });
      return;
    }
    try {
      await api.uploadMeshes(sessionId, added, units || undefined);
    } catch (err) {
      set({ error: `${added.map((f) => f.name).join(', ')} not added: ${(err as Error).message}` });
    } finally {
      done();
    }
  },

  selectPart: (selectedPart) =>
    set((s) => ({ selectedPart, deviations: s.workspace ? deviationsOf(s.workspace, selectedPart) : s.deviations })),
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
    const text = prompt.trim();
    if (!text) return;
    if (opening) await opening;
    const { sessionId, images } = get();
    if (!sessionId) {
      set({ error: 'No workspace is open; reload the page.' });
      return;
    }
    set((s) => ({ feed: [...s.feed, { kind: 'user', id: nextId++, text }], error: null }));
    try {
      // Photos go to the agent with this message.
      if (images.length) {
        await api.uploadImages(sessionId, images);
        set({ images: [] });
      }
      await api.sendTurn(sessionId, text);
    } catch (err) {
      set((s) => ({ feed: [...s.feed, note(`Message not sent: ${(err as Error).message}`, 'error')] }));
    }
  },

  apply: (events) =>
    set((s) => {
      let { feed, revisions, deviations, features, workspace, changedParams, pendingCode, cost, status, stage, error, endedAt } = s;
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
            // Without workspace events (older servers), read checks from tool results.
            const dev = e.ok && !workspace ? deviationFrom(e.name, e.text) : null;
            if (dev) deviations = [...deviations, dev];
            const f = e.ok ? featuresFrom(e.text) : null;
            if (f) features = f;
            break;
          }
          case 'mesh':
            revisions = [...revisions.filter((r) => r.revision !== e.revision), e].sort((a, b) => a.revision - b.revision);
            break;
          case 'model_state':
          case 'workspace_state': {
            const { type: _type, seq: _seq, at: _at, ...rest } = e;
            // Recordings from before workspaces sent one model: show it as a single part.
            const next: WorkspaceState = e.type === 'workspace_state' ? (rest as WorkspaceState) : {
              active: 'part',
              parts: [{ ...(rest as ModelState), name: 'part', object: 'generated_cad', description: '', has_reference: true }],
              assembly: { ...EMPTY_MODEL, instances: {}, instance_count: 0, variants: [] },
            };
            changedParams = changedKeys(workspace, next);
            workspace = next;
            const active = next.parts.find((p) => p.name === next.active);
            features = active ? active.features.map((f) => `${f.id} ${f.name}`) : features;
            // The models' own histories are the authoritative accuracy record.
            deviations = deviationsOf(next, s.selectedPart);
            break;
          }
          case 'heatmap': {
            const { type: _t, seq: _s, at: _a, revision, part, ...heat } = e;
            const key = part ?? 'part';
            revisions = revisions.map((r) =>
              r.revision === revision ? { ...r, heatmaps: { ...r.heatmaps, [key]: heat } } : r,
            );
            break;
          }
          case 'notice':
            feed.push(note(e.text, 'muted'));
            break;
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
      return { feed, revisions, deviations, features, workspace, changedParams, pendingCode, cost, status, stage, error,
        endedAt, lastEventAt };
    }),

  showRevision: (revision) => set({ shownRevision: revision }),
  setViewMode: (viewMode) => set({ viewMode }),
  setStepsOpen: (stepsOpen) => set({ stepsOpen }),
  dismissReply: (replyDismissedId) => set({ replyDismissedId }),
  clearError: () => set({ error: null }),
  setFocus: (focusId) => set({ focusId }),
  setFollow: (followAgent) => set({ followAgent, ...(followAgent ? { focusId: null } : {}) }),

  startReplay: async (id) => {
    clearTimeout(replayTimer);
    unsubscribe?.();
    unsubscribe = null;
    // A replay replaces the open workspace; end it so its CAD engine is freed.
    const live = get().sessionId;
    if (live) api.closeSession(live);
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
    void get().openWorkspace();
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

let previews: MeshPreview[] = [];
/** Object URLs for previewing dropped meshes before a session starts (the URL has no extension, so keep the kind). */
function setPreviews(files: File[]) {
  for (const preview of previews) URL.revokeObjectURL(preview.url);
  previews = files.map((file) => ({
    url: URL.createObjectURL(file),
    kind: file.name.toLowerCase().split('.').pop() as MeshPreview['kind'],
  }));
  useStore.setState({ meshPreviews: previews });
}

const EMPTY_MODEL: ModelState = {
  revision: 0, params: {}, features: [], placement: null, deviation: null, history: [], can_undo: false,
};

/** "<part>.<param>" for every parameter whose value or source changed between two snapshots. */
function changedKeys(before: WorkspaceState | null, next: WorkspaceState): string[] {
  const models: [string, ModelState][] = [...next.parts.map((p) => [p.name, p] as [string, ModelState]),
    ['assembly', next.assembly]];
  const old = new Map<string, ModelState>(before ? [...before.parts.map((p) => [p.name, p] as [string, ModelState]),
    ['assembly', before.assembly]] : []);
  const keys: string[] = [];
  for (const [name, model] of models) {
    const previous = old.get(name)?.params ?? {};
    for (const [param, value] of Object.entries(model.params)) {
      const was = previous[param];
      if (!was || JSON.stringify(was.value) !== JSON.stringify(value.value) || was.source !== value.source) {
        keys.push(`${name}.${param}`);
      }
    }
  }
  return keys;
}

/** The part a view should follow: the user's pick, else the agent's active part. */
export function partOf(workspace: WorkspaceState | null, selected: string | null): PartState | null {
  if (!workspace) return null;
  const name = selected ?? workspace.active;
  return workspace.parts.find((p) => p.name === name) ?? null;
}

/** Accuracy history for the chart: the followed part if it has a scan, else the first part that does. */
function deviationsOf(workspace: WorkspaceState, selected: string | null): Deviation[] {
  const followed = partOf(workspace, selected);
  const part = followed?.has_reference ? followed : workspace.parts.find((p) => p.has_reference);
  if (!part) return [];
  return part.history
    .filter((h) => h.deviation)
    .map((h) => ({ ...h.deviation!, trend: h.trend, revision: h.revision }));
}

/** The heatmap to show for a revision: the followed part's, else the first one computed. */
export function heatmapOf(revision: Revision | undefined, workspace: WorkspaceState | null, selected: string | null) {
  const heatmaps = revision?.heatmaps;
  if (!heatmaps) return undefined;
  const part = partOf(workspace, selected);
  return (part && heatmaps[part.name]) || Object.values(heatmaps)[0];
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
