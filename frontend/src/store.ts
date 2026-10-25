import { create } from 'zustand';
import * as api from './lib/api';
import { feaBusy, type FeaJob, type FeaSettings } from './lib/fea';
import { duration } from './lib/format';
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
  type Question,
  type ReferenceItem,
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
  /** The design as it stood after a prompt: restorable, shown in the viewer at meshRevision. */
  | { kind: 'version'; id: number; number: number; meshRevision: number | null }
  /** Questions Aida asked (ask_user); ``answer`` is the user's next message. */
  | { kind: 'question'; id: number; qid: string; questions: Question[]; answer?: string }
  | ToolItem;

/** Questions waiting for the user's answer, shown above the prompt. */
export type PendingQuestion = { id: string; questions: Question[] };

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

export type ViewMode = 'overlay' | 'side' | 'heatmap' | 'fea-stress' | 'fea-displacement';
/** What the right panel shows: the agent's steps or the structural test. */
export type PanelTab = 'steps' | 'fea';
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
  sessionUnits: 'mm' | null;
  /** The latest FEA run (running or finished). */
  feaJob: FeaJob | null;
  /** Every finished run this session, oldest first: the design loop's history. */
  feaRuns: FeaJob[];
  feaStarting: boolean;
  feaError: string | null;
  /** The deformation view oscillates between the original and deformed shape. */
  feaAnimate: boolean;
  runFea: (settings: FeaSettings) => Promise<void>;
  setFeaAnimate: (animate: boolean) => void;
  /** Aida's unanswered questions; cleared by the user's next message. */
  question: PendingQuestion | null;
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
  /** Which view that panel shows. */
  panelTab: PanelTab;
  /** The agent message the user closed in the reply card; newer messages show again. */
  replyDismissedId: number | null;
  /** Parts the user hid in the 3D view (eye toggle in the explorer). */
  hiddenParts: string[];
  /** Scans and photos the agent can use, as the server last reported them. */
  references: ReferenceItem[];
  /** The version the design is at now: the latest saved, or the one restored. */
  currentVersion: number | null;
  /** A tool card the user clicked to see in 3D; null follows the agent (if followAgent). */
  focusId: number | null;
  followAgent: boolean;
  /** Playing back a recorded session; null when live. */
  replay: ReplayState | null;
  /** Time (unix s) of the latest applied event; the replay's clock. */
  lastEventAt: number | null;

  /** The workspace's name, and whether the user named it (otherwise it comes from the first prompt). */
  workspaceName: string;
  workspaceNamed: boolean;

  /** Open a saved (or running) workspace by id and follow its events. */
  openWorkspace: (id: string) => Promise<void>;
  /** Stop following the open workspace (it keeps running on the server until idle). */
  leaveWorkspace: () => void;
  renameWorkspace: (name: string) => Promise<void>;
  restoreVersion: (number: number) => Promise<void>;
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
  setPanelTab: (tab: PanelTab) => void;
  toggleHidden: (part: string) => void;
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
let openingId: string | null = null;

/**
 * Run a request against the open workspace. A paused workspace (closed while idle, or its
 * session ended) is reopened first, and a request refused because it closed meanwhile is
 * retried once after reopening.
 */
async function whileOpen<T>(request: () => Promise<T>): Promise<T> {
  const { sessionId, status, openWorkspace } = useStore.getState();
  if (!sessionId) throw new Error('No workspace is open.');
  if (status === 'done' || status === 'failed') await openWorkspace(sessionId);
  try {
    return await request();
  } catch (err) {
    if (!(err instanceof api.ApiError) || err.status !== 409) throw err;
    await openWorkspace(sessionId);
    return request();
  }
}
let nextId = 1;
let replayEvents: ServerEvent[] = [];
let replayTimer = 0;
// Longest pause kept between recorded events; long agent thinking is shortened.
const REPLAY_MAX_GAP_S = 3;

/** Session fields reset when a live session or a replay starts. */
const freshSession = {
  error: null, feed: [], revisions: [], deviations: [], features: [], cost: 0, workspace: null, selectedPart: null,
  changedParams: [], pendingCode: null, shownRevision: null, focusId: null, followAgent: true, replyDismissedId: null,
  hiddenParts: [], references: [], currentVersion: null,
  stage: '', startedAt: null, endedAt: null, lastEventAt: null,
  sessionUnits: null, feaJob: null, feaRuns: [], feaStarting: false, feaError: null, viewMode: 'overlay',
  question: null,
} satisfies Partial<State>;

