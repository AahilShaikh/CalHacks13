import { Explorer } from './Explorer';
import { References } from './References';

/** Left column: the assembly explorer, with the workspace's references below it. */
export function LeftPanel() {
  return (
    <aside className="flex w-[272px] shrink-0 flex-col border-r border-line bg-surface" aria-label="Explorer">
      <div className="flex h-10 shrink-0 items-center px-3">
        <h2 className="text-[13px] font-semibold">Explorer</h2>
      </div>
      <div className="scroll-thin flex min-h-0 flex-1 flex-col overflow-y-auto px-1.5">
        <Explorer />
      </div>
      <References />
    </aside>
  );
}
