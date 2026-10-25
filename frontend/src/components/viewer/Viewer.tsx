import { Box, Upload } from 'lucide-react';
import { useEffect, useState } from 'react';
import { fetchExample, listExamples } from '../../lib/api';
import { isMeshFile, useStore } from '../../store';
import { AgentDock } from '../AgentDock';
import { Button } from '../ui';
import { FollowControl, Legend, RevisionCard, ViewModeToggle } from './Controls';
import { Scene } from './Scene';

/** The 3D viewer fills the centre; controls float over it and the agent dock sits at the bottom. */
export function Viewer() {
  const meshFiles = useStore((s) => s.meshFiles);
  const revisions = useStore((s) => s.revisions);
  const addMeshes = useStore((s) => s.addMeshes);
  const replay = useStore((s) => s.replay);
  const asked = useStore((s) => s.feed.some((i) => i.kind === 'user'));
  const [dragging, setDragging] = useState(false);

  // The workspace is always open; meshes can be added to it at any time.
  const canDrop = !replay;
  const empty = revisions.length === 0 && meshFiles.length === 0;
  const hasCad = revisions.some((r) => r.generated);

  return (
    <section
      aria-label="3D view"
      className="relative min-w-0 flex-1 overflow-hidden bg-[#eef0f3]"
      onDragOver={(e) => {
        if (!canDrop) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false);
      }}
      onDrop={(e) => {
        if (!canDrop) return;
        e.preventDefault();
        setDragging(false);
        const meshes = [...e.dataTransfer.files].filter(isMeshFile);
        if (meshes.length) void addMeshes(meshes);
        const photos = [...e.dataTransfer.files].filter((f) => f.type.startsWith('image/'));
        if (photos.length) useStore.getState().addImages(photos);
      }}
    >
      <Scene />

      {empty && !asked && !replay && <EmptyState />}

      <div className="pointer-events-none absolute inset-x-3 top-3 z-10 flex items-start justify-between gap-3">
        <div className="pointer-events-auto">{hasCad && <ViewModeToggle />}</div>
        <div className="pointer-events-auto flex flex-col items-end gap-2">
          {(asked || replay) && <FollowControl />}
          <RevisionCard />
          {hasCad && <Legend />}
        </div>
      </div>

      <AgentDock />

      {dragging && (
        <div className="pointer-events-none absolute inset-3 z-30 flex items-center justify-center rounded-xl border-2 border-dashed border-accent bg-accent-soft/40">
          <span className="rounded-full bg-surface px-3 py-1.5 text-[13px] font-medium text-accent-ink shadow-float">
            Drop to add as references
          </span>
        </div>
      )}
    </section>
  );
}

function EmptyState() {
  const addMeshes = useStore((s) => s.addMeshes);
  const [examples, setExamples] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    listExamples()
      .then((list) => setExamples(list.map((e) => e.name)))
      .catch(() => setExamples([]));
  }, []);

  const browse = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = '.stl,.obj,.ply,.glb';
    input.onchange = () => input.files?.length && void addMeshes([...input.files]);
    input.click();
  };

  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center pb-28">
      <div className="pointer-events-auto flex w-[400px] max-w-[calc(100%-32px)] flex-col items-center gap-4 text-center">
        <div className="flex size-11 items-center justify-center rounded-xl border border-line bg-surface shadow-float">
          <Box size={20} strokeWidth={1.5} className="text-accent" aria-hidden />
        </div>
        <div className="flex flex-col gap-1.5">
          <h2 className="text-[17px] font-semibold tracking-[-0.01em]">Start from a scan, or from a sentence</h2>
          <p className="text-[13px] leading-[1.5] text-muted">
            Drop .stl, .obj, .ply or .glb files anywhere here and Aida rebuilds each one as a parametric part.
            Or describe a part below and Aida designs it.
          </p>
        </div>
        <div className="flex flex-wrap justify-center gap-2">
          <Button icon={Upload} onClick={browse}>Add scans</Button>
          {examples.slice(0, 1).map((name) => (
            <Button key={name} variant="ghost"
              onClick={() => fetchExample(name).then((file) => void addMeshes([file])).catch((e) => setError(e.message))}>
              Try an example: {name}
            </Button>
          ))}
        </div>
        {error && <p className="text-[12px] text-error">{error}</p>}
      </div>
    </div>
  );
}
