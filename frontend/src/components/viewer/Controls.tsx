import { ChevronDown, Crosshair, Pause, Play } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toolLabel, type Deviation } from '../../lib/events';
import { FEA_GRADIENT, feaNumber, type FeaResult } from '../../lib/fea';
import { activeFea, selectedRevision, heatmapOf, geometryLabel, commitHistory, useStore, type ViewMode } from '../../store';
import { Badge, FloatCard, cx, type Tone } from '../ui';

const MODES: { id: ViewMode; label: string }[] = [
  { id: 'overlay', label: 'Overlay' },
  { id: 'side', label: 'Side by side' },
  { id: 'heatmap', label: 'Heatmap' },
  { id: 'fea-stress', label: 'Stress' },
  { id: 'fea-displacement', label: 'Deformation' },
];

/** The generated revision the viewer is showing (the latest unless the user scrubbed back). */
function useShownRevision() {
  return useStore(selectedRevision);
}

/** The shown revision's heatmap for the followed part, if computed. */
function useShownHeatmap() {
  const shown = useShownRevision();
  const workspace = useStore((s) => s.workspace);
  const selected = useStore((s) => s.selectedPart);
  return heatmapOf(shown, workspace, selected);
}

export function ViewModeToggle() {
  const viewMode = useStore((s) => s.viewMode);
  const setViewMode = useStore((s) => s.setViewMode);
  const hasHeat = Boolean(useShownHeatmap());
  const fea = useStore(activeFea);
  const ready = (id: ViewMode) => id === 'heatmap' ? hasHeat : id.startsWith('fea-') ? Boolean(fea) : true;
  return (
    <FloatCard className="flex gap-0.5 p-0.5">
      <div role="radiogroup" aria-label="View mode" className="contents">
        {MODES.map((m) => (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={viewMode === m.id}
            disabled={!ready(m.id)}
            title={ready(m.id) ? undefined : m.id.startsWith('fea-') ? 'Run FEA on this design first' : 'Available once this geometry has been compared with the scan'}
            onClick={() => { setViewMode(m.id); if (m.id.startsWith('fea-')) useStore.getState().setFollow(false); }}
            className={cx('h-7 rounded-md px-2.5 text-[12px] font-medium transition-colors',
              viewMode === m.id ? 'bg-ink text-white' : 'text-muted hover:bg-subtle hover:text-ink',
              'disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent')}
          >
            {m.label}
          </button>
        ))}
      </div>
    </FloatCard>
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
      <FloatCard className="border-accent/50">
        <button type="button" onClick={() => setFollow(true)}
          className="flex h-8 items-center gap-2 px-3 text-[12px] text-ink">
          <span className="size-1.5 rounded-full bg-accent" aria-hidden />
          <span className="font-medium">{toolLabel(focused.name)}</span>
          <span className="font-mono text-[11px] text-faint">{focused.argText}</span>
          <span className="text-accent-ink">Back to live</span>
        </button>
      </FloatCard>
    );
  }
  return (
    <FloatCard>
      <button type="button" aria-pressed={followAgent} onClick={() => setFollow(!followAgent)}
        title="Follow Aida’s inspection camera and slice planes. Drag the camera to stop following. Nothing moves until Aida inspects a view."
        className={cx('flex h-8 items-center gap-2 rounded-lg px-3 text-[12px] font-medium',
          followAgent ? 'text-accent-ink' : 'text-muted hover:text-ink')}>
        <Crosshair size={14} strokeWidth={1.75} aria-hidden />
        {followAgent ? 'Following Aida’s camera' : 'Follow Aida’s camera'}
      </button>
    </FloatCard>
  );
}

export function Legend() {
  const viewMode = useStore((s) => s.viewMode);
  const hasReference = useStore((s) => s.revisions.some((r) => !r.generated));
  const heat = useShownHeatmap();
  const fea = useStore(activeFea);
  if (fea && (viewMode === 'fea-stress' || viewMode === 'fea-displacement')) return <FeaLegend result={fea} deformed={viewMode === 'fea-displacement'} />;
  if (viewMode === 'heatmap' && heat) return <HeatLegend range={heat.range} threshold={heat.threshold} share={heat.share_within} />;
  if (!hasReference) return null;
  return (
    <FloatCard className="flex items-center gap-3 px-3 py-1.5 text-[11px] text-muted">
      <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-mesh/60" /> Scan</span>
      <span className="flex items-center gap-1.5"><span className="size-2 rounded-full bg-cad" /> CAD</span>
    </FloatCard>
  );
}

