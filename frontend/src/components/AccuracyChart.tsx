import { useStore } from '../store';
import { VerdictBadge } from './VerdictBadge';

const W = 344;
const H = 72;

/** Figma: AccuracyChart (4:63). p95 deviation per model check, against the threshold. */
export function AccuracyChart() {
  const deviations = useStore((s) => s.deviations);
  const last = deviations.at(-1);

  if (!last) {
    return (
      <section className="shrink-0 rounded-md border border-line bg-raised px-4 py-3">
        <p className="text-[11px] text-muted">Deviation from mesh · p95</p>
        <p className="mt-1 text-[13px] leading-[1.45] text-faint">
          No checks yet. Each model revision is compared against the scan and plotted here.
        </p>
      </section>
    );
  }

  const threshold = last.threshold;
  const top = Math.max(threshold * 1.5, ...deviations.map((d) => d.p95)) || 1;
  const y = (v: number) => 4 + (1 - v / top) * (H - 8);
  const x = (i: number) => (deviations.length === 1 ? W / 2 : 4 + (i * (W - 8)) / (deviations.length - 1));
  const points = deviations.map((d, i) => `${x(i).toFixed(1)},${y(d.p95).toFixed(1)}`).join(' ');

  return (
    <section className="flex shrink-0 flex-col gap-2 rounded-md border border-line bg-raised px-4 py-3">
      <div className="flex items-center gap-2">
        <div className="flex flex-col gap-0.5">
          <span className="text-[11px] text-muted">Deviation from mesh · p95</span>
          <span className="font-mono text-[20px] font-medium">{last.p95.toFixed(3)}</span>
        </div>
        <div className="flex-1" />
        <VerdictBadge deviation={last} />
      </div>
      <div className="relative h-[72px] w-full">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="absolute inset-0 size-full"
          preserveAspectRatio="none"
          role="img"
          aria-label={`p95 deviation over ${deviations.length} checks, latest ${last.p95.toFixed(3)}`}
        >
          <line x1="0" x2={W} y1={y(threshold)} y2={y(threshold)} className="stroke-ok/60" strokeDasharray="4 4"
            vectorEffect="non-scaling-stroke" />
          {deviations.length > 1 && (
            <polyline points={points} fill="none" className="stroke-cad" strokeWidth="2" strokeLinejoin="round"
              vectorEffect="non-scaling-stroke" />
          )}
        </svg>
        {/* Points are HTML so they stay round while the SVG stretches to the panel width. */}
        {deviations.map((d, i) => (
          <span
            key={i}
            aria-hidden
            className={`absolute -translate-x-1/2 -translate-y-1/2 rounded-full bg-cad ${i === deviations.length - 1 ? 'size-2' : 'size-1.5'}`}
            style={{ left: `${(x(i) / W) * 100}%`, top: `${(y(d.p95) / H) * 100}%` }}
          />
        ))}
      </div>
      <div className="flex font-mono text-[10px]">
        <span className="text-faint">check 1</span>
        <span className="flex-1" />
        <span className="text-ok">threshold {threshold.toFixed(2)}</span>
        <span className="flex-1" />
        <span className="text-faint">check {deviations.length}</span>
      </div>
    </section>
  );
}
