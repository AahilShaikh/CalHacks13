import { useEffect, useRef, useState } from 'react';
import { downloadUrl, type DownloadKind } from '../lib/api';
import { Download } from 'lucide-react';
import { useStore } from '../store';
import { Button } from './ui';

const ITEMS: { kind: DownloadKind; label: string; hint: string }[] = [
  { kind: 'step', label: 'STEP', hint: 'Solid for Fusion, SolidWorks, Onshape' },
  { kind: 'stl', label: 'STL', hint: 'Mesh for printing and slicers' },
  { kind: 'model.py', label: 'model.py', hint: 'Parametric build123d source, every part' },
];

/** Download the generated CAD. Errors (e.g. a STEP blocked by validation) show inline. */
export function DownloadMenu() {
  const sessionId = useStore((s) => s.sessionId);
  const hasCad = useStore((s) => s.revisions.some((r) => r.generated));
  const hasModel = useStore((s) => s.workspace?.parts.some((p) => p.features.length > 0) ?? false);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<DownloadKind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  if (!sessionId || !hasCad) return null;

  const save = async (kind: DownloadKind) => {
    setBusy(kind);
    setError(null);
    try {
      const response = await fetch(downloadUrl(sessionId, kind));
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Download failed (${response.status})`);
      }
      const name = response.headers.get('content-disposition')?.match(/filename="?([^";]+)"?/)?.[1] ?? `aida.${kind}`;
      const link = document.createElement('a');
      link.href = URL.createObjectURL(await response.blob());
      link.download = name;
      link.click();
      URL.revokeObjectURL(link.href);
      setOpen(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div ref={ref} className="relative">
      <Button size="sm" icon={Download} aria-expanded={open} aria-haspopup="menu" onClick={() => setOpen(!open)}>
        Export
      </Button>
      {open && (
        <div role="menu" className="absolute right-0 top-full z-40 mt-1.5 flex w-64 flex-col rounded-lg border border-line bg-surface p-1 shadow-pop">
          {ITEMS.map((item) => {
            const disabled = item.kind === 'model.py' && !hasModel;
            return (
              <button
                key={item.kind}
                type="button"
                disabled={disabled || busy !== null}
                role="menuitem"
                onClick={() => void save(item.kind)}
                className="flex flex-col items-start gap-0.5 rounded-md px-3 py-2 text-left hover:bg-subtle disabled:opacity-40 disabled:hover:bg-transparent"
              >
                <span className="text-[13px] font-medium text-ink">{busy === item.kind ? 'Preparing…' : item.label}</span>
                <span className="text-[11px] text-muted">{item.hint}</span>
              </button>
            );
          })}
          {error && <p className="px-3 py-2 text-[11px] leading-[1.4] text-error">{error}</p>}
        </div>
      )}
    </div>
  );
}
