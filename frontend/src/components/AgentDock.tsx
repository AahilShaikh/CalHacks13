import { AlertCircle, ArrowUp, ImageIcon, MessagesSquare, Square, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useActivity } from '../lib/activity';
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
  const engineStarting = status === 'starting' || stage === 'cad_starting' || stage === 'preparing';
  let text: string | null = null;
  if (meshFiles.length) text = `Adding ${meshFiles.map((f) => f.name).join(', ')}…`;
  else if (asked && !hasCad && engineStarting) text = 'Starting the CAD engine…';
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

type Message = Extract<FeedItem, { kind: 'agent' | 'user' }>;

/** The agent's latest message since your last prompt. Older messages are one click away. */
function ReplyCard() {
  const feed = useStore((s) => s.feed);
  const dismissed = useStore((s) => s.replyDismissedId);
  const dismissReply = useStore((s) => s.dismissReply);
  const [history, setHistory] = useState(false);
  const messages = useMemo(() => feed.filter((i): i is Message => i.kind === 'agent' || i.kind === 'user'), [feed]);
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
      <div aria-live="polite" className="scroll-thin max-h-[32vh] overflow-y-auto px-3.5 pb-3 pt-1 text-[13px] leading-[1.55] text-ink">
        <Markdown text={reply.text} />
      </div>
    </FloatCard>
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
          m.kind === 'user' ? (
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
