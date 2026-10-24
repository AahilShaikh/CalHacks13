import { useEffect, useState } from 'react';
import playIcon from '../assets/icons/play.svg';
import { listReplays, type ReplaySummary } from '../lib/api';
import { useStore } from '../store';

const SPEEDS = [1, 2, 4, 10];

/** Replaces the prompt box while a recorded session plays back. */
export function ReplayControls() {
  const replay = useStore((s) => s.replay);
  const { setReplay, skipReplay, exitReplay, startReplay } = useStore.getState();
  if (!replay) return null;
  const done = replay.index >= replay.total;
  const progress = replay.total ? (replay.index / replay.total) * 100 : 0;

  return (
    <div className="flex shrink-0 flex-col gap-2 rounded-md border border-line-strong bg-sunken p-3">
      <div className="flex items-center gap-2 text-[12px]">
        <span className="rounded-sm bg-cad/16 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.6px] text-cad">Replay</span>
        <span className="text-muted">{done ? 'Finished' : replay.playing ? 'Playing a recorded run' : 'Paused'}</span>
        <span className="flex-1" />
        <button type="button" onClick={exitReplay} className="text-muted hover:text-ink">Exit</button>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-line-strong" role="progressbar" aria-valuenow={Math.round(progress)}>
        <div className="h-full bg-cad transition-[width] duration-200" style={{ width: `${progress}%` }} />
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => (done ? void startReplay(replay.id) : setReplay({ playing: !replay.playing }))}
          aria-label={done ? 'Replay again' : replay.playing ? 'Pause' : 'Play'}
          className="flex h-8 items-center gap-1 rounded-sm bg-cad px-3 text-[12px] font-semibold text-base hover:brightness-110"
        >
          {replay.playing ? (
            <span className="block h-3 w-3 border-x-[3px] border-base" />
          ) : (
            <img src={playIcon} alt="" width={14} height={14} className="invert" />
          )}
          {done ? 'Again' : replay.playing ? 'Pause' : 'Play'}
        </button>
        <div className="flex gap-1" role="radiogroup" aria-label="Replay speed">
          {SPEEDS.map((speed) => (
            <button
              key={speed}
              type="button"
              role="radio"
              aria-checked={replay.speed === speed}
              onClick={() => setReplay({ speed })}
              className={`rounded-sm px-2 py-1 font-mono text-[11px] ${replay.speed === speed ? 'bg-line-strong text-ink' : 'text-muted hover:text-ink'}`}
            >
              {speed}×
            </button>
          ))}
        </div>
        <span className="flex-1" />
        <button type="button" onClick={skipReplay} disabled={done} className="text-[12px] text-muted hover:text-ink disabled:opacity-40">
          Skip to end
        </button>
      </div>
    </div>
  );
}

/** Past runs on this server, for the empty state. */
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
    <div className="flex flex-col gap-1.5">
      <h3 className="text-[10px] font-semibold uppercase tracking-[0.6px] text-faint">Watch a past run</h3>
      {replays.map((r) => (
        <button
          key={r.id}
          type="button"
          onClick={() => void startReplay(r.id)}
          className="flex flex-col items-start gap-0.5 rounded-md border border-line bg-raised px-3 py-2 text-left hover:border-line-strong"
        >
          <span className="line-clamp-1 text-[12px] text-ink">{r.prompt || 'Untitled run'}</span>
          <span className="font-mono text-[10px] text-muted">
            {r.revisions} revision{r.revisions === 1 ? '' : 's'} · {Math.round(r.duration_s / 60)} min ·{' '}
            {new Date(r.started_at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}
          </span>
        </button>
      ))}
    </div>
  );
}
