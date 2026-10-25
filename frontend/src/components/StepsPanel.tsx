import {
  AlertCircle,
  BadgeCheck,
  Box,
  Check,
  ChevronRight,
  Eye,
  Hammer,
  PanelRightClose,
  PanelRightOpen,
  X,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { duration } from '../lib/activity';
import { toolLabel, type Phase } from '../lib/events';
import { useStore, type FeedItem, type ToolItem } from '../store';
import { IconButton, Spinner, cx } from './ui';

const PHASE_ICON: Record<Phase, LucideIcon> = { inspect: Eye, build: Hammer, verify: BadgeCheck };
const PHASE_NAME: Record<Phase, string> = { inspect: 'Inspect', build: 'Build', verify: 'Verify' };

type Note = Extract<FeedItem, { kind: 'note' }>;
type Group = { key: number; prompt: string | null; steps: (ToolItem | Note)[] };

/** Split the feed at each prompt the user sent; agent text goes to the reply card instead. */
function groupFeed(feed: FeedItem[]): Group[] {
  const groups: Group[] = [];
  for (const item of feed) {
    if (item.kind === 'user') {
      groups.push({ key: item.id, prompt: item.text, steps: [] });
      continue;
    }
    if (item.kind === 'agent') continue;
    if (!groups.length) groups.push({ key: 0, prompt: null, steps: [] });
    groups[groups.length - 1].steps.push(item);
  }
  return groups;
}

/** Right column: every tool call the agent makes, newest at the bottom. */
export function StepsPanel() {
  const open = useStore((s) => s.stepsOpen);
  const setOpen = useStore((s) => s.setStepsOpen);
  const feed = useStore((s) => s.feed);
  const groups = useMemo(() => groupFeed(feed), [feed]);
  const toolCount = useMemo(() => feed.filter((i) => i.kind === 'tool').length, [feed]);
  const running = feed.some((i) => i.kind === 'tool' && i.status === 'running');

  if (!open) {
    return (
      <aside className="flex w-11 shrink-0 flex-col items-center gap-2 border-l border-line bg-surface py-2">
        <IconButton icon={PanelRightOpen} label="Show steps" onClick={() => setOpen(true)} />
        {running && <Spinner className="text-accent" />}
        {toolCount > 0 && <span className="font-mono text-[11px] text-faint">{toolCount}</span>}
      </aside>
    );
  }

  return (
    <aside className="flex w-[320px] shrink-0 flex-col border-l border-line bg-surface" aria-label="Agent steps">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-line pl-3 pr-1.5">
        <h2 className="text-[13px] font-semibold">Steps</h2>
        {toolCount > 0 && <span className="font-mono text-[11px] text-faint">{toolCount}</span>}
        <span className="flex-1" />
        <IconButton icon={PanelRightClose} label="Hide steps" size="sm" onClick={() => setOpen(false)} />
      </div>
      {groups.length ? <StepList groups={groups} feed={feed} /> : <EmptySteps />}
    </aside>
  );
}

function EmptySteps() {
  return (
    <div className="scroll-thin flex flex-1 flex-col gap-6 overflow-y-auto p-4">
      <div className="flex flex-col gap-3">
        <p className="text-[13px] leading-[1.5] text-muted">
          Every step Aida takes shows up here as it happens: what it inspects, what it builds, and how each
          revision checks out.
        </p>
        <ul className="flex flex-col gap-2">
          {(['inspect', 'build', 'verify'] as const).map((phase) => {
            const Icon = PHASE_ICON[phase];
            return (
              <li key={phase} className="flex items-center gap-2 text-[12px] text-muted">
                <Icon size={14} strokeWidth={1.75} className="text-faint" aria-hidden />
                <span className="font-medium text-ink">{PHASE_NAME[phase]}</span>
                <span>
                  {phase === 'inspect' ? 'slices and views of the scan' : phase === 'build' ? 'features added to the model' : 'deviation from the scan'}
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function StepList({ groups, feed }: { groups: Group[]; feed: FeedItem[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  // Follow new steps only while the user is already at the bottom.
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [feed]);

  return (
    <div
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
      className="scroll-thin flex min-h-0 flex-1 flex-col overflow-y-auto pb-4"
    >
      {groups.map((group) => (
        <section key={group.key} className="flex flex-col border-b border-line/70 py-2 last:border-b-0">
          {group.prompt !== null && (
            <p className="mx-3 mb-1 line-clamp-3 rounded-md bg-subtle px-2.5 py-1.5 text-[12px] leading-[1.45] text-ink"
              title={group.prompt}>
              {group.prompt}
            </p>
          )}
          <ol className="flex flex-col">
            {group.steps.map((step) =>
              step.kind === 'tool' ? <ToolRow key={step.id} item={step} /> : <NoteRow key={step.id} note={step} />,
            )}
          </ol>
        </section>
      ))}
    </div>
  );
}

function NoteRow({ note }: { note: Note }) {
  const Icon = note.tone === 'error' ? AlertCircle : note.tone === 'ok' ? Check : null;
  return (
    <li className={cx('flex items-start gap-2 px-3 py-1.5 text-[12px] leading-[1.45]',
      note.tone === 'error' ? 'text-error' : 'text-faint')}>
      {Icon ? <Icon size={14} strokeWidth={2} className="mt-px shrink-0" aria-hidden />
        : <span className="mx-[5px] mt-[7px] size-1 shrink-0 rounded-full bg-line-strong" aria-hidden />}
      <span className="min-w-0 break-words">{note.text}</span>
    </li>
  );
}

/** One tool call. Click to open its result; calls with a 3D view can be shown in the viewer. */
function ToolRow({ item }: { item: ToolItem }) {
  const [open, setOpen] = useState(false);
  const focused = useStore((s) => s.focusId === item.id);
  const setFocus = useStore((s) => s.setFocus);
  const running = item.status === 'running';
  const failed = item.status === 'failed';
  const Icon = PHASE_ICON[item.phase];
  const time = item.endedAt ? duration(Math.max(0.1, item.endedAt - item.startedAt)) : null;
  const part = typeof item.args.part === 'string' ? item.args.part : null;
  const hasDetail = Boolean(item.result || item.image || item.code);

  return (
    <li className={cx('relative', focused && 'bg-accent-soft/60')}>
      {focused && <span className="absolute inset-y-0 left-0 w-0.5 bg-accent" aria-hidden />}
      <div className="group flex items-center">
        <button
          type="button"
          aria-expanded={hasDetail ? open : undefined}
          onClick={() => hasDetail && setOpen(!open)}
          className={cx('flex min-w-0 flex-1 items-center gap-2 py-1.5 pl-3 pr-1 text-left',
            hasDetail ? 'hover:bg-subtle' : 'cursor-default')}
        >
          <span className={cx('flex size-5 shrink-0 items-center justify-center rounded-sm',
            running ? 'text-accent' : failed ? 'text-error' : 'text-faint')}
            title={PHASE_NAME[item.phase]}>
            {running ? <Spinner size={13} /> : failed ? <X size={14} strokeWidth={2} aria-hidden />
              : <Icon size={14} strokeWidth={1.75} aria-hidden />}
          </span>
          <span className={cx('shrink-0 text-[12.5px]', running ? 'font-medium text-ink' : 'text-ink')}>
            {toolLabel(item.name)}
            <span className="sr-only">{running ? ', running' : failed ? ', failed' : `, ${PHASE_NAME[item.phase].toLowerCase()} step`}</span>
          </span>
          <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-faint">
            {[part, item.argText].filter(Boolean).join(' · ')}
          </span>
          <span className={cx('shrink-0 font-mono text-[11px]', failed ? 'text-error' : 'text-faint')}>
            {running ? '' : failed ? 'failed' : time}
          </span>
          {hasDetail && (
            <ChevronRight size={13} aria-hidden
              className={cx('shrink-0 text-faint transition-transform', open && 'rotate-90')} />
          )}
        </button>
        {item.view && (
          <IconButton
            icon={Box}
            size="sm"
            label={focused ? 'Back to following the agent' : 'Show this step in 3D'}
            active={focused}
            aria-pressed={focused}
            onClick={() => setFocus(focused ? null : item.id)}
            className={cx('mr-1.5', !focused && 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100')}
          />
        )}
      </div>
      {open && (
        <div className="flex flex-col gap-2 pb-2.5 pl-10 pr-3">
          {item.result && (
            <p className={cx('break-words font-mono text-[11px] leading-[1.5]', failed ? 'text-error' : 'text-muted')}>
              {item.result}
            </p>
          )}
          {item.image && (
            <a href={item.image} target="_blank" rel="noreferrer" className="block overflow-hidden rounded-md border border-line bg-subtle">
              <img src={item.image} alt={`${toolLabel(item.name)} render`} loading="lazy" className="max-h-[180px] w-full object-contain" />
            </a>
          )}
          {item.code && (
            <pre className="scroll-thin max-h-56 overflow-auto rounded-md border border-line bg-subtle p-2 font-mono text-[11px] leading-relaxed text-muted">
              {item.code}
            </pre>
          )}
        </div>
      )}
    </li>
  );
}
