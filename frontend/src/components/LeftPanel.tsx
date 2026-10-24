import { ModelTab } from './ModelTab';

/** Left column. Step 3 of the redesign turns this into the assembly explorer and References. */
export function LeftPanel() {
  return (
    <aside className="flex w-[272px] shrink-0 flex-col border-r border-line bg-surface" aria-label="Explorer">
      <div className="flex h-10 shrink-0 items-center border-b border-line px-3">
        <h2 className="text-[13px] font-semibold">Explorer</h2>
      </div>
      <div className="flex min-h-0 flex-1 flex-col p-2">
        <ModelTab />
      </div>
    </aside>
  );
}
