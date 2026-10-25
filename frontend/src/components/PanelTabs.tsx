import { useStore, type Tab } from '../store';

const TABS: { id: Tab; label: string }[] = [
  { id: 'activity', label: 'Activity' },
  { id: 'model', label: 'Model' },
  { id: 'fea', label: 'FEA' },
];

/** Figma: PanelTabs (4:105). */
export function PanelTabs() {
  const tab = useStore((s) => s.tab);
  const setTab = useStore((s) => s.setTab);
  return (
    <div role="tablist" className="flex h-9 shrink-0 items-start gap-4 border-b border-line pl-1">
      {TABS.map((t) => {
        const active = t.id === tab;
        return (
          <button key={t.id} role="tab" aria-selected={active} onClick={() => setTab(t.id)}
            className="flex cursor-pointer flex-col items-center gap-2 pt-2">
            <span className={`text-[13px] ${active ? 'font-semibold text-ink' : 'font-medium text-muted hover:text-ink'}`}>
              {t.label}
            </span>
            <span className={`h-0.5 w-full ${active ? 'bg-cad' : ''}`} />
          </button>
        );
      })}
    </div>
  );
}