const UNTITLED = 'Untitled workspace';

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
  sessionUnits: null,
  feaJob: null,
  feaRuns: [],
  feaStarting: false,
  feaError: null,
  feaAnimate: true,
  question: null,
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
  stepsOpen: typeof window === 'undefined' || window.innerWidth >= 1200,
  panelTab: 'steps',
  replyDismissedId: null,
  hiddenParts: [],
  references: [],
  currentVersion: null,
  focusId: null,
  followAgent: true,
  replay: null,
  lastEventAt: null,
  workspaceName: '',
  workspaceNamed: false,

  runFea: async (settings) => {
    const s = get();
    const revision = selectedRevision(s)?.revision;
    if (!s.sessionId || s.replay || revision === undefined || s.feaStarting || feaBusy(s.feaJob)) return;
    const sessionId = s.sessionId;
    set({ feaStarting: true, feaError: null });
    try {
      // Progress and the result arrive as session events (fea_started, fea_stage, fea_done).
      await api.createFea(sessionId, revision, settings);
      if (get().sessionId === sessionId) set({ feaStarting: false });
    } catch (err) {
      if (get().sessionId === sessionId) set({ feaStarting: false, feaError: (err as Error).message });
    }
  },
  setFeaAnimate: (feaAnimate) => set({ feaAnimate }),

  openWorkspace: async (id) => {
    if (opening && openingId === id) return opening;
    openingId = id;
    opening = (async () => {
      clearTimeout(replayTimer);
      unsubscribe?.();
      unsubscribe = null;
      set({ ...freshSession, status: 'starting', sessionId: id, meshFiles: [], replay: null });
      setPreviews([]);
      try {
        // Rebuilds the workspace from disk if it is not running; its history streams back in.
        const opened = await api.openWorkspace(id);
        if (get().sessionId !== id) return; // the user moved on meanwhile
        set({ status: 'queued', startedAt: Date.now() / 1000, sessionUnits: opened.units === 'mm' ? 'mm' : null });
        unsubscribe = api.subscribe(
          id,
          (events) => get().apply(events),
          () => {
            if (get().status === 'running') set({ stage: 'reconnecting' });
          },
        );
        api.getWorkspace(id)
          .then((w) => get().sessionId === id && set({ workspaceName: w.name, workspaceNamed: w.named }))
          .catch(() => {});
      } catch (err) {
        const missing = err instanceof api.ApiError && err.status === 404;
        set({ status: 'failed', error: missing ? 'This workspace no longer exists.'
          : `Could not open the workspace: ${(err as Error).message}` });
      } finally {
        opening = null;
        openingId = null;
      }
    })();
    return opening;
  },
  restoreVersion: async (number) => {
    const { sessionId } = get();
    if (!sessionId) return;
    set({ error: null });
    try {
      await whileOpen(() => api.restoreVersion(sessionId, number));
      set({ shownRevision: null }); // follow the restored shape
    } catch (err) {
      set({ error: `Could not restore version ${number}: ${(err as Error).message}` });
    }
  },
  leaveWorkspace: () => {
    clearTimeout(replayTimer);
    unsubscribe?.();
    unsubscribe = null;
    setPreviews([]);
    set({ ...freshSession, sessionId: null, status: 'idle', replay: null, meshFiles: [], images: [],
      workspaceName: '', workspaceNamed: false });
  },
  renameWorkspace: async (name) => {
    const { sessionId } = get();
    const text = name.trim();
    if (!sessionId || !text) return;
    const before = { workspaceName: get().workspaceName, workspaceNamed: get().workspaceNamed };
    set({ workspaceName: text, workspaceNamed: true });
    try {
      await api.renameWorkspace(sessionId, text);
    } catch (err) {
      set({ ...before, error: `Could not rename: ${(err as Error).message}` });
    }
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
      set({ error: 'No workspace is open.' });
      return;
    }
    try {
      await whileOpen(() => api.uploadMeshes(sessionId, added, units || undefined));
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
      set({ error: 'No workspace is open.' });
      return;
    }
    set({ error: null });
    try {
      // The prompt shows once the server records it (user_prompt), so history and live agree.
      await whileOpen(async () => {
        // Photos go to the agent with this message.
        if (images.length) {
          await api.uploadImages(sessionId, images);
          set({ images: [] });
        }
        await api.sendTurn(sessionId, text);
      });
    } catch (err) {
      set((s) => ({ feed: [...s.feed, note(`Message not sent: ${(err as Error).message}`, 'error')] }));
    }
  },

  apply: (events) =>
    set((s) => {
      let { feed, revisions, deviations, features, workspace, changedParams, pendingCode, cost, status, stage, error, endedAt,
        references, workspaceName, currentVersion, feaJob, feaRuns, feaError, question, panelTab, stepsOpen } = s;
      feed = [...feed];
      // FEA run to show as stress once applied; only for fresh events, not a reopened history.
      let showFea: number | null = null;
      const fresh = (at: number) => Boolean(s.replay) || Date.now() / 1000 - at < 60;
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
          case 'references':
            references = e.items;
            break;
          case 'version':
            feed.push({ kind: 'version', id: nextId++, number: e.number, meshRevision: e.mesh_revision });
            currentVersion = e.number;
            break;
          case 'version_restored':
            feed.push(note(`Restored version ${e.number}`, 'ok'));
            currentVersion = e.number;
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
            feed.push(note('Workspace paused', 'muted'));
            break;
          case 'fea_started': {
            const { type: _t, seq: _s, at: _a, ...job } = e;
            feaJob = job;
            feaError = null;
            if (fresh(e.at)) {
              panelTab = 'fea';
              stepsOpen = true;
            }
            break;
          }
          case 'fea_stage':
            if (feaJob?.analysis_id === e.analysis_id) feaJob = { ...feaJob, status: 'running', stage: e.stage };
            break;
          case 'fea_done':
          case 'fea_failed': {
            const { type: _t, seq: _s, at: _a, ...job } = e;
            feaJob = job;
            feaError = job.error;
            if (e.type === 'fea_done') {
              feaRuns = [...feaRuns.filter((r) => r.analysis_id !== job.analysis_id), job];
              if (fresh(e.at)) showFea = job.revision;
            }
            break;
          }
          case 'question':
            feed.push({ kind: 'question', id: nextId++, qid: e.id, questions: e.questions });
            question = { id: e.id, questions: e.questions };
            break;
          case 'user_prompt':
            if (question) {
              const asked = feed.findLastIndex((i) => i.kind === 'question' && i.qid === question!.id);
              const item = feed[asked];
              if (item?.kind === 'question') feed[asked] = { ...item, answer: e.text };
              question = null;
            }
            feed.push({ kind: 'user', id: nextId++, text: e.text });
            // An unnamed workspace goes by its first prompt (as on the home screen).
            if (!s.replay && !s.workspaceNamed && (!workspaceName || workspaceName === UNTITLED)) {
              workspaceName = e.text.slice(0, 60).trim();
            }
            break;
          case 'interrupted':
            feed.push(note('Stopped. Send a message to continue.', 'muted'));
            break;
        }
      }
      const lastEventAt = events.length ? events[events.length - 1].at : s.lastEventAt;
      const after = { ...s, revisions, feaRuns };
      let viewMode = s.viewMode.startsWith('fea-') && !activeFea(after) ? 'overlay' : s.viewMode;
      // A run that just finished on the revision in view: show its stress.
      if (showFea !== null && selectedRevision(after)?.revision === showFea && !viewMode.startsWith('fea-')) {
        viewMode = 'fea-stress';
      }
      return { feed, revisions, deviations, features, workspace, changedParams, pendingCode, cost, status, stage, error,
        endedAt, lastEventAt, references, workspaceName, currentVersion, viewMode, feaJob, feaRuns, feaError, question,
        panelTab, stepsOpen };
    }),

  showRevision: (shownRevision) => set((s) => ({ shownRevision,
    ...(s.viewMode.startsWith('fea-') && !activeFea({ ...s, shownRevision }) ? { viewMode: 'overlay' as const } : {}),
  })),
  setViewMode: (viewMode) => set({ viewMode }),
  setStepsOpen: (stepsOpen) => set({ stepsOpen }),
  setPanelTab: (panelTab) => set({ panelTab }),
  toggleHidden: (part) =>
    set((s) => ({ hiddenParts: s.hiddenParts.includes(part) ? s.hiddenParts.filter((p) => p !== part) : [...s.hiddenParts, part] })),
  dismissReply: (replyDismissedId) => set({ replyDismissedId }),
  clearError: () => set({ error: null }),
  setFocus: (focusId) => set({ focusId }),
  setFollow: (followAgent) => set({ followAgent, ...(followAgent ? { focusId: null } : {}) }),

  startReplay: async (id) => {
    clearTimeout(replayTimer);
    unsubscribe?.();
    unsubscribe = null;
    // The open workspace (if any) keeps running on the server and frees itself when idle.
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
  if (e.subtype === 'error_max_turns') return 'Aida reached its step limit. Ask it to continue.';
  if (e.is_error) return e.subtype === 'success' ? 'Aida stopped with an error; see its reply.' : `Aida stopped (${e.subtype.replaceAll('_', ' ')})`;
  const parts = ['Done'];
  if (e.duration_s) parts.push(duration(e.duration_s));
  if (typeof e.cost_usd === 'number' && e.cost_usd > 0) parts.push(`$${e.cost_usd.toFixed(2)}`);
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
/** The number people see for a CAD revision: its place among generated revisions, from 1. */
export function revisionNumber(revisions: Revision[], revision: number | undefined) {
  if (revision === undefined) return undefined;
  const index = generatedRevisions(revisions).findIndex((r) => r.revision === revision);
  return index < 0 ? revision : index + 1;
}
export const referenceRevision =(revisions: Revision[]) => revisions.find((r) => !r.generated) ?? null;

export function selectedRevision(s: Pick<State, 'revisions' | 'shownRevision'>) {
  const generated = generatedRevisions(s.revisions);
  return s.shownRevision === null ? generated.at(-1) : generated.find((r) => r.revision === s.shownRevision);
}

/** The latest result for the CAD revision in view: results show only on the exact revision tested. */
export function activeFea(s: Pick<State, 'feaRuns' | 'revisions' | 'shownRevision'>) {
  const revision = selectedRevision(s)?.revision;
  return s.feaRuns.findLast((run) => run.result?.revision === revision)?.result ?? null;
}
