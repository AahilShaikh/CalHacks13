import { Html, Line, useGLTF } from '@react-three/drei';
import { useFrame, useThree } from '@react-three/fiber';
import { useEffect, useMemo, useRef } from 'react';
import * as THREE from 'three';
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib';
import type { AgentView, Vec3 } from '../../lib/events';
import { activeTool, useStore } from '../../store';

const SLICE = '#fb923c';
const Z = new THREE.Vector3(0, 0, 1);

/** The reference mesh's bounds, in its own (Z-up) coordinates. */
function useBounds(url: string) {
  const gltf = useGLTF(url);
  return useMemo(() => new THREE.Box3().setFromObject(gltf.scene), [gltf]);
}

function planePose(bounds: THREE.Box3, normal: Vec3, offset: number | null) {
  const n = new THREE.Vector3(...normal).normalize();
  const centre = bounds.getCenter(new THREE.Vector3());
  const along = offset ?? centre.dot(n);
  const position = centre.clone().addScaledVector(n, along - centre.dot(n));
  const quaternion = new THREE.Quaternion().setFromUnitVectors(Z, n);
  return { position, quaternion };
}

function Plane({ bounds, normal, offset, label, strength = 1, x = 0 }: {
  bounds: THREE.Box3; normal: Vec3; offset: number | null; label?: string; strength?: number; x?: number;
}) {
  const size = bounds.getSize(new THREE.Vector3()).length() * 0.72;
  const { position, quaternion } = useMemo(() => planePose(bounds, normal, offset), [bounds, normal, offset]);
  const h = size / 2;
  const outline = useMemo(() => [[-h, -h, 0], [h, -h, 0], [h, h, 0], [-h, h, 0], [-h, -h, 0]] as [number, number, number][], [h]);
  return (
    <group position={[position.x + x, position.y, position.z]} quaternion={quaternion}>
      <mesh renderOrder={2}>
        <planeGeometry args={[size, size]} />
        <meshBasicMaterial color={SLICE} transparent opacity={0.16 * strength} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <Line points={outline} color={SLICE} lineWidth={1.25} dashed dashSize={size / 40} gapSize={size / 60}
        transparent opacity={0.85 * strength} />
      {label && (
        <Html position={[h, h, 0]} zIndexRange={[20, 0]} style={{ pointerEvents: 'none' }}>
          <div className="translate-x-2 -translate-y-1/2 whitespace-nowrap rounded-sm border border-slice bg-raised px-2 py-1 font-mono text-[11px] text-ink">
            {label}
          </div>
        </Html>
      )}
    </group>
  );
}

/** A plane sweeping along the axis while slice_series runs and has not reported yet. */
function Sweep({ bounds, normal, axis, x }: { bounds: THREE.Box3; normal: Vec3; axis: string; x: number }) {
  const group = useRef<THREE.Group>(null);
  const n = useMemo(() => new THREE.Vector3(...normal), [normal]);
  const [lo, hi] = useMemo(() => {
    const corners = [bounds.min, bounds.max].map((c) => c.dot(n));
    return [Math.min(...corners), Math.max(...corners)];
  }, [bounds, n]);
  const invalidate = useThree((s) => s.invalidate);
  useFrame(({ clock }) => {
    if (!group.current) return;
    const t = (Math.sin(clock.elapsedTime * 1.6) + 1) / 2;
    group.current.position.copy(n).multiplyScalar(lo + (hi - lo) * t - (lo + hi) / 2);
    invalidate();
  });
  return (
    <group ref={group}>
      <Plane bounds={bounds} normal={normal} offset={null} label={`scanning along ${axis}`} x={x} />
    </group>
  );
}

/** Slice planes for the tool call being mirrored. Lives inside the Z-up model group. */
export function AgentPlanes({ referenceUrl, x }: { referenceUrl: string; x: number }) {
  const bounds = useBounds(referenceUrl);
  const tool = useStore(activeTool);
  const view: AgentView | undefined = tool?.view;
  if (!tool || !view) return null;
  return (
    <>
      {view.slice && (
        <Plane bounds={bounds} normal={view.slice.normal} offset={view.slice.offset}
          label={`${tool.name} · ${view.slice.label}`} x={x} />
      )}
      {view.series &&
        (view.series.offsets ? (
          view.series.offsets.map((offset, i) => (
            <Plane key={i} bounds={bounds} normal={view.series!.normal} offset={offset} strength={0.7} x={x}
              label={i === view.series!.offsets!.length - 1 ? `${tool.name} · ${view.series!.label}` : undefined} />
          ))
        ) : (
          <Sweep bounds={bounds} normal={view.series.normal} axis={view.series.axis} x={x} />
        ))}
    </>
  );
}

// Part (Z-up) to three.js (Y-up): the model group is rotated -90 degrees about X.
const toWorld = ([x, y, z]: Vec3) => new THREE.Vector3(x, z, -y);
const ease = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

/**
 * Flies the camera to the exact view the agent rendered (focal point, direction, zoom).
 * Any user orbit turns following off, so the person stays in control.
 */
export function AgentCamera({ x }: { x: number }) {
  const tool = useStore(activeTool);
  const setFollow = useStore((s) => s.setFollow);
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const controls = useThree((s) => s.controls) as OrbitControlsImpl | null;
  const get = useThree((s) => s.get);
  const invalidate = useThree((s) => s.invalidate);
  // Start pose is captured on the first animated frame, when the orbit controls exist.
  const flight = useRef<{
    to: THREE.Vector3; toTarget: THREE.Vector3; from?: THREE.Vector3; fromTarget?: THREE.Vector3; t: number;
  } | null>(null);

  const spec = tool?.view?.camera;
  const key = spec && tool ? `${tool.id}|${x}` : null;

  useEffect(() => {
    if (!spec) return;
    const target = toWorld(spec.focal).add(new THREE.Vector3(x, 0, 0));
    const dir = toWorld(spec.dir).normalize();
    // Orbit controls keep world Y up; nudge straight-down/up views so the agent's
    // screen-up still reads as up on screen instead of spinning arbitrarily.
    if (Math.abs(dir.y) > 0.98) dir.addScaledVector(toWorld(spec.up), -0.02).normalize();
    // Fit the agent's whole frame (width and height) into this viewport's aspect.
    const half = Math.max(spec.height / 2, spec.width / 2 / camera.aspect);
    const distance = (half / Math.tan(THREE.MathUtils.degToRad(camera.fov) / 2)) * 1.1;
    flight.current = { to: target.clone().addScaledVector(dir, distance), toTarget: target, t: 0 };
    invalidate();
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!controls) return;
    const stop = () => {
      flight.current = null;
      if (useStore.getState().followAgent) setFollow(false);
    };
    controls.addEventListener('start', stop);
    return () => controls.removeEventListener('start', stop);
  }, [controls, setFollow]);

  useFrame((_, delta) => {
    const f = flight.current;
    if (!f) return;
    const orbit = get().controls as OrbitControlsImpl | null;
    if (!orbit) {
      invalidate(); // controls register a frame later; try again then
      return;
    }
    if (!f.from || !f.fromTarget) {
      f.from = camera.position.clone();
      f.fromTarget = orbit.target.clone();
    }
    f.t = Math.min(1, f.t + Math.min(delta, 0.1) / 0.9);
    const k = ease(f.t);
    camera.position.lerpVectors(f.from, f.to, k);
    orbit.target.lerpVectors(f.fromTarget, f.toTarget, k);
    orbit.update();
    if (f.t >= 1) flight.current = null;
    invalidate();
  });
  return null;
}
