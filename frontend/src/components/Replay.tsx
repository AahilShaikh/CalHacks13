import { Pause, Play, PlayCircle, RotateCcw, SkipForward, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { listReplays, type ReplaySummary } from '../lib/api';
import { useStore } from '../store';
import { IconButton, SectionLabel, cx } from './ui';

const SPEEDS = [1, 2, 4, 10];

/** Takes the prompt pill's place while a recorded session plays back. */
export function ReplayControls() {
  const replay = useStore((s) => s.replay);
  const { setReplay, skipReplay, exitReplay, startReplay } = useStore.getState();
  if (!replay) return null;
  const done = replay.index >= replay.total;
  const progress = replay.total ? (replay.index / replay.total) * 100 : 0;

  return (
    <div className="flex h-12 items-center gap-2 rounded-full border border-line-strong bg-surface pl-1.5 pr-2 shadow-float">
      <button
        type="button"
        onClick={() => (done ? void startReplay(replay.id) : setReplay({ playing: !replay.playing }))}
        aria-label={done ? 'Replay again' : replay.playing ? 'Pause' : 'Play'}
        className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent-ink text-white hover:bg-accent-ink-hover"
      >
        {done ? <RotateCcw size={16} aria-hidden /> : replay.playing
          ? <Pause size={15} fill="currentColor" strokeWidth={0} aria-hidden />
          : <Play size={15} fill="currentColor" strokeWidth={0} className="translate-x-px" aria-hidden />}
      </button>
      <div className="flex min-w-0 flex-1 flex-col gap-1 px-1">
        <span className="text-[12px] text-muted">
          {done ? 'Replay finished' : replay.playing ? 'Playing a recorded run' : 'Paused'}
        </span>
        <div className="h-1 overflow-hidden rounded-full bg-subtle" role="progressbar" aria-label="Replay progress"
          aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full rounded-full bg-accent transition-[width] duration-200" style={{ width: `${progress}%` }} />
        </div>
      </div>
      <div className="flex shrink-0 gap-0.5" role="radiogroup" aria-label="Replay speed">
        {SPEEDS.map((speed) => (
          <button
            key={speed}
            type="button"
            role="radio"
            aria-checked={replay.speed === speed}
            onClick={() => setReplay({ speed })}
            className={cx('h-7 rounded-md px-2 font-mono text-[11px]',
              replay.speed === speed ? 'bg-subtle font-medium text-ink' : 'text-faint hover:text-ink')}
          >
            {speed}×
          </button>
        ))}
      </div>
      <IconButton icon={SkipForward} label="Skip to the end" size="sm" onClick={skipReplay} disabled={done} />
      <IconButton icon={X} label="Exit the replay" size="sm" onClick={exitReplay} />
    </div>
  );
}

/** Past runs on this server. */
export function ReplayList() {
  const [replays, setReplays] = useState<ReplaySummary[]>([]);
  const startReplay = useStore((s) => s.startReplay);
  useEffect(() => {
    listReplays()
      .then((list) => setReplays(list.filter((r) => r.revisions > 0).slice(0, 5)))
      .catch(() => setReplays([]));
  }, []);
  if (!replays.length) return null;
  return (
    <div className="-mx-3 flex flex-col">
      <SectionLabel>Recorded runs</SectionLabel>
      {replays.map((r) => (
        <button
          key={r.id}
          type="button"
          onClick={() => void startReplay(r.id)}
          className="flex items-center gap-2.5 rounded-md px-3 py-2 text-left hover:bg-subtle"
        >
          <PlayCircle size={16} strokeWidth={1.75} className="shrink-0 text-faint" aria-hidden />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span className="truncate text-[12.5px] text-ink">{r.prompt || 'Untitled run'}</span>
            <span className="font-mono text-[11px] text-faint">
              {r.revisions} rev · {Math.max(1, Math.round(r.duration_s / 60))} min ·{' '}
              {new Date(r.started_at * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })}
            </span>
          </span>
        </button>
      ))}
    </div>
  );
}
