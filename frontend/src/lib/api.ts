import type { ServerEvent } from './events';
import type { FeaJob, FeaSettings } from './fea';

async function json<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // FastAPI reports validation failures as a list of { msg } objects.
    const detail = body.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map((item: { msg: string }) => item.msg).join('; ') : '';
    throw new ApiError(message || `Request failed (${response.status})`, response.status);
  }
  return body as T;
}

/**
 * Create a workspace and start its session (CAD engine and agent). Everything is optional:
 * scans and photos become references, and a prompt starts the agent's first turn.
 */
export async function createSession(start: { units?: string; prompt?: string; name?: string; meshes?: File[];
  images?: File[] } = {}) {
  const form = new FormData();
  if (start.units) form.append('units', start.units);
  if (start.prompt) form.append('prompt', start.prompt);
  if (start.name) form.append('name', start.name);
  for (const mesh of start.meshes ?? []) form.append('mesh', mesh);
  for (const image of start.images ?? []) form.append('images', image);
  return json<{ session_id: string; parts: string[] }>(await fetch('/api/v1/cad-sessions', { method: 'POST', body: form }));
}

export type WorkspaceSummary = {
  id: string;
  name: string;
  /** False when the name was made up from the first prompt. */
  named: boolean;
  prompt: string;
  parts: string[];
  revisions: number;
  created_at: number;
  updated_at: number;
  thumbnail: string | null;
  status: 'open' | 'closed';
};

export async function listWorkspaces() {
  return json<WorkspaceSummary[]>(await fetch('/api/v1/workspaces'));
}
export async function getWorkspace(id: string) {
  return json<WorkspaceSummary>(await fetch(`/api/v1/workspaces/${encodeURIComponent(id)}`));
}
export async function renameWorkspace(id: string, name: string) {
  return json<WorkspaceSummary>(await fetch(`/api/v1/workspaces/${encodeURIComponent(id)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  }));
}
export async function deleteWorkspace(id: string) {
  return json(await fetch(`/api/v1/workspaces/${encodeURIComponent(id)}`, { method: 'DELETE' }));
}
/** Open a saved workspace: the running session, or a new one rebuilt from disk. */
export async function openWorkspace(id: string) {
  return json<{ session_id: string; reopened: boolean; units?: string | null }>(
    await fetch(`/api/v1/workspaces/${encodeURIComponent(id)}/open`, { method: 'POST' }));
}
/** Put the open workspace back to a saved version (one is saved per prompt that changed it). */
export async function restoreVersion(id: string, number: number) {
  return json<{ restored: number; notes: string[] }>(await fetch(
    `/api/v1/workspaces/${encodeURIComponent(id)}/versions/${number}/restore`, { method: 'POST' }));
}

export async function putThumbnail(id: string, png: Blob) {
  const response = await fetch(`/api/v1/workspaces/${encodeURIComponent(id)}/thumbnail.png`, {
    method: 'PUT', headers: { 'Content-Type': 'image/png' }, body: png,
  });
  if (!response.ok) throw new Error(`Thumbnail not saved (${response.status})`);
}

/** Errors carry the HTTP status so callers can tell "closed" (409) apart. */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/** Add meshes to a running workspace; each becomes a part. */
export async function uploadMeshes(sessionId: string, meshes: File[], units?: string) {
  const form = new FormData();
  for (const mesh of meshes) form.append('mesh', mesh);
  if (units) form.append('units', units);
  return json<{ parts: string[]; notes: string[] }>(
    await fetch(`/api/v1/cad-sessions/${sessionId}/meshes`, { method: 'POST', body: form }),
  );
}

/** Reference photos for the agent's next turn. */
export async function uploadImages(sessionId: string, images: File[]) {
  const form = new FormData();
  for (const image of images) form.append('images', image);
  return json<{ images: string[] }>(await fetch(`/api/v1/cad-sessions/${sessionId}/images`, { method: 'POST', body: form }));
}

/** End a session when the page goes away (a beacon survives the unload). */
export function closeSession(sessionId: string) {
  navigator.sendBeacon(`/api/v1/cad-sessions/${sessionId}/close`);
}

export async function sendTurn(sessionId: string, prompt: string) {
  return json(
    await fetch(`/api/v1/cad-sessions/${sessionId}/turns`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    }),
  );
}

export async function interrupt(sessionId: string) {
  return json(await fetch(`/api/v1/cad-sessions/${sessionId}/interrupt`, { method: 'POST' }));
}

export async function createFea(sessionId: string, revision: number, settings: FeaSettings) {
  return json<FeaJob>(await fetch(`/api/v1/cad-sessions/${sessionId}/fea`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ revision, ...settings }),
  }));
}

export async function getFea(sessionId: string, analysisId: string) {
  return json<FeaJob>(await fetch(`/api/v1/cad-sessions/${sessionId}/fea/${analysisId}`));
}

export type DownloadKind = 'step' | 'stl' | 'model.py';
export const downloadUrl = (sessionId: string, kind: DownloadKind) =>
  `/api/v1/cad-sessions/${sessionId}/download/${kind}`;

export type ReplaySummary = {
  id: string;
  prompt: string;
  revisions: number;
  events: number;
  started_at: number;
  duration_s: number;
};
export async function listReplays() {
  return json<ReplaySummary[]>(await fetch('/api/v1/replays/index.json'));
}
export async function replayEvents(id: string) {
  return json<ServerEvent[]>(await fetch(`/api/v1/replays/${encodeURIComponent(id)}/events.json`));
}

export async function listExamples() {
  return json<{ name: string; bytes: number }[]>(await fetch('/api/v1/examples'));
}

export async function fetchExample(name: string): Promise<File> {
  const response = await fetch(`/api/v1/examples/${encodeURIComponent(name)}`);
  if (!response.ok) throw new Error(`Example ${name} is not available`);
  return new File([await response.blob()], name);
}

/**
 * Subscribe to a session's events. Events arriving in the same animation frame are
 * delivered together, so a burst of tool events causes one React update, not dozens.
 */
export function subscribe(sessionId: string, onEvents: (events: ServerEvent[]) => void, onError: () => void) {
  const source = new EventSource(`/api/v1/cad-sessions/${sessionId}/events`);
  let buffer: ServerEvent[] = [];
  let frame = 0;
  let timer = 0;
  let lastSeq = 0;
  const flush = () => {
    cancelAnimationFrame(frame);
    clearTimeout(timer);
    frame = timer = 0;
    const batch = buffer;
    buffer = [];
    if (batch.length) onEvents(batch);
  };
  source.onmessage = (message) => {
    const event = JSON.parse(message.data) as ServerEvent;
    if (event.seq <= lastSeq) return; // replayed after a reconnect
    lastSeq = event.seq;
    buffer.push(event);
    if (!frame) {
      frame = requestAnimationFrame(flush);
      // Background tabs pause animation frames; the timer keeps state current anyway.
      timer = window.setTimeout(flush, 150);
    }
  };
  source.onerror = onError;
  return () => {
    cancelAnimationFrame(frame);
    clearTimeout(timer);
    source.close();
  };
}
