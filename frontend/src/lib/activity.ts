import { useEffect, useState } from 'react';
import { useStore, type ToolItem } from '../store';
import { toolLabel, type Phase } from './events';

const STAGES: Record<string, string> = {
  preparing: 'Preparing workspace',
  cad_starting: 'Starting CAD engine',
  agent_running: 'Thinking',
  exporting_mesh: 'Updating preview',
  reconnecting: 'Reconnecting',
  idle: 'Ready',
};

export type ActivityTone = Phase | 'idle' | 'busy' | 'error';

export type Activity = {
  tone: ActivityTone;
  label: string;
  /** A turn is running: the agent is thinking or calling tools. */
  working: boolean;
  /** The tool call in progress, if any. */
  running: ToolItem | null;
};

/** What the agent is doing right now, in one line. */
export function useActivity(): Activity {
  const status = useStore((s) => s.status);
  const stage = useStore((s) => s.stage);
  const replay = useStore((s) => s.replay);
  const running = useStore((s) => {
    const item = s.feed.findLast((i) => i.kind === 'tool' && i.status === 'running');
    return item?.kind === 'tool' ? item : null;
  });

  const live = status === 'running' || status === 'queued';
  // The session stays open between turns; only "working" while a turn runs.
  const working = live && !['', 'idle', 'preparing', 'cad_starting'].includes(stage);

  if (status === 'failed') return { tone: 'error', label: 'Session failed', working: false, running: null };
  if (status === 'done') return { tone: 'idle', label: replay ? 'Replay finished' : 'Session ended', working: false, running: null };
  if (running) return { tone: running.phase, label: toolLabel(running.name), working: true, running };
  if (status === 'starting') return { tone: 'busy', label: 'Opening workspace', working: false, running: null };
  if (stage) {
    const busy = stage !== 'idle';
    return { tone: busy ? 'busy' : 'idle', label: STAGES[stage] ?? stage, working, running: null };
  }
  return { tone: 'idle', label: 'Ready', working: false, running: null };
}

/** "1m 05s" since start (or until end), ticking while open. */
export function useElapsed(start: number | null, end: number | null) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!start || end) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [start, end]);
  if (!start) return null;
  return duration(Math.max(0, (end ?? now) - start));
}

export function duration(seconds: number) {
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  const s = Math.round(seconds);
  return s >= 60 ? `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`;
}
