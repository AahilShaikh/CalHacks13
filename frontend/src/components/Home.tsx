import { ArrowUp, Box, ImageIcon, MoreHorizontal, Pencil, Plus, Trash2, Upload, X } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import * as api from '../lib/api';
import { linkProps, navigate, workspacePath } from '../lib/router';
import { MESH_EXTENSIONS, isMeshFile } from '../store';
import { ReplayList } from './Replay';
import { Logo } from './TopBar';
import { Button, IconButton, RenameField, Spinner, cx } from './ui';

/** Start something new, or pick up a saved workspace. */
export function Home() {
  const [workspaces, setWorkspaces] = useState<api.WorkspaceSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = () =>
    api.listWorkspaces()
      .then((list) => {
        setWorkspaces(list);
        setError(null);
      })
      .catch((err) => setError(`Could not load your workspaces: ${(err as Error).message}`));
  useEffect(() => {
    void load();
    document.title = 'Aida';
  }, []);

  const create = async (start: Parameters<typeof api.createSession>[0] = {}) => {
    setCreating(true);
    setError(null);
    try {
      const { session_id } = await api.createSession({ units: 'mm', ...start });
      navigate(workspacePath(session_id));
    } catch (err) {
      setError(`Could not create a workspace: ${(err as Error).message}`);
      setCreating(false);
    }
  };

  return (
    <div className="flex h-full flex-col bg-canvas">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-surface px-4">
        <Logo />
        <span className="text-[14px] font-semibold tracking-[-0.01em]">Aida</span>
        <span className="flex-1" />
        <Button variant="primary" size="sm" icon={Plus} onClick={() => void create()} disabled={creating}>
          New workspace
        </Button>
      </header>

      <main className="scroll-thin flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-10 px-6 pb-16 pt-14">
          <section className="flex flex-col items-center gap-5 text-center">
            <div className="flex flex-col gap-2">
              <h1 className="text-[26px] font-semibold tracking-[-0.02em]">What are we making?</h1>
              <p className="text-[14px] text-muted">
                Describe a part, or drop scans and photos. Aida builds it as parametric CAD and checks every step.
              </p>
            </div>
            <StartPill busy={creating} onStart={(start) => void create(start)} />
          </section>

          {error && (
            <p role="alert" className="rounded-lg border border-error/30 bg-error/5 px-3 py-2 text-[13px] text-error">{error}</p>
          )}

          <section className="flex flex-col gap-3" aria-labelledby="workspaces-heading">
            <div className="flex items-baseline gap-2">
              <h2 id="workspaces-heading" className="text-[15px] font-semibold">Workspaces</h2>
              {workspaces && workspaces.length > 0 && (
                <span className="font-mono text-[12px] text-faint">{workspaces.length}</span>
              )}
            </div>
            {workspaces === null && !error ? (
              <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4" aria-busy>
                {[0, 1, 2].map((i) => <div key={i} className="h-[214px] animate-pulse rounded-xl border border-line bg-surface" />)}
              </div>
            ) : workspaces && workspaces.length > 0 ? (
              <ul className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-4">
                {workspaces.map((w) => (
                  <WorkspaceCard key={w.id} workspace={w} onChanged={() => void load()} />
                ))}
              </ul>
            ) : workspaces ? (
              <div className="flex flex-col items-center gap-1 rounded-xl border border-dashed border-line-strong px-6 py-10 text-center">
                <p className="text-[13.5px] font-medium">No workspaces yet</p>
                <p className="text-[13px] text-muted">Start one above. Everything you build is saved here.</p>
              </div>
            ) : null}
          </section>

          <section className="max-w-[420px]">
            <ReplayList />
          </section>
        </div>
      </main>
    </div>
  );
}

