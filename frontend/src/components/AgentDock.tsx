import { AlertCircle, ArrowUp, ImageIcon, MessagesSquare, Square, X } from 'lucide-react';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useActivity } from '../lib/activity';
import type { Question } from '../lib/events';
import { isMeshFile, useStore, type FeedItem } from '../store';
import { Markdown } from './Markdown';
import { ReplayControls } from './Replay';
import { FloatCard, IconButton, Spinner, cx } from './ui';

/** Bottom centre of the viewer: hints, errors, the agent's reply, and the prompt pill. */
export function AgentDock() {
  const replay = useStore((s) => s.replay);
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-5 z-20 flex justify-center px-4">
      <div className="pointer-events-auto flex w-full max-w-[640px] flex-col gap-2">
        <Hints />
        <ErrorCard />
        <ReplyCard />
        {!replay && <QuestionCard />}
        {replay ? <ReplayControls /> : <PromptPill />}
      </div>
    </div>
  );
}

function Hints() {
  const meshFiles = useStore((s) => s.meshFiles);
  const stage = useStore((s) => s.stage);
  const status = useStore((s) => s.status);
  const asked = useStore((s) => s.feed.some((i) => i.kind === 'user'));
  const hasCad = useStore((s) => s.revisions.some((r) => r.generated));
  const hasParts = useStore((s) => (s.workspace?.parts.length ?? 0) > 0);
  const working = useActivity().working;
  const engineStarting = status === 'starting' || stage === 'cad_starting' || stage === 'preparing';
  let text: string | null = null;
  if (meshFiles.length) text = `Adding ${meshFiles.map((f) => f.name).join(', ')}…`;
  else if (asked && !hasCad && engineStarting) text = 'Starting the CAD engine…';
  // A part exists in the explorer from its first parameters; it has a shape only after its first feature builds.
  else if (!hasCad && hasParts && working) text = 'No shape yet: Aida is building the first feature…';
  if (!text) return null;
  return (
    <div className="flex justify-center">
      <FloatCard className="flex items-center gap-2 rounded-full px-3 py-1.5 text-[12px] text-muted">
        <Spinner size={12} className="text-accent" />
        {text}
      </FloatCard>
    </div>
  );
}

function ErrorCard() {
  const error = useStore((s) => s.error);
  const clearError = useStore((s) => s.clearError);
  if (!error) return null;
  return (
    <FloatCard className="flex items-start gap-2 border-error/30 py-2 pl-3 pr-1.5 text-[13px] text-error">
      <AlertCircle size={16} strokeWidth={2} className="mt-0.5 shrink-0" aria-hidden />
      <p role="alert" className="min-w-0 flex-1 py-0.5 leading-[1.45]">{error}</p>
      <IconButton icon={X} size="sm" label="Dismiss" onClick={clearError} />
    </FloatCard>
  );
}

type Message = Extract<FeedItem, { kind: 'agent' | 'user' | 'question' }>;

/** One answer as the agent will read it: "PLA (E 3500 MPa, yield 50 MPa)", "50 N". */
function answerText(q: Question, value: string, other: boolean) {
  if (q.kind === 'number') return value && q.unit ? `${value} ${q.unit}` : value;
  if (q.kind === 'choice' && !other) {
    const option = q.options.find((o) => o.label === value);
    return option?.detail ? `${option.label} (${option.detail})` : value;
  }
  return value.trim();
}

