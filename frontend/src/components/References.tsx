import { Box, ChevronRight, Plus, RotateCw, Upload } from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { MESH_EXTENSIONS, isMeshFile, referenceRevision, useStore } from '../store';
import { IconButton, Spinner, cx } from './ui';

/**
 * Everything Aida can look at in this workspace: scans (each one is a part) and photos.
 * Replaces the old Attach button; files can also be dropped on the 3D view or the prompt.
 */
export function References() {
  const [open, setOpen] = useState(true);
  const [dragging, setDragging] = useState(false);
  const workspace = useStore((s) => s.workspace);
  const revisions = useStore((s) => s.revisions);
  const references = useStore((s) => s.references);
  const meshFiles = useStore((s) => s.meshFiles);
  const images = useStore((s) => s.images);
  const selected = useStore((s) => s.selectedPart);
  const replay = useStore((s) => s.replay);
  const { addMeshes, addImages, selectPart } = useStore.getState();
  const canAdd = !replay;

  // The server's list; recordings from before it existed fall back to the scanned parts.
  const fallback = workspace?.parts.length
    ? workspace.parts.filter((p) => p.has_reference).map((p) => p.name)
    : referenceRevision(revisions)?.parts ?? [];
  const scans = references.length
    ? references.flatMap((r) => (r.kind === 'mesh' ? [{ part: r.part, name: r.name }] : []))
    : fallback.map((part) => ({ part, name: `${part} scan` }));
  const photos = references.flatMap((r) => (r.kind === 'photo' ? [r] : []));
  const count = scans.length + meshFiles.length + images.length + photos.length;

  const take = (files: File[]) => {
    if (!canAdd) return;
    const meshes = files.filter(isMeshFile);
    if (meshes.length) void addMeshes(meshes);
    const photos = files.filter((f) => f.type.startsWith('image/'));
    if (photos.length) addImages(photos);
  };
  const browse = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = [...MESH_EXTENSIONS, 'image/*'].join(',');
    input.onchange = () => input.files?.length && take([...input.files]);
    input.click();
  };

  return (
    <section
      aria-label="References"
      className={cx('flex min-h-0 shrink-0 flex-col border-t border-line', open && 'max-h-[45%]',
        dragging && 'bg-accent-soft/50')}
      onDragOver={(e) => {
        if (!canAdd) return;
        e.preventDefault();
        e.stopPropagation();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setDragging(false);
        take([...e.dataTransfer.files]);
      }}
    >
      <div className="flex h-10 shrink-0 items-center gap-1 pl-1.5 pr-1.5">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
          className="flex h-7 items-center gap-1 rounded-md pl-0.5 pr-2 hover:bg-subtle">
          <ChevronRight size={13} className={cx('text-faint transition-transform', open && 'rotate-90')} aria-hidden />
          <h2 className="text-[13px] font-semibold">References</h2>
          {count > 0 && <span className="pl-1 font-mono text-[11px] text-faint">{count}</span>}
        </button>
        <span className="flex-1" />
        {canAdd && <UnitsSelect />}
        {canAdd && <IconButton icon={Plus} label="Add scans or photos" size="sm" onClick={browse} />}
      </div>

      {open && (
        <ul className="scroll-thin flex min-h-0 flex-col gap-px overflow-y-auto px-1.5 pb-2">
          {scans.map(({ part, name }) => (
            <Row
              key={part}
              icon={<Box size={14} strokeWidth={1.75} className="text-mesh" aria-hidden />}
              title={name}
              detail={`Scan · part ${part}`}
              selected={selected === part}
              onClick={() => selectPart(selected === part ? null : part)}
            />
          ))}
          {meshFiles.map((file, i) => (
            <Row key={`${file.name}-${i}`} icon={<Spinner size={13} className="text-accent" />} title={file.name}
              detail="Uploading…" />
          ))}
          <Photos files={images} />
          {photos.map((photo) => (
            <Row key={photo.url} icon={<Thumb url={photo.url} />} title={photo.name} detail="Photo · shared with Aida" />
          ))}

          {canAdd && (count === 0 ? (
            <li>
              <button type="button" onClick={browse}
                className="flex w-full flex-col items-center gap-1 rounded-lg border border-dashed border-line-strong px-3 py-4 text-center hover:border-accent hover:bg-accent-soft/40">
                <Upload size={16} strokeWidth={1.75} className="text-faint" aria-hidden />
                <span className="text-[12.5px] font-medium text-ink">Drop scans or photos</span>
                <span className="text-[11.5px] text-faint">.stl .obj .ply .glb, or images · or browse</span>
              </button>
            </li>
          ) : null)}

          <Row
            icon={<RotateCw size={14} strokeWidth={1.75} className="text-faint" aria-hidden />}
            title="Scan from turntable"
            detail="Turntable not connected"
            disabled
          />
        </ul>
      )}
    </section>
  );
}

function UnitsSelect() {
  const units = useStore((s) => s.units);
  const setUnits = useStore((s) => s.setUnits);
  return (
    <select
      value={units}
      onChange={(e) => setUnits(e.target.value as 'mm' | '')}
      aria-label="Units of added scans"
      title="Units of the scans you add"
      className="h-6 rounded-md border border-line bg-surface px-1 text-[11.5px] text-muted outline-none hover:border-line-strong hover:text-ink"
    >
      <option value="mm">mm</option>
      <option value="">unknown units</option>
    </select>
  );
}

/** Photos waiting to go out with the next message, with thumbnails. */
function Photos({ files }: { files: File[] }) {
  const removeImage = useStore((s) => s.removeImage);
  const urls = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);
  useEffect(() => () => urls.forEach((u) => URL.revokeObjectURL(u)), [urls]);
  return (
    <>
      {files.map((file, i) => (
        <Row key={urls[i]} icon={<Thumb url={urls[i]} />} title={file.name} detail="Photo · goes with your next message"
          trailing={
            <button type="button" onClick={() => removeImage(i)} aria-label={`Remove ${file.name}`}
              className="shrink-0 rounded-sm px-1.5 text-[11.5px] text-faint hover:bg-surface hover:text-ink">
              Remove
            </button>
          } />
      ))}
    </>
  );
}

function Thumb({ url }: { url: string }) {
  return <img src={url} alt="" className="size-5 shrink-0 rounded-sm border border-line object-cover" />;
}

function Row({ icon, title, detail, selected = false, onClick, disabled = false, trailing }: {
  icon: ReactNode;
  title: string;
  detail: string;
  selected?: boolean;
  onClick?: () => void;
  disabled?: boolean;
  trailing?: ReactNode;
}) {
  const body = (
    <>
      <span className="flex size-6 shrink-0 items-center justify-center">{icon}</span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className={cx('truncate text-[12.5px]', disabled ? 'text-muted' : 'text-ink')}>{title}</span>
        <span className="truncate text-[11px] text-faint">{detail}</span>
      </span>
    </>
  );
  const row = 'flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left';
  return (
    <li className="group flex items-center">
      {onClick ? (
        <button type="button" onClick={onClick} aria-pressed={selected}
          className={cx(row, selected ? 'bg-accent-soft' : 'hover:bg-subtle')}>
          {body}
        </button>
      ) : (
        <div className={row} aria-disabled={disabled || undefined}
          title={disabled ? 'Aida will scan whatever is on the turntable once one is connected' : undefined}>
          {body}
          {trailing}
        </div>
      )}
    </li>
  );
}
