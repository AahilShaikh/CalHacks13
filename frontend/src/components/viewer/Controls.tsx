import { useEffect, useState } from 'react';
import playIcon from '../../assets/icons/play.svg';
import { generatedRevisions, useStore, type ViewMode } from '../../store';

const MODES: { id: ViewMode; label: string; ready: boolean }[] = [
  { id: 'overlay', label: 'Overlay', ready: true },
  { id: 'side', label: 'Side by side', ready: true },
  { id: 'heatmap', label: 'Heatmap', ready: false },
];

/** Figma: ViewModeToggle (4:54). */
export function ViewModeToggle() {
  const viewMode = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);
  return (
    <div role="radiogroup" aria-label="View mode" className="flex gap-1 rounded-md border border-line bg-raised/90 p-1 backdrop-blur">
      {MODES.map((m) => (
        <button
          key={m.id}
          role="radio"
          aria-checked={viewMode === m.id}
          disabled={!m.ready}
          title={m.ready ? undefined : 'Deviation heatmap is next on the roadmap'}
          onClick={() => setViewMode(m.id)}
          className={`rounded-sm px-3 py-1 text-[12px] font-medium ${
            viewMode === m.id ? 'bg-line-strong text-ink' : 'text-muted hover:text-ink'
          } disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:text-muted`}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}

export function Legend() {
  return (
    <div className="flex items-center gap-3 rounded-md border border-line bg-raised/90 px-3 py-2 text-[12px] text-muted backdrop-blur">
      <span className="size-2 rounded-full bg-mesh" /> Reference mesh
      <span className="size-2 rounded-full bg-cad" /> Generated CAD
    </div>
  );
}

/** Figma: RevisionScrubber (4:55). Step through every generated CAD revision; play replays the build. */
export function RevisionScrubber() {
  const revisions = useStore((s) => s.revisions);
  const shownRevision = useStore((s) => s.shownRevision);
  const showRevision = useStore((s) => s.showRevision);
  const [playing, setPlaying] = useState(false);
  const generated = generatedRevisions(revisions);
  const count = generated.length;
  const index = shownRevision === null ? count - 1 : generated.findIndex((r) => r.revision === shownRevision);

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      const { revisions: all, shownRevision: current } = useStore.getState();
      const list = generatedRevisions(all);
      const at = current === null ? list.length - 1 : list.findIndex((r) => r.revision === current);
      if (at >= list.length - 1) {
        setPlaying(false);
        showRevision(null); // back to following the latest revision
      } else {
        showRevision(list[at + 1].revision);
      }
    }, 700);
    return () => clearInterval(timer);
  }, [playing, showRevision]);

  if (count < 2) return null;

  return (
    <div className="flex h-10 w-[560px] max-w-[calc(100%-32px)] items-center gap-3 rounded-md border border-line bg-raised/90 px-3 py-2 backdrop-blur">
      <button
        type="button"
        aria-label={playing ? 'Pause replay' : 'Replay the build'}
        onClick={() => {
          if (!playing && index >= count - 1) showRevision(generated[0].revision);
          setPlaying(!playing);
        }}
        className="flex size-5 items-center justify-center opacity-90 hover:opacity-100"
      >
        {playing ? <span className="block h-3 w-3 border-x-[3px] border-ink" /> : <img src={playIcon} alt="" width={14} height={14} />}
      </button>
      <input
        type="range"
        min={0}
        max={count - 1}
        value={Math.max(0, index)}
        aria-label="Revision"
        onChange={(e) => {
          setPlaying(false);
          const i = Number(e.target.value);
          showRevision(i === count - 1 ? null : generated[i].revision);
        }}
        className="h-1 flex-1 cursor-pointer accent-cad"
      />
      <span className="font-mono text-[11px] text-muted">
        rev {index + 1} / {count}
      </span>
    </div>
  );
}
