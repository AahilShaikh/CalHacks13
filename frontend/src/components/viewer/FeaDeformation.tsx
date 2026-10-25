import { useGLTF } from '@react-three/drei';
import { useFrame } from '@react-three/fiber';
import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import { heatMaterial, toLinearColours } from './Models';

const PERIOD_S = 2.4;

function firstMesh(scene: THREE.Object3D): THREE.Mesh | null {
  let found: THREE.Mesh | null = null;
  scene.updateMatrixWorld(true);
  scene.traverse((node) => {
    if (!found && (node as THREE.Mesh).isMesh) found = node as THREE.Mesh;
  });
  return found;
}

/**
 * The tested part swinging between its original and deformed (magnified) shape, coloured by
 * displacement. Both result GLBs share vertex order (same surface, positions differ), so the
 * animation blends their positions. Returns null if they do not line up.
 */
export function FeaDeformation({ originalUrl, deformedUrl, playing }: {
  originalUrl: string;
  deformedUrl: string;
  playing: boolean;
}) {
  const original = useGLTF(originalUrl);
  const deformed = useGLTF(deformedUrl);
  const model = useMemo(() => {
    const a = firstMesh(original.scene);
    const b = firstMesh(deformed.scene);
    if (!a || !b) return null;
    const from = a.geometry.getAttribute('position');
    const to = b.geometry.getAttribute('position');
    if (!from || !to || from.count !== to.count) return null;
    const geometry = b.geometry.clone();
    toLinearColours(geometry);
    const mesh = new THREE.Mesh(geometry, heatMaterial);
    mesh.applyMatrix4(b.matrixWorld);
    mesh.frustumCulled = false; // the shape moves; its cached bounds would not
    return { mesh, from: from.array as ArrayLike<number>, to: to.array as ArrayLike<number> };
  }, [original, deformed]);
  useEffect(() => () => model?.mesh.geometry.dispose(), [model]);

  useFrame((state) => {
    if (!model) return;
    // Paused: hold the fully deformed shape.
    const t = playing ? (1 - Math.cos((state.clock.elapsedTime * 2 * Math.PI) / PERIOD_S)) / 2 : 1;
    const position = model.mesh.geometry.getAttribute('position') as THREE.BufferAttribute;
    const values = position.array as Float32Array;
    for (let i = 0; i < values.length; i++) values[i] = model.from[i] + (model.to[i] - model.from[i]) * t;
    position.needsUpdate = true;
    if (playing) state.invalidate(); // the canvas renders on demand: keep the loop going
  });

  return model ? <primitive object={model.mesh} /> : null;
}
