import { useGLTF } from '@react-three/drei';
import { invalidate as invalidateFrame, useLoader, type ThreeEvent } from '@react-three/fiber';
import { useEffect, useMemo } from 'react';
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { OBJLoader } from 'three/examples/jsm/loaders/OBJLoader.js';
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js';
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
  // Deviation heatmap: the reference's own vertex colours (blue extra, white match, red missing).
  heat: new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.75, metalness: 0, flatShading: true }),
  // CAD drawn as an outline over the heatmap, so the colours stay readable.
  outline: new THREE.MeshBasicMaterial({ color: '#6ee7ff', transparent: true, opacity: 0.06, depthWrite: false }),
};
// Parts other than the selected one, when a part is selected.
const dimmed = new THREE.MeshStandardMaterial({ color: '#6ee7ff', roughness: 0.6, transparent: true, opacity: 0.16,
  depthWrite: false, flatShading: true });
const selectedMaterial = new THREE.MeshStandardMaterial({ color: '#a7f3ff', emissive: '#1b6f80', roughness: 0.38,
  metalness: 0.08, flatShading: true, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
const edgeMaterials = {
  cad: new THREE.LineBasicMaterial({ color: '#0b101d', transparent: true, opacity: 0.55 }),
  outline: new THREE.LineBasicMaterial({ color: '#6ee7ff', transparent: true, opacity: 0.9 }),
};

export type Look = keyof typeof materials;

/**
 * The API writes 8-bit sRGB vertex colours, but glTF COLOR_0 is linear, so three.js would
 * show them washed out. Convert once per (cached, shared) geometry.
 */
function toLinearColours(geometry: THREE.BufferGeometry) {
  const colours = geometry.getAttribute('color') as THREE.BufferAttribute | undefined;
  if (!colours || geometry.userData.linearColours) return;
  const c = new THREE.Color();
  for (let i = 0; i < colours.count; i++) {
    c.setRGB(colours.getX(i), colours.getY(i), colours.getZ(i)).convertSRGBToLinear();
    colours.setXYZ(i, c.r, c.g, c.b);
  }
  colours.needsUpdate = true;
  geometry.userData.linearColours = true;
}

// Feature edges per geometry: instances share a geometry, so sixty keycaps compute one.
const edgeCache = new WeakMap<THREE.BufferGeometry, THREE.EdgesGeometry>();
function edgesOf(geometry: THREE.BufferGeometry) {
  let edges = edgeCache.get(geometry);
  if (!edges) {
    edges = new THREE.EdgesGeometry(geometry, 25);
    edgeCache.set(geometry, edges);
  }
  return edges;
}

/** The part a scene node belongs to: nodes are named "<part>|<instance>" by the API. */
export function partOfNode(node: THREE.Object3D | null): string | null {
  for (let at = node; at; at = at.parent) {
    const bar = at.name.indexOf('|');
    if (bar > 0) return at.name.slice(0, bar);
  }
  return null;
}

/** Apply a look to every mesh in a cloned glTF scene; CAD also gets crisp feature edges. */
function styled(source: THREE.Object3D, look: Look) {
  const scene = source.clone(true);
  scene.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.material = materials[look];
    mesh.userData.part = partOfNode(mesh);
    if (look === 'heat') toLinearColours(mesh.geometry);
    if (look === 'cad' || look === 'outline') {
      const edges = new THREE.LineSegments(edgesOf(mesh.geometry), edgeMaterials[look]);
      edges.raycast = () => {};
      mesh.add(edges);
    }
  });
  return scene;
}

/** Highlight one part's meshes (every instance of it) and dim the rest; null restores the look. */
function highlight(scene: THREE.Object3D, look: Look, part: string | null) {
  scene.traverse((node) => {
    const mesh = node as THREE.Mesh;
    if (!mesh.isMesh) return;
    const mine = mesh.userData.part === part;
    mesh.material = part === null || look !== 'cad' ? materials[look] : mine ? selectedMaterial : dimmed;
    for (const child of mesh.children) child.visible = part === null || look !== 'cad' || mine;
  });
}

/**
 * A GLB revision from the API. Suspends while loading; results are cached by URL.
 * ``selected`` highlights one part; ``onPick`` reports the part under a click.
 */
export function GlbModel({ url, look, x = 0, selected = null, onPick }: {
  url: string;
  look: Look;
  x?: number;
  selected?: string | null;
  onPick?: (part: string | null) => void;
}) {
  const gltf = useGLTF(url);
  const scene = useMemo(() => styled(gltf.scene, look), [gltf, look]);
  useEffect(() => {
    highlight(scene, look, selected);
    invalidateFrame(); // the canvas renders on demand
  }, [scene, look, selected]);
  return (
    <primitive
      object={scene}
      position={[x, 0, 0]}
      onClick={
        onPick
          ? (e: ThreeEvent<MouseEvent>) => {
              e.stopPropagation();
              // A drag to orbit is not a pick.
              if (e.delta > 4) return;
              const part = (e.object.userData.part as string | null) ?? null;
              onPick(part === selected ? null : part);
            }
          : undefined
      }
    />
  );
}

/** A dropped mesh before a session starts, read straight from the file; drawn like the reference. */
export function MeshPreview({ url, kind }: { url: string; kind: 'stl' | 'obj' | 'ply' | 'glb' }) {
  if (kind === 'glb') return <ScenePreview url={url} gltf />;
  if (kind === 'obj') return <ScenePreview url={url} />;
  return <GeometryPreview url={url} ply={kind === 'ply'} />;
}

function GeometryPreview({ url, ply }: { url: string; ply: boolean }) {
  const geometry = useLoader(ply ? PLYLoader : STLLoader, url);
  return <mesh geometry={geometry} material={materials.solid} />;
}

function ScenePreview({ url, gltf }: { url: string; gltf?: boolean }) {
  // Loaders are picked by flag, not URL: object URLs have no file extension.
  const loaded = useLoader(gltf ? GLTFLoader : OBJLoader, url) as { scene?: THREE.Object3D } & THREE.Object3D;
  const scene = useMemo(() => {
    const copy = (loaded.scene ?? loaded).clone(true);
    copy.traverse((node) => {
      const mesh = node as THREE.Mesh;
      if (mesh.isMesh) mesh.material = materials.solid;
    });
    return copy;
  }, [loaded]);
  return <primitive object={scene} />;
}
