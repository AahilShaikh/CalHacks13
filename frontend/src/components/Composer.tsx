import { useRef, useState } from 'react';
import attachIcon from '../assets/icons/attach.svg';
import sendIcon from '../assets/icons/send.svg';
import { isMeshFile, useStore } from '../store';

/** Figma: Composer (4:32). Prompt box; Attach takes a mesh or reference photos. */
export function Composer() {
  const [text, setText] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const status = useStore((s) => s.status);
  const meshFile = useStore((s) => s.meshFile);
  const images = useStore((s) => s.images);
  const error = useStore((s) => s.error);
  const { setMesh, addImages, removeImage, submit } = useStore.getState();

  const live = status === 'running' || status === 'queued';
  const ended = status === 'done' || status === 'failed';
  const busy = status === 'starting';
  const canSend = !busy && !ended && text.trim().length > 0 && (live || meshFile !== null);
  const placeholder = live
    ? 'Aida is working. You can queue a follow-up…'
    : ended
      ? 'Session ended. Reload to start a new one.'
      : 'Describe the part, or ask for a change…';

  const send = async () => {
    if (!canSend) return;
    const prompt = text;
    setText('');
    await submit(prompt);
  };

  const onFiles = (files: FileList | null) => {
    if (!files) return;
    const list = [...files];
    const mesh = list.find(isMeshFile);
    if (mesh && !live) setMesh(mesh);
    const photos = list.filter((f) => f.type.startsWith('image/'));
    if (photos.length && !live) addImages(photos);
  };

  return (
    <div className="flex shrink-0 flex-col gap-2">
      {error && <p className="text-[12px] text-error">{error}</p>}
      <div className="flex flex-col gap-2 rounded-md border border-line-strong bg-sunken p-3 focus-within:border-cad/60">
        {!live && !ended && (meshFile || images.length > 0) && (
          <div className="flex flex-wrap gap-1.5">
            {meshFile && (
              <Chip label={meshFile.name} tone="cad" onRemove={() => setMesh(null)} />
            )}
            {images.map((image, index) => (
              <Chip key={`${image.name}-${index}`} label={image.name} tone="muted" onRemove={() => removeImage(index)} />
            ))}
          </div>
        )}
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          disabled={ended || busy}
          rows={2}
          placeholder={placeholder}
          className="min-h-12 resize-none bg-transparent text-[13px] text-ink outline-none placeholder:text-faint disabled:opacity-60"
        />
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => input.current?.click()}
            disabled={live || ended}
            className="flex items-center gap-1 rounded-sm px-3 py-2 text-[12px] font-semibold text-muted hover:text-ink disabled:opacity-40"
          >
            <img src={attachIcon} alt="" width={14} height={14} />
            Attach
          </button>
          <input
            ref={input}
            type="file"
            multiple
            hidden
            accept=".stl,.obj,.ply,.glb,image/*"
            onChange={(e) => {
              onFiles(e.target.files);
              e.target.value = '';
            }}
          />
          <span className="flex-1" />
          <button
            type="button"
            onClick={() => void send()}
            disabled={!canSend}
            className="flex items-center gap-1 rounded-sm bg-cad px-3 py-2 text-[12px] font-semibold text-base hover:brightness-110 disabled:opacity-40"
          >
            <img src={sendIcon} alt="" width={14} height={14} />
            {busy ? 'Starting…' : live ? 'Queue' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Chip({ label, tone, onRemove }: { label: string; tone: 'cad' | 'muted'; onRemove: () => void }) {
  return (
    <span className={`flex items-center gap-1 rounded-lg px-2 py-0.5 text-[11px] font-medium ${tone === 'cad' ? 'bg-cad/16 text-cad' : 'bg-muted/16 text-muted'}`}>
      {label}
      <button type="button" aria-label={`Remove ${label}`} onClick={onRemove} className="opacity-70 hover:opacity-100">
        ×
      </button>
    </span>
  );
}
