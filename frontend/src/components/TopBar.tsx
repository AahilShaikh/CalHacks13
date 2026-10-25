import { ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { useActivity, useElapsed, type ActivityTone } from '../lib/activity';
import { linkProps } from '../lib/router';
import { useStore } from '../store';
import { DownloadMenu } from './DownloadMenu';
import { CommitScrubber } from './viewer/Controls';
import { RenameField, Spinner, cx } from './ui';

const DOT: Record<ActivityTone, string> = {
  inspect: 'bg-inspect',
  build: 'bg-accent',
  verify: 'bg-verify',
  busy: 'bg-faint',
  paused: 'bg-line-strong',
  idle: 'bg-ok',
  error: 'bg-error',
};

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill="#18181b" />
      <path d="M9 23 16 8l7 15M11.6 18h8.8" fill="none" stroke="#f26b1d" strokeWidth="2.6" strokeLinecap="round"
        strokeLinejoin="round" />
    </svg>
  );
}

/** Workspace header: where you are, what the agent is doing, and export. */
export function TopBar() {
  const replay = useStore((s) => s.replay);

  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line bg-surface px-3">
      <a {...linkProps('/')} className="flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-subtle" aria-label="All workspaces">
        <Logo />
        <span className="text-[14px] font-semibold tracking-[-0.01em]">Aida</span>
      </a>
      <ChevronRight size={14} className="text-line-strong" aria-hidden />
      <div className="w-64 min-w-0 max-w-[256px] shrink">{replay ? <span className="truncate text-[13px] font-medium">Recorded run</span> : <WorkspaceName />}</div>
      {replay && (
        <span className="rounded-sm bg-accent-soft px-1.5 py-0.5 text-[11px] font-medium text-accent-ink">
          Replay · {replay.speed}×
        </span>
      )}

      <div className="flex min-w-0 flex-1 items-center justify-center gap-3">
        <StatusChip />
        <CommitScrubber />
      </div>

      <DownloadMenu />
    </header>
  );
}

/** The workspace's name; click to rename. Unnamed workspaces show their first prompt. */
function WorkspaceName() {
  const name = useStore((s) => s.workspaceName);
  const named = useStore((s) => s.workspaceNamed);
  const sessionId = useStore((s) => s.sessionId);
  const rename = useStore((s) => s.renameWorkspace);
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <RenameField
        className="min-w-0 text-[13px]"
        initial={named ? name : ''}
        placeholder={name || 'Untitled workspace'}
        onDone={(text) => {
          setEditing(false);
          if (text) void rename(text);
        }}
      />
    );
  }
  return (
    <button
      type="button"
      disabled={!sessionId}
      onClick={() => setEditing(true)}
      title="Rename"
      className="max-w-full min-w-0 truncate rounded-md px-1.5 py-1 text-[13px] font-medium hover:bg-subtle"
    >
      {name || 'Untitled workspace'}
    </button>
  );
}

/** The agent's current step; hover or focus for revision, time and cost. */
function StatusChip() {
  const activity = useActivity();
  const cost = useStore((s) => s.cost);
  const startedAt = useStore((s) => s.startedAt);
  const endedAt = useStore((s) => s.endedAt);
  const replay = useStore((s) => s.replay);
  const lastEventAt = useStore((s) => s.lastEventAt);
  // A replay's clock is the recorded timeline, not the wall clock.
  const elapsed = useElapsed(startedAt, endedAt ?? (replay ? lastEventAt ?? startedAt : null));
  const rev = useStore((s) => s.currentCommit);
  const details = [
    ['Commit', rev ? String(rev) : '—'],
    ['Session time', elapsed ?? '—'],
    ['Agent cost', cost > 0 ? `$${cost.toFixed(2)}` : '—'],
  ];

  return (
    <div className="group relative">
      <button
        type="button"
        aria-describedby="status-details"
        className="flex h-7 items-center gap-2 rounded-full border border-line bg-surface px-3 text-[12px] font-medium text-ink hover:border-line-strong"
      >
        {activity.working || activity.tone === 'busy' ? (
          <Spinner size={12} className={activity.tone === 'busy' ? 'text-faint' : 'text-accent'} />
        ) : (
          <span className={cx('size-1.5 rounded-full', DOT[activity.tone])} aria-hidden />
        )}
        <span aria-live="polite">{activity.label}</span>
      </button>
      <div
        id="status-details"
        role="tooltip"
        className="invisible absolute left-1/2 top-full z-40 mt-2 w-48 -translate-x-1/2 rounded-lg border border-line bg-surface p-2 opacity-0 shadow-pop transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
      >
        {details.map(([label, value]) => (
          <div key={label} className="flex items-center justify-between px-1 py-1 text-[12px]">
            <span className="text-muted">{label}</span>
            <span className="font-mono text-ink">{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