/** Aida's questions with clickable answers; sending them is the user's next message. */
function QuestionCard() {
  const question = useStore((s) => s.question);
  const submit = useStore((s) => s.submit);
  const [values, setValues] = useState<Record<string, string>>({});
  const [others, setOthers] = useState<Record<string, boolean>>({});
  const [sending, setSending] = useState(false);
  // A new set of questions starts blank.
  useEffect(() => {
    setValues({});
    setOthers({});
    setSending(false);
  }, [question?.id]);
  if (!question) return null;
  const answered = (q: Question) => (values[q.id] ?? '').trim().length > 0;
  const complete = question.questions.every(answered);
  const send = async () => {
    if (!complete || sending) return;
    setSending(true);
    const lines = question.questions.map((q) => `${q.id}: ${answerText(q, values[q.id], Boolean(others[q.id]))}`);
    await submit(lines.join('\n'));
    setSending(false);
  };
  const set = (id: string, value: string, other = false) => {
    setValues((v) => ({ ...v, [id]: value }));
    setOthers((o) => ({ ...o, [id]: other }));
  };

  return (
    <FloatCard className="flex flex-col">
      <div className="flex items-center gap-2 pl-3.5 pr-3.5 pt-2.5">
        <AidaMark />
        <span className="text-[12px] font-semibold">Aida needs {question.questions.length === 1 ? 'one detail' : 'a few details'}</span>
        <span className="flex-1" />
        <span className="text-[11px] text-faint">or type your own answer below</span>
      </div>
      <form
        className="flex flex-col gap-3 px-3.5 pb-3 pt-2"
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        {question.questions.map((q) => (
          <fieldset key={q.id} className="flex flex-col gap-1.5">
            <legend className="mb-1.5 text-[13px] leading-[1.45] text-ink">{q.prompt}</legend>
            {q.kind === 'choice' ? (
              <div role="radiogroup" aria-label={q.prompt} className="flex flex-wrap gap-1.5">
                {q.options.map((o) => {
                  const picked = values[q.id] === o.label && !others[q.id];
                  return (
                    <button key={o.label} type="button" role="radio" aria-checked={picked}
                      onClick={() => set(q.id, o.label)}
                      className={cx('flex flex-col items-start rounded-lg border px-2.5 py-1.5 text-left text-[12px] leading-tight',
                        picked ? 'border-accent bg-accent-soft text-accent-ink' : 'border-line-strong text-ink hover:border-accent/60 hover:bg-subtle')}>
                      <span className="font-medium">{o.label}</span>
                      {o.detail && <span className="mt-0.5 text-[10.5px] text-muted">{o.detail}</span>}
                    </button>
                  );
                })}
                {q.allow_other && (
                  others[q.id] ? (
                    <input autoFocus value={values[q.id] ?? ''} onChange={(e) => set(q.id, e.target.value, true)}
                      aria-label={`${q.prompt} (your answer)`} placeholder="Your answer"
                      className="min-w-40 flex-1 rounded-lg border border-accent bg-surface px-2.5 py-1.5 text-[12px] text-ink outline-none" />
                  ) : (
                    <button type="button" role="radio" aria-checked={false} onClick={() => set(q.id, '', true)}
                      className="rounded-lg border border-dashed border-line-strong px-2.5 py-1.5 text-[12px] text-muted hover:border-accent/60 hover:text-ink">
                      Other…
                    </button>
                  )
                )}
              </div>
            ) : (
              <label className="flex items-center gap-2">
                <input type={q.kind === 'number' ? 'number' : 'text'} step="any" value={values[q.id] ?? ''}
                  onChange={(e) => set(q.id, e.target.value)} aria-label={q.prompt}
                  className={cx('rounded-lg border border-line-strong bg-surface px-2.5 py-1.5 text-[13px] text-ink outline-none focus:border-accent',
                    q.kind === 'number' ? 'w-32 font-mono' : 'flex-1')} />
                {q.unit && <span className="text-[12px] text-muted">{q.unit}</span>}
              </label>
            )}
          </fieldset>
        ))}
        <button type="submit" disabled={!complete || sending}
          className="self-end rounded-full bg-accent-ink px-3.5 py-1.5 text-[12px] font-semibold text-white hover:bg-accent-ink-hover disabled:cursor-not-allowed disabled:bg-line-strong">
          {sending ? 'Sending…' : 'Send answers'}
        </button>
      </form>
    </FloatCard>
  );
}

