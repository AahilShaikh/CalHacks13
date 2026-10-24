import { Bounds, GizmoHelper, GizmoViewport, OrbitControls, useBounds } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { Suspense, useEffect, useRef } from 'react';
import { activeTool, generatedRevisions, heatmapOf, referenceRevision, useStore } from '../../store';
import { AgentCamera, AgentPlanes } from './AgentFocus';
import { GlbModel, MeshPreview } from './Models';

/** Re-frame the camera when the reference geometry changes (not on every CAD revision). */
function Refit({ token }: { token: string }) {
  const bounds = useBounds();
  useEffect(() => {
    bounds.refresh().clip().fit();
  }, [token]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

function Models() {
  const revisions = useStore((s) => s.revisions);
  const shownRevision = useStore((s) => s.shownRevision);
  const viewMode = useStore((s) => s.viewMode);
  const previews = useStore((s) => s.meshPreviews);
  const workspace = useStore((s) => s.workspace);
  const selectedPart = useStore((s) => s.selectedPart);
  const selectPart = useStore((s) => s.selectPart);
  const hidden = useStore((s) => s.hiddenParts);
  // 'assembly' selects the assembly in the Model tab; in 3D it highlights nothing.
  const highlighted = selectedPart === 'assembly' ? null : selectedPart;
  // Show the scan as a ghost while a slice plane is up, so the plane reads through it.
  const slicing = useStore((s) => {
    const view = activeTool(s)?.view;
    return Boolean(view?.slice || view?.series);
  });

  const reference = referenceRevision(revisions);
  const generated = generatedRevisions(revisions);
  const shown = shownRevision === null ? generated.at(-1) : generated.find((r) => r.revision === shownRevision);
  // Keep showing the last loaded revision while the next one downloads.
  const lastShown = useRef<string | null>(null);
  const fallback = lastShown.current && lastShown.current !== shown?.url ? lastShown.current : null;
  useEffect(() => {
    if (shown) lastShown.current = shown.url;
  }, [shown]);

  const side = viewMode === 'side' && reference && shown;
  const gap = side ? (reference.extents[0] || 1) * 0.65 : 0;
  // Heatmap: the scan coloured by distance to the shown revision, CAD as an outline on top.
  const heat = viewMode === 'heatmap' ? heatmapOf(shown, workspace, selectedPart) : undefined;

  return (
    <>
    <AgentCamera x={-gap} />
    {/* CAD and STL are Z-up; three.js is Y-up. */}
    <group rotation={[-Math.PI / 2, 0, 0]}>
      <Bounds fit clip observe margin={1.7} maxDuration={0.6}>
        {reference ? (
          <Suspense fallback={null}>
            {heat ? (
              <Suspense fallback={<GlbModel hidden={hidden} url={reference.url} look="ghost" />}>
                <GlbModel hidden={hidden} url={heat.url} look="heat" />
              </Suspense>
            ) : (
              <GlbModel hidden={hidden} url={reference.url} look={(shown && !side) || slicing ? 'ghost' : 'solid'} x={-gap} />
            )}
            <Refit token={`${reference.url}|${side ? 'side' : 'single'}`} />
          </Suspense>
        ) : (
          previews.length > 0 && (
            <Suspense fallback={null}>
              {previews.map((preview) => (
                <MeshPreview key={preview.url} {...preview} />
              ))}
              <Refit token={previews.map((p) => p.url).join('|')} />
            </Suspense>
          )
        )}
        {shown && (
          <Suspense
            fallback={
              fallback ? <GlbModel hidden={hidden} url={fallback} look={heat ? 'outline' : 'cad'} x={gap} selected={highlighted} /> : null
            }
          >
            <GlbModel hidden={hidden} url={shown.url} look={heat ? 'outline' : 'cad'} x={gap} selected={highlighted} onPick={selectPart} />
          </Suspense>
        )}
      </Bounds>
      {/* Outside Bounds so slice planes never change the auto-framing. */}
      {reference && (
        <Suspense fallback={null}>
          <AgentPlanes referenceUrl={reference.url} x={-gap} />
        </Suspense>
      )}
    </group>
    </>
  );
}

export function Scene() {
  return (
    <Canvas
      frameloop="demand"
      dpr={[1, 2]}
      camera={{ position: [1.2, 0.9, 1.4], fov: 40, near: 0.01, far: 100000 }}
      gl={{ antialias: true }}
      className="!absolute inset-0"
      onPointerMissed={(e) => {
        // A click on empty space clears the part selection (not the end of an orbit drag).
        const { selectedPart, selectPart } = useStore.getState();
        if (selectedPart && selectedPart !== 'assembly' && e.type === 'click') selectPart(null);
      }}
    >
      <color attach="background" args={['#eef0f3']} />
      <hemisphereLight args={['#ffffff', '#b9bec7', 1.6]} />
      <directionalLight position={[5, 9, 7]} intensity={1.7} />
      <directionalLight position={[-7, 4, -5]} intensity={0.55} color="#e6ebf5" />
      <Models />
      <OrbitControls makeDefault enableDamping dampingFactor={0.1} />
      <GizmoHelper alignment="top-left" margin={[52, 100]}>
        {/* World axes relabelled to the part's Z-up frame: world Y is part Z, world Z is part -Y. */}
        <GizmoViewport axisColors={['#dc2626', '#2563eb', '#15803d']} labels={['X', 'Z', '-Y']} labelColor="#ffffff" />
      </GizmoHelper>
    </Canvas>
  );
}
