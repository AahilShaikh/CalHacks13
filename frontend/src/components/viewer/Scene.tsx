import { Bounds, GizmoHelper, GizmoViewport, OrbitControls, useBounds } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { Suspense, useEffect, useRef } from 'react';
import { activeFea, activeTool, generatedRevisions, referenceRevision, useStore } from '../../store';
import { AgentCamera, AgentPlanes } from './AgentFocus';
import { FeaMarkers } from './FeaMarkers';
import { GlbModel, StlPreview } from './Models';

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
  const preview = useStore((s) => s.meshPreviewUrl);
  const fea = useStore(activeFea);
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
  const heat = viewMode === 'heatmap' ? shown?.heatmap : undefined;

  if (fea && (viewMode === 'fea-stress' || viewMode === 'fea-displacement')) {
    const deformed = viewMode === 'fea-displacement';
    const url = deformed ? fea.displacement_url : fea.stress_url;
    return <group rotation={[-Math.PI / 2, 0, 0]}>
      <Bounds fit clip observe margin={1.7} maxDuration={0.6}>
        <Suspense fallback={shown ? <GlbModel url={shown.url} look="cad" /> : null}>
          <GlbModel url={url} look="heat" />
          {deformed && shown && <GlbModel url={shown.url} look="outline" />}
          <Refit token={url} />
        </Suspense>
      </Bounds>
      <FeaMarkers result={fea} />
    </group>;
  }

  return (
    <>
    <AgentCamera x={-gap} />
    {/* CAD and STL are Z-up; three.js is Y-up. */}
    <group rotation={[-Math.PI / 2, 0, 0]}>
      <Bounds fit clip observe margin={1.7} maxDuration={0.6}>
        {reference ? (
          <Suspense fallback={null}>
            {heat ? (
              <Suspense fallback={<GlbModel url={reference.url} look="ghost" />}>
                <GlbModel url={heat.url} look="heat" />
              </Suspense>
            ) : (
              <GlbModel url={reference.url} look={(shown && !side) || slicing ? 'ghost' : 'solid'} x={-gap} />
            )}
            <Refit token={`${reference.url}|${side ? 'side' : 'single'}`} />
          </Suspense>
        ) : (
          preview && (
            <Suspense fallback={null}>
              <StlPreview url={preview} />
              <Refit token={preview} />
            </Suspense>
          )
        )}
        {shown && (
          <Suspense fallback={fallback ? <GlbModel url={fallback} look={heat ? 'outline' : 'cad'} x={gap} /> : null}>
            <GlbModel url={shown.url} look={heat ? 'outline' : 'cad'} x={gap} />
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
    >
      <color attach="background" args={['#080c16']} />
      <hemisphereLight args={['#c7d6ff', '#0b101d', 0.9]} />
      <directionalLight position={[5, 9, 7]} intensity={1.8} />
      <directionalLight position={[-7, 4, -5]} intensity={0.6} color="#9db4ff" />
      <Models />
      <OrbitControls makeDefault enableDamping dampingFactor={0.1} />
      <GizmoHelper alignment="bottom-left" margin={[56, 56]}>
        {/* World axes relabelled to the part's Z-up frame: world Y is part Z, world Z is part -Y. */}
        <GizmoViewport axisColors={['#f87171', '#3dadff', '#4ade80']} labels={['X', 'Z', '-Y']} labelColor="#0b101d" />
      </GizmoHelper>
    </Canvas>
  );
}
