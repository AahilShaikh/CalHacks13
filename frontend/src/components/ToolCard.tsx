import { useStore, type ToolItem } from '../store';

const PHASE_TEXT = { inspect: 'text-inspect', build: 'text-build', verify: 'text-verify' } as const;
const PHASE_DOT = { inspect: 'bg-inspect', build: 'bg-build', verify: 'bg-verify' } as const;

/** Figma: ToolCard (2:93). One agent tool call; phase = inspect / build / verify. */
export function ToolCard({ item }: { item: ToolItem }) {
  const failed = item.status === 'failed';
  const running = item.status === 'running';
  const duration = item.endedAt ? `${Math.max(0.1, item.endedAt - item.startedAt).toFixed(1)}s` : null;
  const focused = useStore((s) => s.focusId === item.id);
  const setFocus = useStore((s) => s.setFocus);
  // Calls with a view (slices, rendered views) can be replayed in the 3D viewer.
  const showable = Boolean(item.view);
  const toggle = () => setFocus(focused ? null : item.id);
  return (
    <article
      {...(showable && {
        role: 'button',
        tabIndex: 0,
        'aria-pressed': focused,
        title: focused ? 'Showing in 3D. Click to follow the agent again.' : 'Show this step in the 3D view',
        onClick: toggle,
        onKeyDown: (e: React.KeyboardEvent) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            toggle();
          }
        },
      })}
      className={`flex w-full flex-col gap-2 rounded-md border bg-surface p-3 ${
        failed ? 'border-error' : focused ? 'border-accent' : 'border-line'
      } ${showable ? 'cursor-pointer hover:border-line-strong' : ''}`}
    >
      <header className="flex min-w-0 items-center gap-2">
        <span className={`size-2 shrink-0 rounded-full ${PHASE_DOT[item.phase]} ${running ? 'animate-pulse-dot' : ''}`} />
        <span className={`shrink-0 text-[10px] font-semibold uppercase tracking-[0.6px] ${PHASE_TEXT[item.phase]}`}>
          {item.phase}
        </span>
        <span className="shrink-0 font-mono text-[12px] font-medium">{item.name}</span>
        <span className="min-w-0 truncate font-mono text-[11px] text-muted">{item.argText}</span>
        <span className="flex-1" />
        {showable && (
          <span className={`shrink-0 rounded-sm px-1.5 text-[10px] font-semibold ${focused ? 'bg-accent-soft text-accent-ink' : 'bg-line text-muted'}`}>
            3D
          </span>
        )}
        <span className={`shrink-0 text-[11px] ${running ? 'text-accent-ink' : failed ? 'text-error' : 'text-faint'}`}>
          {running ? 'running…' : failed ? 'failed' : duration}
        </span>
      </header>
      {item.result && (
        <p className={`break-words font-mono text-[11px] ${failed ? 'text-error' : 'text-muted'}`}>{item.result}</p>
      )}
      {item.image && (
        <a href={item.image} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}
          className="block overflow-hidden rounded-sm bg-subtle">
          <img src={item.image} alt={`${item.name} render`} loading="lazy" className="max-h-[180px] w-full object-contain" />
        </a>
      )}
      {item.code && (
        <details onClick={(e) => e.stopPropagation()}>
          <summary className="cursor-pointer select-none text-[11px] text-faint hover:text-muted">build123d code</summary>
          <pre className="scroll-thin mt-2 max-h-56 overflow-auto rounded-sm bg-subtle p-2 font-mono text-[11px] leading-relaxed text-muted">
            {item.code}
          </pre>
        </details>
      )}
    </article>
  );
}
