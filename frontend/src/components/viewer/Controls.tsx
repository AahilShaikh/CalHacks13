import { useEffect, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import { generatedRevisions, heatmapOf, useStore, type ViewMode } from '../../store';

const MODES: { id: ViewMode; label: string }[] = [
  { id: 'overlay', label: 'Overlay' },
  { id: 'side', label: 'Side by side' },
  { id: 'heatmap', label: 'Heatmap' },
];

/** The generated revision the viewer is showing (the latest unless the user scrubbed back). */
function useShownRevision() {
  const revisions = useStore((s) => s.revisions);
  const shownRevision = useStore((s) => s.shownRevision);
  const generated = generatedRevisions(revisions);
  return shownRevision === null ? generated.at(-1) : generated.find((r) => r.revision === shownRevision);
}

/** The shown revision's heatmap for the followed part, if computed. */
function useShownHeatmap() {
  const shown = useShownRevision();
  const workspace = useStore((s) => s.workspace);
  const selected = useStore((s) => s.selectedPart);
  return heatmapOf(shown, workspace, selected);
}

/** Figma: ViewModeToggle (4:54). */
export function ViewModeToggle() {
  const viewMode = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);
  const hasHeat = Boolean(useShownHeatmap());
  const ready = (id: ViewMode) => id !== 'heatmap' || hasHeat;
  return (
    <div role="radiogroup" aria-label="View mode" className="flex gap-1 rounded-md border border-line bg-surface/95 p-1 backdrop-blur">
      {MODES.map((m) => (
        <button
          key={m.id}
          role="radio"
          aria-checked={viewMode === m.id}
          disabled={!ready(m.id)}
          title={ready(m.id) ? undefined : 'The heatmap appears once this revision has been compared with the scan'}
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

/** Whether the camera and slice planes mirror the agent, or a step the user picked. */
export function FollowControl() {
  const followAgent = useStore((s) => s.followAgent);
  const focusId = useStore((s) => s.focusId);
  const feed = useStore((s) => s.feed);
  const setFollow = useStore((s) => s.setFollow);
  const focused = focusId === null ? null : feed.find((i) => i.id === focusId);
  if (focused?.kind === 'tool') {
    return (
      <button type="button" onClick={() => setFollow(true)}
        className="flex items-center gap-2 rounded-md border border-accent bg-surface/95 px-3 py-2 text-[12px] text-ink backdrop-blur hover:bg-surface">
        <span className="font-mono">{focused.name}</span>
        <span className="text-muted">{focused.argText}</span>
        <span className="text-muted">· Back to live ×</span>
      </button>
    );
  }
  return (
    <button type="button" aria-pressed={followAgent} onClick={() => setFollow(!followAgent)}
      title={followAgent ? 'The camera follows what the agent looks at. Drag to look around yourself.' : 'Follow the agent camera'}
      className={`flex items-center gap-2 rounded-md border px-3 py-2 text-[12px] backdrop-blur ${
        followAgent ? 'border-accent/60 bg-accent-soft text-accent-ink' : 'border-line bg-surface/95 text-muted hover:text-ink'
      }`}>
      <span className={`size-2 rounded-full ${followAgent ? 'bg-accent animate-pulse-dot' : 'bg-muted'}`} />
      {followAgent ? 'Following agent' : 'Follow agent'}
    </button>
  );
}

export function Legend() {
  const viewMode = useStore((s) => s.viewMode);
  const heat = useShownHeatmap();
  if (viewMode === 'heatmap' && heat) return <HeatLegend range={heat.range} threshold={heat.threshold} share={heat.share_within} />;
  return (
    <div className="flex items-center gap-3 rounded-md border border-line bg-surface/95 px-3 py-2 text-[12px] text-muted backdrop-blur">
      <span className="size-2 rounded-full bg-mesh" /> Reference mesh
      <span className="size-2 rounded-full bg-cad" /> Generated CAD
    </div>
  );
}

const n = (v: number) => (v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toFixed(2));

/** Colour scale for the heatmap: same blue-white-red as compare_to_mesh's images. */
function HeatLegend({ range, threshold, share }: { range: number; threshold: number; share: number }) {
  return (
    <div className="flex w-64 flex-col gap-1.5 rounded-md border border-line bg-surface/95 px-3 py-2 text-[11px] text-muted backdrop-blur">
      <div className="flex items-center justify-between">
        <span className="font-medium text-ink">Distance to scan</span>
        <span>{Math.round(share * 100)}% within ±{n(threshold)}</span>
      </div>
      <div className="h-2 rounded-full" style={{ background: 'linear-gradient(90deg, #2659d9, #ffffff, #d9261f)' }} />
      <div className="flex justify-between font-mono text-[10px]">
        <span>−{n(range)}</span>
        <span>0</span>
        <span>+{n(range)}</span>
      </div>
      <div className="flex justify-between text-[10px]">
        <span className="text-[#2659d9]">CAD has extra</span>
        <span className="text-[#c0261f]">CAD is missing</span>
      </div>
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
    <div className="flex h-10 w-[560px] max-w-[calc(100%-32px)] items-center gap-3 rounded-md border border-line bg-surface/95 px-3 py-2 backdrop-blur">
      <button
        type="button"
        aria-label={playing ? 'Pause replay' : 'Replay the build'}
        onClick={() => {
          if (!playing && index >= count - 1) showRevision(generated[0].revision);
          setPlaying(!playing);
        }}
        className="flex size-6 items-center justify-center rounded-sm text-ink hover:bg-subtle"
      >
        {playing ? <Pause size={14} fill="currentColor" aria-hidden /> : <Play size={14} fill="currentColor" aria-hidden />}
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
        className="h-1 flex-1 cursor-pointer accent-accent-ink"
      />
      <span className="font-mono text-[11px] text-muted">
        rev {index + 1} / {count}
      </span>
    </div>
  );
}
