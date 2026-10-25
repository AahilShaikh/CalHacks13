import { useEffect, useState } from 'react';
import { useStore, type ToolItem } from '../store';
import { toolLabel, type Phase } from './events';
import { duration } from './format';

export { duration };

const STAGES: Record<string, string> = {
  preparing: 'Preparing workspace',
  cad_starting: 'Starting CAD engine',
  agent_running: 'Thinking',
  restoring: 'Rebuilding parts',
  exporting_mesh: 'Updating preview',
  reconnecting: 'Reconnecting',
  idle: 'Ready',
  scan_checking_comfy: 'Checking scanner',
  scan_capturing_front: 'Photographing front',
  scan_capturing_left: 'Photographing left',
  scan_capturing_back: 'Photographing back',
  scan_capturing_right: 'Photographing right',
  scan_uploading_images: 'Uploading scan photos',
  scan_generating_mesh_and_masks: 'Generating scan mesh',
  scan_fitting_metric_scale: 'Fitting metric dimensions',
  scan_rendering_blueprints: 'Rendering scan blueprints',
  scan_attaching_mesh: 'Adding scan to workspace',
  capturing_angle: 'Photographing object',
};

export type ActivityTone = Phase | 'idle' | 'busy' | 'paused' | 'error';

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

  if (status === 'failed') return { tone: 'error', label: replay ? 'Run failed' : 'Stopped with an error', working: false, running: null };
  if (status === 'done') return { tone: 'paused', label: replay ? 'Replay finished' : 'Paused', working: false, running: null };
  if (running) return { tone: running.phase, label: running.name === 'scan_object' ? STAGES[stage] ?? 'Scanning object' : toolLabel(running.name), working: true, running };
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

