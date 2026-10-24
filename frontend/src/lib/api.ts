import type { ServerEvent } from './events';

async function json<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body as T;
}

/** Open a workspace session: the CAD engine and agent start now, with nothing uploaded yet. */
export async function createSession(units?: string) {
  const form = new FormData();
  if (units) form.append('units', units);
  return json<{ session_id: string; parts: string[] }>(await fetch('/api/v1/cad-sessions', { method: 'POST', body: form }));
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
