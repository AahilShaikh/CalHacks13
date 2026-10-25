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
  RotateCcw,
  X,
  type LucideIcon,
} from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { duration, useActivity } from '../lib/activity';
import { toolLabel, type Phase } from '../lib/events';
import { useStore, commitHistory, type FeedItem, type ToolItem } from '../store';
import { FeaPanel } from './FeaPanel';
import { IconButton, Spinner, cx } from './ui';

const PHASE_ICON: Record<Phase, LucideIcon> = { inspect: Eye, build: Hammer, verify: BadgeCheck };
const PHASE_NAME: Record<Phase, string> = { inspect: 'Inspect', build: 'Build', verify: 'Verify' };

type Note = Extract<FeedItem, { kind: 'note' }>;
type Commit = Extract<FeedItem, { kind: 'commit' }>;
type Group = { key: number; prompt: string | null; steps: (ToolItem | Note)[]; commit: Commit | null };

/** A commit closes a group of tool steps, regardless of prompt boundaries. */
function groupFeed(feed: FeedItem[]): Group[] {
  const groups: Group[] = commitHistory({ feed }).map((commit) => {
    const steps: (ToolItem | Note)[] = [];
    for (const event of commit.stepEvents) {
      if (event.type === 'tool_call') {
        const tool = feed.find((i): i is ToolItem => i.kind === 'tool' && i.name === event.name && i.startedAt === event.at);
        if (tool) steps.push(tool);
      } else if (event.type === 'user_prompt' || event.type === 'notice') {
        steps.push({ kind: 'note', id: -event.seq, text: event.text ?? '', tone: 'muted' });
      }
    }
    return { key: commit.id, prompt: `Commit ${commit.number}: ${commit.message}`, steps, commit };
  });
  const boundary = feed.findLastIndex((i) => i.kind === 'commit' || (i.kind === 'note' && i.text.startsWith('Restored commit ')));
  const steps: (ToolItem | Note)[] = [];
  for (const item of feed.slice(boundary + 1)) {
    if (item.kind === 'user') steps.push({ kind: 'note', id: item.id, text: item.text, tone: 'muted' });
    else if (item.kind === 'note' || (item.kind === 'tool' && item.name !== 'commit_changes')) steps.push(item);
  }
  if (steps.length) groups.push({ key: -1, prompt: 'Live activity', steps, commit: null });
  return groups;
}

/** Right column: every tool call the agent makes, newest at the bottom. */
export function StepsPanel() {
  const open = useStore((s) => s.stepsOpen);
  const setOpen = useStore((s) => s.setStepsOpen);
  const tab = useStore((s) => s.panelTab);
  const setTab = useStore((s) => s.setPanelTab);
  const feed = useStore((s) => s.feed);
  const dirty = useStore((s) => s.dirty);
  const groups = useMemo(() => {
    const groups = groupFeed(feed);
    const live = groups.find((g) => g.commit === null);
    if (live) live.prompt = dirty ? 'Live changes' : 'Live activity';
    else if (dirty) groups.push({ key: -1, prompt: 'Live changes', steps: [], commit: null });
    return groups;
  }, [feed, dirty]);
  const toolCount = useMemo(() => feed.filter((i) => i.kind === 'tool').length, [feed]);
  const running = feed.some((i) => i.kind === 'tool' && i.status === 'running');

  // When the window gets narrow, fold the panel so the 3D view keeps its room.
  useEffect(() => {
    const narrow = window.matchMedia('(max-width: 1099px)');
    const fold = () => narrow.matches && setOpen(false);
    narrow.addEventListener('change', fold);
    return () => narrow.removeEventListener('change', fold);
  }, [setOpen]);

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
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-line pl-2 pr-1.5">
        <div role="tablist" aria-label="Right panel" className="flex rounded-md bg-subtle p-0.5">
          {(['steps', 'fea'] as const).map((id) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
              className={cx('rounded px-2.5 py-1 text-[12px] font-medium',
                tab === id ? 'bg-surface text-ink shadow-sm' : 'text-muted hover:text-ink')}>
              {id === 'steps' ? 'Steps' : 'Structural test'}
            </button>
          ))}
        </div>
        {tab === 'steps' && toolCount > 0 && <span className="font-mono text-[11px] text-faint">{toolCount}</span>}
        <span className="flex-1" />
        <IconButton icon={PanelRightClose} label="Hide panel" size="sm" onClick={() => setOpen(false)} />
      </div>
      {tab === 'fea' ? <FeaPanel /> : groups.length ? <StepList groups={groups} feed={feed} /> : <EmptySteps />}
    </aside>
  );
}

