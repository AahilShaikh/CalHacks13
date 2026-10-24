import type { ToolItem } from '../store';

const PHASE_TEXT = { inspect: 'text-inspect', build: 'text-build', verify: 'text-verify' } as const;
const PHASE_DOT = { inspect: 'bg-inspect', build: 'bg-build', verify: 'bg-verify' } as const;

/** Figma: ToolCard (2:93). One agent tool call; phase = inspect / build / verify. */
export function ToolCard({ item }: { item: ToolItem }) {
  const failed = item.status === 'failed';
  const running = item.status === 'running';
  const duration = item.endedAt ? `${Math.max(0.1, item.endedAt - item.startedAt).toFixed(1)}s` : null;
  return (
    <article className={`flex w-full flex-col gap-2 rounded-md border bg-raised p-3 ${failed ? 'border-error' : 'border-line'}`}>
      <header className="flex min-w-0 items-center gap-2">
        <span className={`size-2 shrink-0 rounded-full ${PHASE_DOT[item.phase]} ${running ? 'animate-pulse-dot' : ''}`} />
        <span className={`shrink-0 text-[10px] font-semibold uppercase tracking-[0.6px] ${PHASE_TEXT[item.phase]}`}>
          {item.phase}
        </span>
        <span className="shrink-0 font-mono text-[12px] font-medium">{item.name}</span>
        <span className="min-w-0 truncate font-mono text-[11px] text-muted">{item.argText}</span>
        <span className="flex-1" />
        <span className={`shrink-0 text-[11px] ${running ? 'text-cad' : failed ? 'text-error' : 'text-faint'}`}>
          {running ? 'running…' : failed ? 'failed' : duration}
        </span>
      </header>
      {item.result && (
        <p className={`break-words font-mono text-[11px] ${failed ? 'text-error' : 'text-muted'}`}>{item.result}</p>
      )}
      {item.image && (
        <a href={item.image} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-sm bg-sunken">
          <img src={item.image} alt={`${item.name} render`} loading="lazy" className="max-h-[180px] w-full object-contain" />
        </a>
      )}
      {item.code && (
        <details>
          <summary className="cursor-pointer select-none text-[11px] text-faint hover:text-muted">build123d code</summary>
          <pre className="scroll-thin mt-2 max-h-56 overflow-auto rounded-sm bg-sunken p-2 font-mono text-[11px] leading-relaxed text-muted">
            {item.code}
          </pre>
        </details>
      )}
    </article>
  );
}