function FeaLegend({ result, deformed }: { result: FeaResult; deformed: boolean }) {
  const animate = useStore((s) => s.feaAnimate);
  const feed = useStore((s) => s.feed);
  const setAnimate = useStore((s) => s.setFeaAnimate);
  // Where yield sits on the stress scale (the scale's top is the peak stress).
  const yieldAt = result.settings.yield_strength_mpa / result.max_von_mises_mpa;
  return <FloatCard className="flex w-64 flex-col gap-1.5 px-3 py-2 text-[11px] text-muted">
    <div className="flex items-center justify-between font-medium text-ink">
      <span>{deformed ? 'Displacement · mm' : 'von Mises stress · MPa'}</span>
      {deformed ? (
        <button type="button" onClick={() => setAnimate(!animate)} aria-label={animate ? 'Pause animation' : 'Play animation'}
          className="flex items-center gap-1 rounded px-1 text-[10.5px] font-normal text-accent-ink hover:bg-subtle">
          {animate ? <Pause size={11} aria-hidden /> : <Play size={11} aria-hidden />}{animate ? 'Pause' : 'Play'}
        </button>
      ) : <span>{geometryLabel({ feed }, result.revision)}</span>}
    </div>
    <div className="relative h-2 rounded-full" style={{ background: FEA_GRADIENT }}>
      {!deformed && yieldAt <= 1 && (
        <span className="absolute -top-0.5 h-3 w-0.5 rounded bg-ink" style={{ left: `calc(${yieldAt * 100}% - 1px)` }}
          title={`Yield ${feaNumber(result.settings.yield_strength_mpa)} MPa`} />
      )}
    </div>
    <div className="flex justify-between font-mono text-[10px]"><span>0</span><span>{feaNumber(deformed ? result.max_displacement_mm : result.max_von_mises_mpa)}</span></div>
    {!deformed && (
      <span className={cx('text-[10.5px]', result.yield_ratio !== null && result.yield_ratio < 1 ? 'text-error' : result.yield_ratio !== null && result.yield_ratio < 2 ? 'text-warn' : 'text-ok')}>
        {yieldAt <= 1 ? 'Above the black mark the material yields' : `Peak is ${feaNumber(result.yield_ratio ?? 0)}× below yield (${feaNumber(result.settings.yield_strength_mpa)} MPa)`}
      </span>
    )}
    {deformed && <span>Deformation magnified {feaNumber(result.deformation_scale)}× · outline: original CAD</span>}
    <div className="flex items-center gap-1.5 text-[10px]"><span className="size-1.5 rounded-full bg-ok" />Fixed end<span className="ml-2 text-accent-ink">↗</span>{feaNumber(result.settings.force_n)} N · {result.settings.load_direction.toUpperCase()}</div>
    <span className="text-[10px]">CAD mesh · {result.elements.toLocaleString()} quadratic elements</span>
  </FloatCard>;
}

const n = (v: number) => (v >= 10 ? v.toFixed(0) : v >= 1 ? v.toFixed(1) : v.toFixed(2));

/** Colour scale for the heatmap: same blue-white-red as compare_to_mesh's images. */
function HeatLegend({ range, threshold, share }: { range: number; threshold: number; share: number }) {
  return (
    <FloatCard className="flex w-64 flex-col gap-1.5 px-3 py-2 text-[11px] text-muted">
      <div className="flex items-center justify-between">
        <span className="font-medium text-ink">Distance to scan</span>
        <span>{Math.round(share * 100)}% within ±{n(threshold)}</span>
      </div>
      <div className="h-2 rounded-full border border-line" style={{ background: 'linear-gradient(90deg, #2659d9, #ffffff, #d9261f)' }} />
      <div className="flex justify-between font-mono text-[10px]">
        <span>−{n(range)}</span>
        <span>0</span>
        <span>+{n(range)}</span>
      </div>
      <div className="flex justify-between text-[10px]">
        <span className="text-[#2659d9]">CAD has extra</span>
        <span className="text-[#c0261f]">CAD is missing</span>
      </div>
    </FloatCard>
  );
}

function verdictOf(d: Deviation): { tone: Tone; label: string } {
  if (d.verdict === 'within_threshold') return { tone: 'ok', label: 'Within tolerance' };
  if (d.trend === 'better') return { tone: 'accent', label: 'Improving' };
  if (d.trend === 'worse') return { tone: 'error', label: 'Worse' };
  return { tone: 'neutral', label: d.verdict.replaceAll('_', ' ') };
}

/** Accuracy against the scan and the revision timeline, in one card. */
/** Accuracy against the scan, for workspaces that have one. Revisions are stepped in the top bar. */
export function RevisionCard() {
  const deviations = useStore((s) => s.deviations);
  const [open, setOpen] = useState(true);
  const last = deviations.at(-1);
  if (!last) return null;

  return (
    <FloatCard className="flex w-[264px] flex-col">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex items-center gap-2 px-3 py-2 text-left">
        <span className="text-[12px] font-semibold">Accuracy</span>
        <span className="flex-1" />
        <Badge tone={verdictOf(last).tone} dot>{verdictOf(last).label}</Badge>
        <ChevronDown size={14} className={cx('shrink-0 text-faint transition-transform', !open && '-rotate-90')} aria-hidden />
      </button>
      {open && (
        <div className="flex flex-col gap-2 border-t border-line px-3 pb-2.5 pt-2">
          <Sparkline deviations={deviations} />
        </div>
      )}
    </FloatCard>
  );
}