/** The agent's latest message since your last prompt. Older messages are one click away. */
function ReplyCard() {
  const feed = useStore((s) => s.feed);
  const dismissed = useStore((s) => s.replyDismissedId);
  const dismissReply = useStore((s) => s.dismissReply);
  const [history, setHistory] = useState(false);
  const messages = useMemo(
    () => feed.filter((i): i is Message => i.kind === 'agent' || i.kind === 'user' || i.kind === 'question'),
    [feed],
  );
  const latest = messages.at(-1);
  const reply = latest?.kind === 'agent' && latest.id !== dismissed ? latest : null;

  if (history) return <History messages={messages} onClose={() => setHistory(false)} />;
  if (!reply) {
    // Nothing new to read, but there is a conversation: offer it.
    return messages.some((m) => m.kind === 'agent') ? (
      <div className="flex justify-center">
        <button
          type="button"
          onClick={() => setHistory(true)}
          className="flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[12px] text-muted hover:bg-surface/80 hover:text-ink"
        >
          <MessagesSquare size={13} strokeWidth={1.75} aria-hidden />
          Conversation
        </button>
      </div>
    ) : null;
  }
  return (
    <FloatCard className="flex flex-col">
      <div className="flex items-center gap-2 pl-3.5 pr-1.5 pt-1.5">
        <AidaMark />
        <span className="text-[12px] font-semibold">Aida</span>
        <span className="flex-1" />
        <IconButton icon={MessagesSquare} size="sm" label="Show the conversation" onClick={() => setHistory(true)} />
        <IconButton icon={X} size="sm" label="Dismiss" onClick={() => dismissReply(reply.id)} />
      </div>
      <ReplyBody key={reply.id} text={reply.text} />
    </FloatCard>
  );
}

