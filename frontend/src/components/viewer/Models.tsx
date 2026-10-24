import { useGLTF } from '@react-three/drei';
import { useLoader } from '@react-three/fiber';
import { useMemo } from 'react';
import * as THREE from 'three';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';

// Shared materials: one instance each, however many revisions are shown.
// Flat shading derives face normals in the shader: the API's GLBs carry no vertex
// normals (lit materials would render black), and CAD faces are planar anyway.
const materials = {
  cad: new THREE.MeshStandardMaterial({ color: '#6ee7ff', roughness: 0.42, metalness: 0.08, flatShading: true,
    polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }),
  ghost: new THREE.MeshStandardMaterial({ color: '#a5b4fc', roughness: 0.85, transparent: true, opacity: 0.2,
    depthWrite: false, side: THREE.DoubleSide, flatShading: true }),
  solid: new THREE.MeshStandardMaterial({ color: '#a5b4fc', roughness: 0.7, metalness: 0.02, flatShading: true }),
};
const edgeMaterial = new THREE.LineBasicMaterial({ color: '#0b101d', transparent: true, opacity: 0.55 });

export type Look = keyof typeof materials;

/** Apply a look to every mesh in a cloned glTF scene; CAD also gets crisp feature edges. */
function styled(source: THREE.Object3D, look: Look) {
  const scene = source.clone(true);
  scene.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.material = materials[look];
    if (look === 'cad') {
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 25), edgeMaterial);
      edges.raycast = () => {};
      mesh.add(edges);
    }
  });
  return scene;
}

/** A GLB revision from the API. Suspends while loading; results are cached by URL. */
export function GlbModel({ url, look, x = 0 }: { url: string; look: Look; x?: number }) {
  const gltf = useGLTF(url);
  const scene = useMemo(() => styled(gltf.scene, look), [gltf, look]);
  return <primitive object={scene} position={[x, 0, 0]} />;
}

/** The user's STL before a session starts, read straight from the dropped file. */
export function StlPreview({ url }: { url: string }) {
  const geometry = useLoader(STLLoader, url);
  return <mesh geometry={geometry} material={materials.solid} />;
}
