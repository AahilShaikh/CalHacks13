import { useEffect, useState } from 'react';
import { fetchExample, listExamples } from '../../lib/api';
import { isMeshFile, useStore } from '../../store';
import { Legend, RevisionScrubber, ViewModeToggle } from './Controls';
import { Scene } from './Scene';

/** Figma: "Viewer" in Screen / 1 Empty (7:7) and Screen / 2 Running (7:58). */
export function Viewer() {
  const status = useStore((s) => s.status);
  const stage = useStore((s) => s.stage);
  const meshFile = useStore((s) => s.meshFile);
  const revisions = useStore((s) => s.revisions);
  const setMesh = useStore((s) => s.setMesh);
  const [dragging, setDragging] = useState(false);

  const hasSession = status !== 'idle';
  const hasCad = revisions.some((r) => r.generated);

  return (
    <section
      className="relative min-w-0 flex-1 overflow-hidden bg-sunken"
      onDragOver={(e) => {
        if (hasSession) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        if (hasSession) return;
        e.preventDefault();
        setDragging(false);
        const mesh = [...e.dataTransfer.files].find(isMeshFile);
        if (mesh) setMesh(mesh);
        const photos = [...e.dataTransfer.files].filter((f) => f.type.startsWith('image/'));
        if (photos.length) useStore.getState().addImages(photos);
      }}
    >
      <Scene />

      {!hasSession && !meshFile && <DropZone />}
      {!hasSession && meshFile && (
        <Hint text={`${meshFile.name} loaded. Describe the part in the panel to start.`} />
      )}
      {hasSession && !hasCad && (status === 'starting' || stage === 'cad_starting' || stage === 'preparing') && (
        <Hint text={status === 'starting' ? 'Uploading mesh…' : 'Starting the CAD engine…'} spinner />
      )}

      <div className="pointer-events-none absolute inset-x-4 top-4 flex items-start justify-between gap-4">
        <div className="pointer-events-auto">{hasCad && <ViewModeToggle />}</div>
        <div className="pointer-events-auto">{hasCad && <Legend />}</div>
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
  const setMesh = useStore((s) => s.setMesh);
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
    input.accept = '.stl,.obj,.ply,.glb';
    input.onchange = () => input.files?.[0] && setMesh(input.files[0]);
    input.click();
  };

  return (
    <div className="absolute inset-0 flex items-center justify-center">
      <div className="flex h-60 w-[440px] flex-col items-center justify-center gap-3 rounded-lg border-[1.5px] border-dashed border-line-strong bg-raised">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path d="M12 2l9 5v10l-9 5-9-5V7z M3 7l9 5 9-5 M12 12v10" stroke="#6EE7FF" strokeWidth="1.5" strokeLinejoin="round" />
        </svg>
        <button type="button" onClick={browse} className="text-[16px] font-semibold hover:text-cad">
          Drop a mesh to start
        </button>
        <p className="text-[12px] text-muted">.stl · .obj · .ply · .glb, or attach photos for context</p>
        {examples.slice(0, 2).map((name) => (
          <button
            key={name}
            type="button"
            onClick={() =>
              fetchExample(name)
                .then(setMesh)
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