const W = 240;
const H = 44;

/** p95 deviation per check, against the tolerance line. */
function Sparkline({ deviations }: { deviations: Deviation[] }) {
  const last = deviations.at(-1)!;
  const threshold = last.threshold;
  const top = Math.max(threshold * 1.5, ...deviations.map((d) => d.p95)) || 1;
  const y = (v: number) => 4 + (1 - v / top) * (H - 8);
  const x = (i: number) => (deviations.length === 1 ? W / 2 : 4 + (i * (W - 8)) / (deviations.length - 1));
  const points = deviations.map((d, i) => `${x(i).toFixed(1)},${y(d.p95).toFixed(1)}`).join(' ');
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline gap-1.5">
        <span className="font-mono text-[18px] font-medium tracking-[-0.02em]">{last.p95.toFixed(3)}</span>
        <span className="text-[11px] text-faint" title="95% of the scan's surface is within this distance of the CAD">
          p95 deviation from the scan
        </span>
      </div>
      <div className="relative h-11 w-full">
        <svg viewBox={`0 0 ${W} ${H}`} className="absolute inset-0 size-full" preserveAspectRatio="none" role="img"
          aria-label={`p95 deviation over ${deviations.length} checks, latest ${last.p95.toFixed(3)}, tolerance ${threshold}`}>
          <line x1="0" x2={W} y1={y(threshold)} y2={y(threshold)} className="stroke-ok/70" strokeDasharray="3 3"
            vectorEffect="non-scaling-stroke" />
          {deviations.length > 1 && (
            <polyline points={points} fill="none" className="stroke-accent" strokeWidth="1.75" strokeLinejoin="round"
              vectorEffect="non-scaling-stroke" />
          )}
        </svg>
        {deviations.map((d, i) => (
          <span key={i} aria-hidden
            className={cx('absolute -translate-x-1/2 -translate-y-1/2 rounded-full',
              i === deviations.length - 1 ? 'size-2 bg-accent ring-2 ring-surface' : 'size-1.5 bg-accent/70')}
            style={{ left: `${(x(i) / W) * 100}%`, top: `${(y(d.p95) / H) * 100}%` }} />
        ))}
      </div>
      <div className="flex font-mono text-[10px] text-faint">
        <span>{deviations.length} check{deviations.length === 1 ? '' : 's'}</span>
        <span className="flex-1" />
        <span className="text-ok">tolerance {threshold.toFixed(2)}</span>
      </div>
    </div>
  );
}

/** Two synchronized presentations of the same explicit commit history. */
export function CommitScrubber() {
  const feed = useStore((s) => s.feed);
  const selected = useStore((s) => s.selectedCommit);
  const current = useStore((s) => s.currentCommit);
  const dirty = useStore((s) => s.dirty);
  const select = useStore((s) => s.selectCommit);
  const [playing, setPlaying] = useState(false);
  const commits = commitHistory({ feed });
  const entries = [...commits.map((c) => ({ number: c.number as number | null, label: `Commit ${c.number}: ${c.message}` })),
    ...(dirty ? [{ number: null, label: 'Live changes' }] : [])];
  const chosen = selected ?? (dirty ? null : current);
  const index = Math.max(0, entries.findIndex((e) => e.number === chosen));
  useEffect(() => {
    if (!playing) return;
    if (index >= entries.length - 1) { setPlaying(false); return; }
    const timer = setTimeout(() => select(entries[index + 1].number), 700);
    return () => clearTimeout(timer);
  }, [playing, index, entries.length, select, feed]);
  if (!entries.length) return null;
  return <div className="flex min-w-0 items-center gap-2" role="group" aria-label="Commit history">
    {entries.length > 1 && <>
      <button type="button" aria-label={playing ? 'Pause commit playback' : 'Play commits'}
        onClick={() => { if (!playing && index === entries.length - 1) select(entries[0].number); setPlaying(!playing); }}
        className="flex size-6 shrink-0 items-center justify-center rounded-md hover:bg-subtle">
        {playing ? <Pause size={12} /> : <Play size={12} />}
      </button>
      <input type="range" min={0} max={entries.length - 1} value={index} aria-label="Commit"
        aria-valuetext={entries[index]?.label}
        onChange={(e) => { setPlaying(false); select(entries[Number(e.target.value)].number); }}
        className="h-1 w-28 shrink-0 cursor-pointer accent-accent-ink" />
    </>}
    <span className="max-w-56 truncate text-[11px] text-muted" title={entries[index]?.label}>{entries[index]?.label}</span>
    {selected !== null && <button type="button" onClick={() => { setPlaying(false); select(null); }}
      className="shrink-0 text-[11px] text-accent-ink">Back to live</button>}
  </div>;
}