function EmptySteps() {
  return (
    <div className="scroll-thin flex flex-1 flex-col gap-6 overflow-y-auto p-4">
      <div className="flex flex-col gap-3">
        <p className="text-[13px] leading-[1.5] text-muted">
          Every step Aida takes shows up here as it happens: what it inspects, what it builds, and how each
          commit checks out.
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
  // Groups the user opened or closed; the rest follow the default (only the latest is open).
  const [choices, setChoices] = useState<Record<number, boolean>>({});
  // Follow new steps only while the user is already at the bottom.
  useEffect(() => {
    const el = ref.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [feed]);

  const last = groups.length - 1;
  return (
    <div
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget;
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      }}
      className="scroll-thin flex min-h-0 flex-1 flex-col overflow-y-auto pb-4"
    >
      {groups.map((group, index) => {
        const running = group.steps.some((step) => step.kind === 'tool' && step.status === 'running');
        const open = group.prompt === null || (choices[group.key] ?? (index === last || running));
        return (
          <section key={group.key} className="flex flex-col border-b border-line/70 py-1.5 last:border-b-0">
            {group.prompt !== null && (
              <GroupHeader group={group} open={open} running={running}
                onToggle={() => setChoices((c) => ({ ...c, [group.key]: !open }))} />
            )}
            {open && (
              <ol className="flex flex-col">
                {group.steps.map((step) =>
                  step.kind === 'tool' ? <ToolRow key={step.id} item={step} /> : <NoteRow key={step.id} note={step} />,
                )}
              </ol>
            )}
          </section>
        );
      })}
    </div>
  );
}

/** Select the same checkpoint as the top-bar slider, or expand its tool steps. */
function GroupHeader({ group, open, running, onToggle }: {
  group: Group;
  open: boolean;
  running: boolean;
  onToggle: () => void;
}) {
  const selected = useStore((s) => s.selectedCommit);
  const current = useStore((s) => s.currentCommit);
  const dirty = useStore((s) => s.dirty);
  const select = useStore((s) => s.selectCommit);
  const active = group.commit ? (selected ?? (dirty ? null : current)) === group.commit.number : selected === null && dirty;
  return <div className="flex flex-col gap-1 px-3 pb-1">
    <div className={cx('flex items-start rounded-md', active ? 'bg-accent-soft' : 'bg-subtle')}>
      <button type="button" onClick={onToggle} aria-expanded={open} aria-label={`Expand ${group.prompt}`}
        className="p-2"><ChevronRight size={13} className={cx(open && 'rotate-90')} /></button>
      <button type="button" onClick={() => select(group.commit?.number ?? null)} aria-pressed={active}
        className="min-w-0 flex-1 px-1 py-1.5 text-left text-[12px] leading-relaxed">
        {group.prompt}{running && <Spinner size={11} className="ml-1 inline text-accent" />}
      </button>
    </div>
    {group.commit && <CommitRow commit={group.commit} />}
  </div>;
}

/** Previewing is separate from restoring the editable workspace. */
function CommitRow({ commit }: { commit: Commit }) {
  const current = useStore((s) => s.currentCommit === commit.number);
  const shown = useStore((s) => s.selectedCommit === commit.number);
  const dirty = useStore((s) => s.dirty);
  const selectCommit = useStore((s) => s.selectCommit);
  const restore = useStore((s) => s.restoreCommit);
  const { working } = useActivity();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!confirming) return;
    const timer = setTimeout(() => setConfirming(false), 4000);
    return () => clearTimeout(timer);
  }, [confirming]);

  return (
    <div className="flex items-center gap-1.5 pl-[22px] text-[11px]">
      <span className={cx('rounded-sm px-1.5 py-px font-mono font-medium',
        current ? 'bg-accent-soft text-accent-ink' : 'bg-subtle text-muted')}>
        Commit {commit.number}
      </span>
      {current && <span className="text-faint">{dirty ? 'base' : 'current'}</span>}
      <span className="flex-1" />
      {commit.meshRevision !== null && (
        <button type="button" aria-pressed={shown}
          onClick={() => selectCommit(shown ? null : commit.number)}
          className={cx('rounded-sm px-1.5 py-0.5 hover:bg-subtle', shown ? 'font-medium text-accent-ink' : 'text-muted hover:text-ink')}>
          {shown ? 'Viewing' : 'View'}
        </button>
      )}
      {(!current || dirty) && (
        <button
          type="button"
          disabled={working || busy}
          title={working ? 'Wait for Aida to finish, or stop it' : 'Put the design back to this commit (later commits are kept)'}
          onClick={async () => {
            if (!confirming) {
              setConfirming(true);
              return;
            }
            setConfirming(false);
            setBusy(true);
            await restore(commit.number);
            setBusy(false);
          }}
          className={cx('flex items-center gap-1 rounded-sm px-1.5 py-0.5 disabled:cursor-not-allowed disabled:opacity-40',
            confirming ? 'bg-accent-ink font-medium text-white' : 'text-muted hover:bg-subtle hover:text-ink')}
        >
          {busy ? <Spinner size={11} /> : <RotateCcw size={11} strokeWidth={2} aria-hidden />}
          {confirming ? `Restore commit ${commit.number}?` : 'Restore'}
        </button>
      )}
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
