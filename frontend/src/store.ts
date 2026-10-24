import { create } from 'zustand';
import * as api from './lib/api';
import {
  argSummary,
  deviationFrom,
  featuresFrom,
  phaseOf,
  resultSummary,
  viewOf,
  type AgentView,
  type Deviation,
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
};

export type ViewMode = 'overlay' | 'side' | 'heatmap';
export type Tab = 'activity' | 'model';
export type AppStatus = 'idle' | 'starting' | SessionStatus;

type State = {
  // Before a session: what the user picked.
  meshFile: File | null;
  meshPreviewUrl: string | null;
  images: File[];
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
  pendingCode: string | null;
  // View.
  shownRevision: number | null; // null follows the latest generated revision
  viewMode: ViewMode;
  tab: Tab;
  /** A tool card the user clicked to see in 3D; null follows the agent (if followAgent). */
  focusId: number | null;
  followAgent: boolean;

  setMesh: (file: File | null) => void;
  addImages: (files: File[]) => void;
  removeImage: (index: number) => void;
  submit: (prompt: string) => Promise<void>;
  apply: (events: ServerEvent[]) => void;
  showRevision: (revision: number | null) => void;
  setViewMode: (mode: ViewMode) => void;
  setTab: (tab: Tab) => void;
  setFocus: (id: number | null) => void;
  setFollow: (follow: boolean) => void;
};

let unsubscribe: (() => void) | null = null;
let nextId = 1;

export const MESH_EXTENSIONS = ['.stl', '.obj', '.ply', '.glb'];
export const isMeshFile = (file: File) => MESH_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext));

export const useStore = create<State>((set, get) => ({
  meshFile: null,
  meshPreviewUrl: null,
  images: [],
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
  pendingCode: null,
  shownRevision: null,
  viewMode: 'overlay',
  tab: 'activity',
  focusId: null,
  followAgent: true,

  setMesh: (file) => {
    const previous = get().meshPreviewUrl;
    if (previous) URL.revokeObjectURL(previous);
    const preview = file && file.name.toLowerCase().endsWith('.stl') ? URL.createObjectURL(file) : null;
    set({ meshFile: file, meshPreviewUrl: preview, error: null });
  },
  addImages: (files) => set((s) => ({ images: [...s.images, ...files].slice(0, 8) })),
  removeImage: (index) => set((s) => ({ images: s.images.filter((_, i) => i !== index) })),

  submit: async (prompt) => {
    const { sessionId, status, meshFile, images } = get();
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
    set({ status: 'starting', error: null, feed: [userItem], revisions: [], deviations: [], features: [], cost: 0,
      focusId: null, followAgent: true });
    try {
      const { session_id } = await api.createSession(meshFile, text, images);
      unsubscribe?.();
      set({ sessionId: session_id, status: 'queued', startedAt: Date.now() / 1000, endedAt: null, shownRevision: null });
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
      let { feed, revisions, deviations, features, pendingCode, cost, status, stage, error, endedAt } = s;
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
            const dev = e.ok ? deviationFrom(e.name, e.text) : null;
            if (dev) deviations = [...deviations, dev];
            const f = e.ok ? featuresFrom(e.text) : null;
            if (f) features = f;
            break;
          }
          case 'mesh':
            revisions = [...revisions.filter((r) => r.revision !== e.revision), e].sort((a, b) => a.revision - b.revision);
            break;
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
        }
      }
      return { feed, revisions, deviations, features, pendingCode, cost, status, stage, error, endedAt };
    }),

  showRevision: (revision) => set({ shownRevision: revision }),
  setViewMode: (viewMode) => set({ viewMode }),
  setTab: (tab) => set({ tab }),
  setFocus: (focusId) => set({ focusId }),
  setFollow: (followAgent) => set({ followAgent, ...(followAgent ? { focusId: null } : {}) }),
}));

function note(text: string, tone: 'muted' | 'error' | 'ok'): FeedItem {
  return { kind: 'note', id: nextId++, text, tone };
}

function turnLine(e: Extract<ServerEvent, { type: 'turn_end' }>) {
  if (e.subtype === 'error_max_turns') return 'Turn limit reached. Ask Aida to continue.';
  if (e.is_error) return `Turn stopped (${e.subtype})`;
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
