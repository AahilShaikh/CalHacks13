import { useEffect, useState } from 'react';
import { fetchExample, listExamples } from '../../lib/api';
import { isMeshFile, useStore } from '../../store';
import { FollowControl, Legend, RevisionScrubber, ViewModeToggle } from './Controls';
import { Scene } from './Scene';

/** Figma: "Viewer" in Screen / 1 Empty (7:7) and Screen / 2 Running (7:58). */
export function Viewer() {
  const status = useStore((s) => s.status);
  const stage = useStore((s) => s.stage);
  const meshFiles = useStore((s) => s.meshFiles);
  const revisions = useStore((s) => s.revisions);
  const addMeshes = useStore((s) => s.addMeshes);
  const replay = useStore((s) => s.replay);
  const asked = useStore((s) => s.feed.some((i) => i.kind === 'user'));
  const [dragging, setDragging] = useState(false);

  // The workspace is always open; meshes can be added to it at any time.
  const canDrop = !replay && status !== 'done' && status !== 'failed';
  const empty = revisions.length === 0 && meshFiles.length === 0;
  const hasCad = revisions.some((r) => r.generated);
  const engineStarting = status === 'starting' || stage === 'cad_starting' || stage === 'preparing';

  return (
    <section
      className="relative min-w-0 flex-1 overflow-hidden bg-sunken"
      onDragOver={(e) => {
        if (!canDrop) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
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

      {empty && !asked && !replay && <DropZone />}
      {meshFiles.length > 0 ? (
        <Hint text={`Adding ${meshFiles.map((f) => f.name).join(', ')} to the workspace…`} spinner />
      ) : (
        asked && !hasCad && engineStarting && <Hint text="Starting the CAD engine…" spinner />
      )}

      <div className="pointer-events-none absolute inset-x-4 top-4 flex items-start justify-between gap-4">
        <div className="pointer-events-auto">{hasCad && <ViewModeToggle />}</div>
        <div className="pointer-events-auto flex flex-wrap items-start justify-end gap-2">
          {(asked || replay) && <FollowControl />}
          {hasCad && <Legend />}
        </div>
      </div>
      <div className="pointer-events-none absolute inset-x-0 bottom-4 flex justify-center">
        <div className="pointer-events-auto contents">
          <RevisionScrubber />
        </div>
      </div>

      {dragging && (
        <div className="pointer-events-none absolute inset-3 rounded-lg border-2 border-dashed border-cad/70 bg-cad/5" />
      )}
    </section>
  );
}

function DropZone() {
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
    <div className="absolute inset-0 flex items-center justify-center">
      <div className="flex h-60 w-[440px] flex-col items-center justify-center gap-3 rounded-lg border-[1.5px] border-dashed border-line-strong bg-raised">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path d="M12 2l9 5v10l-9 5-9-5V7z M3 7l9 5 9-5 M12 12v10" stroke="#6EE7FF" strokeWidth="1.5" strokeLinejoin="round" />
        </svg>
        <button type="button" onClick={browse} className="text-[16px] font-semibold hover:text-cad">
          Drop meshes to start
        </button>
        <p className="text-[12px] text-muted">.stl · .obj · .ply · .glb, one part per file</p>
        <p className="text-[12px] text-muted">or describe a part in the panel to design it from scratch</p>
        {examples.slice(0, 2).map((name) => (
          <button
            key={name}
            type="button"
            onClick={() =>
              fetchExample(name)
                .then((file) => void addMeshes([file]))
                .catch((e) => setError(e.message))
            }
            className="rounded-lg bg-cad/16 px-3 py-1 text-[12px] font-medium text-cad hover:bg-cad/25"
          >
            Try the example part: {name}
          </button>
        ))}
        {error && <p className="text-[12px] text-error">{error}</p>}
      </div>
    </div>
  );
}

function Hint({ text, spinner = false }: { text: string; spinner?: boolean }) {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-6 flex justify-center">
      <div className="flex items-center gap-2 rounded-lg border border-line bg-raised/90 px-3 py-1.5 text-[12px] text-muted backdrop-blur">
        {spinner && <span className="size-2 rounded-full bg-cad animate-pulse-dot" />}
        {text}
      </div>
    </div>
  );
}
