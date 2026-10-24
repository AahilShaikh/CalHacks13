import { useStore } from '../store';
import { AccuracyChart } from './AccuracyChart';
import { Composer } from './Composer';
import { Feed } from './Feed';
import { ModelTab } from './ModelTab';
import { PanelTabs } from './PanelTabs';
import { ReplayControls, ReplayList } from './Replay';

const STEPS = [
  { label: 'Inspect', dot: 'bg-inspect', text: 'text-inspect', line: 'Slices and views of the mesh' },
  { label: 'Build', dot: 'bg-build', text: 'text-build', line: 'Features added to the CAD model' },
  { label: 'Verify', dot: 'bg-verify', text: 'text-verify', line: 'Deviation from the scan, per revision' },
];

/** Figma: "Right panel" in Screen / 1 Empty (7:7) and Screen / 2 Running (7:58). */
export function RightPanel() {
  // The workspace is open from the start; the feed takes over once something happens in it.
  const hasSession = useStore((s) => s.feed.length > 0 || s.replay !== null);
  const replaying = useStore((s) => s.replay !== null);
  const tab = useStore((s) => s.tab);

  return (
    <aside className="flex h-full w-[400px] shrink-0 flex-col gap-3 border-l border-line bg-canvas p-3">
      {hasSession ? (
        <>
          <AccuracyChart />
          <PanelTabs />
          {tab === 'activity' ? <Feed /> : <ModelTab />}
        </>
      ) : (
        <div className="flex flex-1 flex-col gap-3">
          <div className="flex flex-col gap-2 pt-2">
            <h2 className="text-[18px] font-semibold">Scan in, CAD out.</h2>
            <p className="text-[13px] leading-[1.5] text-muted">
              Drop meshes to rebuild them as parametric build123d parts, or describe a part to design it from
              scratch. Aida builds one feature at a time, checks every revision against the scan, and can assemble
              parts together. You watch each step here.
            </p>
          </div>
          <ul className="flex flex-col gap-2">
            {STEPS.map((s) => (
              <li key={s.label} className="flex items-center gap-2 text-[12px]">
                <span className={`size-2 rounded-full ${s.dot}`} />
                <span className={`font-semibold ${s.text}`}>{s.label}</span>
                <span className="text-muted">{s.line}</span>
              </li>
            ))}
          </ul>
          <div className="flex-1" />
          <ReplayList />
        </div>
      )}
      {replaying ? <ReplayControls /> : <Composer />}
    </aside>
  );
}