/** The first prompt, with scans and photos dropped or attached alongside it. */
function StartPill({ busy, onStart }: { busy: boolean; onStart: (start: { prompt?: string; meshes?: File[]; images?: File[] }) => void }) {
  const [text, setText] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = input.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 148)}px`;
  }, [text]);

  const take = (list: File[]) => {
    const usable = list.filter((f) => isMeshFile(f) || f.type.startsWith('image/'));
    if (usable.length) setFiles((current) => [...current, ...usable].slice(0, 12));
  };
  const browse = () => {
    const picker = document.createElement('input');
    picker.type = 'file';
    picker.multiple = true;
    picker.accept = [...MESH_EXTENSIONS, 'image/*'].join(',');
    picker.onchange = () => picker.files && take([...picker.files]);
    picker.click();
  };
  const canStart = !busy && (text.trim().length > 0 || files.length > 0);
  const submit = (e?: FormEvent) => {
    e?.preventDefault();
    if (!canStart) return;
    onStart({
      prompt: text.trim() || undefined,
      meshes: files.filter(isMeshFile),
      images: files.filter((f) => f.type.startsWith('image/')),
    });
  };

  return (
    <form
      onSubmit={submit}
      className={cx('agent-frame w-full max-w-[680px] rounded-[26px] p-px shadow-float', dragging && 'agent-frame-drop')}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        take([...e.dataTransfer.files]);
      }}
    >
      <div className="flex flex-col gap-2 rounded-[25px] bg-surface py-2 pl-5 pr-2 text-left">
        {files.length > 0 && (
          <div className="flex flex-wrap gap-1.5 pt-1">
            {files.map((file, i) => (
              <span key={`${file.name}-${i}`} className="flex items-center gap-1 rounded-full bg-subtle py-0.5 pl-2 pr-0.5 text-[12px] text-muted">
                {isMeshFile(file) ? <Box size={12} aria-hidden /> : <ImageIcon size={12} aria-hidden />}
                <span className="max-w-48 truncate">{file.name}</span>
                <IconButton icon={X} size="sm" className="size-5" label={`Remove ${file.name}`}
                  onClick={() => setFiles(files.filter((_, j) => j !== i))} />
              </span>
            ))}
          </div>
        )}
        <div className="flex items-end gap-1.5">
          <textarea
            ref={input}
            autoFocus
            rows={1}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            aria-label="Describe what to make"
            placeholder="A wall bracket for a 40 mm pipe, two screw holes…"
            className="min-h-9 flex-1 resize-none bg-transparent py-2 text-[15px] leading-[20px] outline-none placeholder:text-faint"
          />
          <IconButton icon={Upload} label="Add scans or photos" onClick={browse} className="size-9 rounded-full" />
          <button
            type="submit"
            disabled={!canStart}
            aria-label="Start"
            className="flex size-9 shrink-0 items-center justify-center rounded-full bg-accent-ink text-white hover:bg-accent-ink-hover disabled:cursor-not-allowed disabled:bg-line-strong"
          >
            {busy ? <Spinner size={16} /> : <ArrowUp size={17} strokeWidth={2.25} aria-hidden />}
          </button>
        </div>
      </div>
    </form>
  );
}

function ago(seconds: number) {
  const delta = Math.max(0, Date.now() / 1000 - seconds);
  if (delta < 60) return 'just now';
  if (delta < 3600) return `${Math.round(delta / 60)} min ago`;
  if (delta < 86400) return `${Math.round(delta / 3600)} h ago`;
  if (delta < 86400 * 7) return `${Math.round(delta / 86400)} d ago`;
  return new Date(seconds * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function WorkspaceCard({ workspace: w, onChanged }: { workspace: api.WorkspaceSummary; onChanged: () => void }) {
  const [menu, setMenu] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => !menuRef.current?.contains(e.target as Node) && setMenu(false);
    const escape = (e: KeyboardEvent) => e.key === 'Escape' && setMenu(false);
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', escape);
    };
  }, [menu]);

  const parts = w.parts.length;
  const detail = [parts ? `${parts} part${parts === 1 ? '' : 's'}` : 'No parts yet', ago(w.updated_at)].join(' · ');

  return (
    <li className="group relative flex flex-col overflow-hidden rounded-xl border border-line bg-surface transition-shadow hover:shadow-float">
      <a {...linkProps(workspacePath(w.id))} className="flex flex-col" tabIndex={-1} aria-hidden>
        <div className="flex h-[150px] items-center justify-center border-b border-line bg-[#eef0f3]">
          {w.thumbnail ? (
            <img src={`${w.thumbnail}?t=${Math.round(w.updated_at)}`} alt="" className="size-full object-cover" loading="lazy" />
          ) : (
            <Box size={28} strokeWidth={1.25} className="text-line-strong" aria-hidden />
          )}
        </div>
      </a>
      <div className="flex items-start gap-1 p-3 pr-1.5">
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          {renaming ? (
            <RenameField
              initial={w.named ? w.name : ''}
              placeholder={w.name}
              onDone={async (name) => {
                setRenaming(false);
                if (!name || name === w.name) return;
                try {
                  await api.renameWorkspace(w.id, name);
                  onChanged();
                } catch (err) {
                  setError((err as Error).message);
                }
              }}
            />
          ) : (
            <a {...linkProps(workspacePath(w.id))} className="truncate text-[13.5px] font-medium hover:underline" title={w.name}>
              {w.name}
            </a>
          )}
          <span className="flex items-center gap-1.5 text-[12px] text-faint">
            {w.status === 'open' && <span className="size-1.5 rounded-full bg-ok" title="Open now" />}
            {detail}
          </span>
          {error && <span className="text-[12px] text-error">{error}</span>}
        </div>
        <div ref={menuRef} className="relative">
          <IconButton icon={MoreHorizontal} label={`More for ${w.name}`} size="sm" aria-haspopup="menu" aria-expanded={menu}
            onClick={() => setMenu(!menu)} />
          {menu && (
            <div role="menu" className="absolute right-0 top-full z-20 mt-1 flex w-40 flex-col rounded-lg border border-line bg-surface p-1 shadow-pop">
              <button type="button" role="menuitem" onClick={() => { setMenu(false); setRenaming(true); }}
                className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px] hover:bg-subtle">
                <Pencil size={14} strokeWidth={1.75} aria-hidden /> Rename
              </button>
              <button type="button" role="menuitem" onClick={() => { setMenu(false); setConfirming(true); }}
                className="flex items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px] text-error hover:bg-error/8">
                <Trash2 size={14} strokeWidth={1.75} aria-hidden /> Delete
              </button>
            </div>
          )}
        </div>
      </div>
      {confirming && (
        <ConfirmDialog
          title={`Delete “${w.name}”?`}
          body="The workspace moves to the trash folder on the server. Its files are kept, but it disappears from this list."
          confirm="Delete"
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={async () => {
            setBusy(true);
            try {
              await api.deleteWorkspace(w.id);
              setConfirming(false);
              onChanged();
            } catch (err) {
              setError((err as Error).message);
              setConfirming(false);
            } finally {
              setBusy(false);
            }
          }}
        />
      )}
    </li>
  );
}

function ConfirmDialog({ title, body, confirm, busy, onCancel, onConfirm }: {
  title: string;
  body: string;
  confirm: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === 'Escape' && onCancel();
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [onCancel]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/20 p-4" onMouseDown={onCancel}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="confirm-title" aria-describedby="confirm-body"
        onMouseDown={(e) => e.stopPropagation()}
        className="flex w-full max-w-[400px] flex-col gap-3 rounded-xl border border-line bg-surface p-5 shadow-pop">
        <h2 id="confirm-title" className="text-[15px] font-semibold">{title}</h2>
        <p id="confirm-body" className="text-[13px] leading-[1.5] text-muted">{body}</p>
        <div className="flex justify-end gap-2 pt-1">
          <Button autoFocus onClick={onCancel}>Cancel</Button>
          <Button variant="danger" busy={busy} onClick={onConfirm}>{confirm}</Button>
        </div>
      </div>
    </div>
  );
}
