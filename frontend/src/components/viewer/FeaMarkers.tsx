import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import type { FeaResult } from '../../lib/fea';

/** Boundary-condition markers stay in the original, Z-up CAD coordinates. */
export function FeaMarkers({ result }: { result: FeaResult }) {
  const length = Math.hypot(...result.bounds_mm[1].map((v, i) => v - result.bounds_mm[0][i]));
  const arrow = useMemo(() => {
    const direction = new THREE.Vector3(...result.force_vector_n).normalize();
    const helper = new THREE.ArrowHelper(direction, new THREE.Vector3(...result.load_center_mm), length * 0.24, '#fb923c', length * 0.05, length * 0.025);
    for (const object of [helper.line, helper.cone]) {
      (object.material as THREE.Material).depthTest = false;
      object.renderOrder = 10;
    }
    return helper;
  }, [result, length]);
  useEffect(() => () => arrow.dispose(), [arrow]);
  return <>
    <primitive object={arrow} />
    <mesh position={new THREE.Vector3(...result.fixed_center_mm)} renderOrder={10}>
      <sphereGeometry args={[length * 0.012, 12, 12]} />
      <meshBasicMaterial color="#4ade80" depthTest={false} />
    </mesh>
  </>;
}
