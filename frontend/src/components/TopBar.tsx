import { useEffect, useState } from 'react';
import { PHASE_VERB } from '../lib/events';
import { generatedRevisions, useStore } from '../store';
import { DownloadMenu } from './DownloadMenu';

const CHIP = {
  inspect: 'bg-inspect/16 text-inspect',
  build: 'bg-build/16 text-build',
  verify: 'bg-verify/16 text-verify',
  idle: 'bg-muted/16 text-muted',
  error: 'bg-error/16 text-error',
} as const;
const DOT = {
  inspect: 'bg-inspect',
  build: 'bg-build',
  verify: 'bg-verify',
  idle: 'bg-muted',
  error: 'bg-error',
} as const;

const STAGES: Record<string, string> = {
  preparing: 'Preparing session',
  cad_starting: 'Starting CAD engine',
  agent_running: 'Thinking',
  exporting_mesh: 'Updating preview',
  reconnecting: 'Reconnecting',
  idle: 'Waiting for you',
};

function useElapsed(start: number | null, end: number | null) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!start || end) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [start, end]);
  if (!start) return null;
  const seconds = Math.max(0, Math.round((end ?? now) - start));
  return seconds >= 60 ? `${Math.floor(seconds / 60)}m ${seconds % 60}s` : `${seconds}s`;
}

/** Figma: TopBar (4:81). What the agent is doing now, plus revision, elapsed time and cost. */
export function TopBar() {
  const status = useStore((s) => s.status);
  const stage = useStore((s) => s.stage);
  const feed = useStore((s) => s.feed);
  const revisions = useStore((s) => s.revisions);
  const cost = useStore((s) => s.cost);
  const startedAt = useStore((s) => s.startedAt);
  const endedAt = useStore((s) => s.endedAt);
  const replay = useStore((s) => s.replay);
  const lastEventAt = useStore((s) => s.lastEventAt);
  // A replay's clock is the recorded timeline, not the wall clock.
  const elapsed = useElapsed(startedAt, endedAt ?? (replay ? lastEventAt ?? startedAt : null));

  const running = feed.findLast((i) => i.kind === 'tool' && i.status === 'running');
  let tone: keyof typeof CHIP = 'idle';
  let activity = 'Ready';
  if (status === 'failed') {
    tone = 'error';
    activity = 'Session failed';
  } else if (status === 'done') {
    activity = 'Session finished';
  } else if (running?.kind === 'tool') {
    tone = running.phase;
    activity = `${PHASE_VERB[running.phase]} · ${running.name}`;
  } else if (status === 'starting') {
    activity = 'Uploading mesh';
  } else if (stage) {
    activity = STAGES[stage] ?? stage;
  }

  const rev = generatedRevisions(revisions).length;
  const metrics = startedAt
    ? [`rev ${rev}`, elapsed, cost > 0 ? `$${cost.toFixed(2)}` : null].filter(Boolean).join(' · ')
    : 'no session';
  const live = tone !== 'idle' && tone !== 'error';

  return (
    <header className="flex h-[52px] shrink-0 items-center gap-3 border-b border-line bg-base px-4 py-2">
      <div className="flex size-6 items-center justify-center rounded-sm bg-cad text-[13px] font-semibold text-base">A</div>
      <span className="text-[14px] font-semibold">Aida</span>
      <span className="text-[13px] text-muted">CAD Studio</span>
      <div className="flex-1" />
      <div className={`flex items-center gap-2 rounded-lg px-3 py-1 text-[12px] font-medium ${CHIP[tone]}`}>
        <span className={`size-1.5 rounded-full ${DOT[tone]} ${live ? 'animate-pulse-dot' : ''}`} />
        {activity}
      </div>
      {replay && (
        <span className="rounded-sm bg-cad/16 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.6px] text-cad">
          Replay {replay.speed}×
        </span>
      )}
      <span className="font-mono text-[12px] text-muted">{metrics}</span>
      <DownloadMenu />
    </header>
  );
}
