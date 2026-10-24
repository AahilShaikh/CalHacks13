import type { ServerEvent } from './events';

async function json<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `Request failed (${response.status})`);
  return body as T;
}

export async function createSession(mesh: File, prompt: string, images: File[], units?: string) {
  const form = new FormData();
  form.append('mesh', mesh);
  form.append('prompt', prompt);
  if (units) form.append('units', units);
  for (const image of images) form.append('images', image);
  return json<{ session_id: string }>(await fetch('/api/v1/cad-sessions', { method: 'POST', body: form }));
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