/** About five lines of the reply, so the model stays in view; "Show more" opens the rest. */
function ReplyBody({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !expanded) setOverflows(el.scrollHeight > el.clientHeight + 2);
  }, [text, expanded]);
  return (
    <div className="flex flex-col px-3.5 pb-2.5 pt-1">
      <div
        ref={ref}
        aria-live="polite"
        className={cx('relative text-[13px] leading-[1.55] text-ink',
          expanded ? 'scroll-thin max-h-[40vh] overflow-y-auto' : 'max-h-[7.9em] overflow-hidden')}
      >
        <Markdown text={text} />
        {!expanded && overflows && (
          <span aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-6 bg-gradient-to-t from-surface to-transparent" />
        )}
      </div>
      {(overflows || expanded) && (
        <button type="button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}
          className="self-start pt-1 text-[12px] font-medium text-accent-ink hover:underline">
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  );
}

function History({ messages, onClose }: { messages: Message[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [messages.length]);
  useEffect(() => {
    const close = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [onClose]);
  return (
    <FloatCard className="flex flex-col">
      <div className="flex h-9 items-center gap-2 border-b border-line pl-3.5 pr-1.5">
        <span className="text-[12px] font-semibold">Conversation</span>
        <span className="flex-1" />
        <IconButton icon={X} size="sm" label="Close the conversation" onClick={onClose} />
      </div>
      <div ref={ref} className="scroll-thin flex max-h-[45vh] flex-col gap-3 overflow-y-auto p-3.5 text-[13px] leading-[1.55]">
        {messages.map((m) =>
          m.kind === 'question' ? (
            <div key={m.id} className="flex gap-2">
              <AidaMark className="mt-1" />
              <ul className="min-w-0 flex-1 list-none rounded-lg border border-line px-3 py-2">
                {m.questions.map((q) => (
                  <li key={q.id} className="text-ink">{q.prompt}
                    {q.options.length > 0 && <span className="text-muted"> · {q.options.map((o) => o.label).join(' / ')}</span>}
                  </li>
                ))}
                {!m.answer && <li className="mt-1 text-[12px] text-faint">Waiting for your answer</li>}
              </ul>
            </div>
          ) : m.kind === 'user' ? (
            <div key={m.id} className="self-end rounded-lg bg-subtle px-3 py-2 whitespace-pre-wrap">{m.text}</div>
          ) : (
            <div key={m.id} className="flex gap-2">
              <AidaMark className="mt-1" />
              <div className="min-w-0 flex-1"><Markdown text={m.text} /></div>
            </div>
          ),
        )}
      </div>
    </FloatCard>
  );
}

function AidaMark({ className }: { className?: string }) {
  return <span className={cx('size-2 shrink-0 rounded-full bg-accent', className)} aria-hidden />;
}

/** The prompt: one line that grows, with Stop while the agent works. Files dropped here become references. */
function PromptPill() {
  const [text, setText] = useState('');
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const status = useStore((s) => s.status);
  const images = useStore((s) => s.images);
  const asked = useStore((s) => s.feed.some((i) => i.kind === 'user'));
  const { addMeshes, addImages, removeImage, submit, stop } = useStore.getState();
  const activity = useActivity();

  // A closed workspace is only paused: sending (or adding files) reopens it.
  const paused = status === 'done' || status === 'failed';
  const canSend = status !== 'idle' && text.trim().length > 0;
  const placeholder = paused
    ? 'Paused. Send a message to pick up where you left off…'
    : activity.working
      ? `Aida is working: ${activity.label.toLowerCase()}. Type to queue a follow-up…`
      : asked
        ? 'Ask for a change, or what to check next…'
        : 'Describe a part to design, or what to do with your scans…';

  // Grow with the text, up to about six lines.
  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 148)}px`;
  }, [text]);

  // "/" focuses the prompt from anywhere outside a text field.
  useEffect(() => {
    const focus = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (e.key !== '/' || target.closest('input, textarea, select, [contenteditable]')) return;
      e.preventDefault();
      input.current?.focus();
    };
    window.addEventListener('keydown', focus);
    return () => window.removeEventListener('keydown', focus);
  }, []);

  const send = async () => {
    if (!canSend) return;
    const prompt = text;
    setText('');
    await submit(prompt);
  };

  const takeFiles = (files: File[]) => {
    const meshes = files.filter(isMeshFile);
    if (meshes.length) void addMeshes(meshes);
    const photos = files.filter((f) => f.type.startsWith('image/'));
    if (photos.length) addImages(photos);
  };

  return (
    <div
      data-working={activity.working || undefined}
      className={cx('agent-frame rounded-[24px] p-px shadow-float', dragging && 'agent-frame-drop')}
      onDragOver={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setDragging(false);
        takeFiles([...e.dataTransfer.files]);
      }}
    >
      <div className="flex flex-col gap-1.5 rounded-[23px] bg-surface py-1.5 pl-4 pr-1.5">
        {images.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {images.map((image, index) => (
              <span key={`${image.name}-${index}`}
                className="flex items-center gap-1 rounded-full bg-subtle py-0.5 pl-2 pr-0.5 text-[11px] text-muted">
                <ImageIcon size={12} strokeWidth={1.75} aria-hidden />
                <span className="max-w-40 truncate">{image.name}</span>
                <IconButton icon={X} size="sm" label={`Remove ${image.name}`} className="size-5"
                  onClick={() => removeImage(index)} />
              </span>
            ))}
          </div>
        )}
        <div className="flex items-end gap-1.5">
          <textarea
            ref={input}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            onPaste={(e) => {
              const files = [...e.clipboardData.files];
              if (files.length) {
                e.preventDefault();
                takeFiles(files);
              }
            }}
            rows={1}
            aria-label="Message Aida"
            placeholder={placeholder}
            className="min-h-8 flex-1 resize-none bg-transparent py-[7px] text-[14px] leading-[18px] text-ink outline-none placeholder:text-faint disabled:opacity-60"
          />
          {activity.working && (
            <button
              type="button"
              onClick={() => void stop()}
              aria-label="Stop Aida"
              title="Stop"
              className="flex size-8 shrink-0 items-center justify-center rounded-full border border-line-strong text-ink hover:bg-subtle"
            >
              <Square size={11} strokeWidth={0} fill="currentColor" aria-hidden />
            </button>
          )}
          <button
            type="button"
            onClick={() => void send()}
            disabled={!canSend}
            aria-label={activity.working ? 'Queue message' : 'Send'}
            title={activity.working ? 'Queue (Enter)' : 'Send (Enter)'}
            className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-ink text-white hover:bg-accent-ink-hover disabled:cursor-not-allowed disabled:bg-line-strong disabled:text-white"
          >
            <ArrowUp size={16} strokeWidth={2.25} aria-hidden />
          </button>
        </div>
      </div>
    </div>
  );
}
