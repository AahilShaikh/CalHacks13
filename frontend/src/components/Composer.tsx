import { useRef, useState } from 'react';
import attachIcon from '../assets/icons/attach.svg';
import sendIcon from '../assets/icons/send.svg';
import stopIcon from '../assets/icons/stop.svg';
import { isMeshFile, useStore } from '../store';

/** Figma: Composer (4:32). Prompt box; Attach takes meshes (one part each) or reference photos. */
export function Composer() {
  const [text, setText] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const status = useStore((s) => s.status);
  const stage = useStore((s) => s.stage);
  const meshFiles = useStore((s) => s.meshFiles);
  const images = useStore((s) => s.images);
  const error = useStore((s) => s.error);
  const units = useStore((s) => s.units);
  const { addMeshes, setUnits, addImages, removeImage, submit, stop } = useStore.getState();

  const live = status === 'running' || status === 'queued';
  // The session stays open between turns; only say "working" while a turn runs.
  const working = live && !['', 'idle', 'preparing', 'cad_starting'].includes(stage);
  const ended = status === 'done' || status === 'failed';
  const asked = useStore((s) => s.feed.some((i) => i.kind === 'user'));
  // The workspace opens with the page; a message typed while it starts waits for it.
  const canSend = !ended && status !== 'idle' && text.trim().length > 0;
  const placeholder = ended
    ? 'Session ended. Reload to start a new one.'
    : working
      ? 'Aida is working. You can queue a follow-up…'
      : asked
        ? 'Ask for a change, or tell Aida what to check next…'
        : 'Describe a part to design, or what to do with the meshes…';

  const send = async () => {
    if (!canSend) return;
    const prompt = text;
    setText('');
    await submit(prompt);
  };

  const onFiles = (files: FileList | null) => {
    if (!files) return;
    const list = [...files];
    const meshes = list.filter(isMeshFile);
    if (meshes.length && !ended) void addMeshes(meshes);
    const photos = list.filter((f) => f.type.startsWith('image/'));
    if (photos.length && !ended) addImages(photos);
  };

  return (
    <div className="flex shrink-0 flex-col gap-2">
      {error && <p className="text-[12px] text-error">{error}</p>}
      <div className="flex flex-col gap-2 rounded-md border border-line-strong bg-sunken p-3 focus-within:border-cad/60">
        {!ended && (meshFiles.length > 0 || images.length > 0) && (
          <div className="flex flex-wrap gap-1.5">
            {meshFiles.map((mesh, index) => (
              <Chip key={`${mesh.name}-${index}`} label={`${mesh.name} · uploading`} tone="cad" />
            ))}
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
          disabled={ended}
          rows={2}
          placeholder={placeholder}
          className="min-h-12 resize-none bg-transparent text-[13px] text-ink outline-none placeholder:text-faint disabled:opacity-60"
        />
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => input.current?.click()}
            disabled={ended}
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
          {!ended && (
            <select
              value={units}
              onChange={(e) => setUnits(e.target.value as 'mm' | '')}
              aria-label="Mesh units"
              title="Units of the uploaded mesh"
              className="rounded-sm border border-line bg-sunken px-1.5 py-1 text-[12px] text-muted outline-none hover:text-ink"
            >
              <option value="mm">mm</option>
              <option value="">unknown units</option>
            </select>
          )}
          <span className="flex-1" />
          {working && (
            <button
              type="button"
              onClick={() => void stop()}
              className="flex items-center gap-1 rounded-sm border border-error px-3 py-2 text-[12px] font-semibold text-error hover:bg-error/10"
            >
              <img src={stopIcon} alt="" width={14} height={14} />
              Stop
            </button>
          )}
          <button
            type="button"
            onClick={() => void send()}
            disabled={!canSend}
            className="flex items-center gap-1 rounded-sm bg-cad px-3 py-2 text-[12px] font-semibold text-base hover:brightness-110 disabled:opacity-40"
          >
            <img src={sendIcon} alt="" width={14} height={14} />
            {working ? 'Queue' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  );
}

function Chip({ label, tone, onRemove }: { label: string; tone: 'cad' | 'muted'; onRemove?: () => void }) {
  return (
    <span className={`flex items-center gap-1 rounded-lg px-2 py-0.5 text-[11px] font-medium ${tone === 'cad' ? 'bg-cad/16 text-cad' : 'bg-muted/16 text-muted'}`}>
      {label}
      {onRemove && (
        <button type="button" aria-label={`Remove ${label}`} onClick={onRemove} className="opacity-70 hover:opacity-100">
          ×
        </button>
      )}
    </span>
  );
}
