// Aida CAD Studio — live CAD viewer.
// Streams session events over SSE, loads revisioned GLB meshes, and morphs
// between revisions with a GPU particle transition.

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

// ---------------------------------------------------------------- DOM ------

const $ = (id) => document.getElementById(id);
const dom = {
  canvas: $('viewport'),
  empty: $('viewport-empty'),
  status: $('viewport-status'),
  statusText: $('viewport-status-text'),
  stagePill: $('stage-pill'),
  revLabel: $('rev-label'),
  feed: $('feed'),
  feedCount: $('feed-count'),
  meshInput: $('mesh-input'),
  meshDrop: $('mesh-drop'),
  meshChip: $('mesh-chip'),
  imageInput: $('image-input'),
  imageDrop: $('image-drop'),
  imageChips: $('image-chips'),
  promptInput: $('prompt-input'),
  unitsInput: $('units-input'),
  startBtn: $('start-btn'),
  setupError: $('setup-error'),
  summary: $('session-summary'),
  summaryBody: $('summary-body'),
  closeBtn: $('close-btn'),
  composer: $('composer'),
  followup: $('followup-input'),
  sendBtn: $('send-btn'),
  toggleReference: $('toggle-reference'),
  toggleRotate: $('toggle-rotate'),
};

const state = {
  sessionId: null,
  source: null,
  lastSeq: 0,
  meshFile: null,
  imageFiles: [],
  status: 'idle',
  stage: '',
  revisions: 0,
  feedStick: true,
  events: 0,
};

// ---------------------------------------------------------------- utils ----

const fmtNum = (n) => n >= 1e6 ? (n / 1e6).toFixed(1) + 'M'
  : n >= 1e3 ? (n / 1e3).toFixed(1) + 'k' : String(n);

const fmtBytes = (b) => b >= 1 << 20 ? (b / (1 << 20)).toFixed(1) + ' MB'
  : b >= 1 << 10 ? (b / (1 << 10)).toFixed(0) + ' KB' : b + ' B';

const fmtExtents = (e) => e.map((v) => v < 10 ? v.toFixed(1) : Math.round(v)).join(' × ');

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

// ---------------------------------------------------------------- viewer ---

const loader = new GLTFLoader();

class Viewer {
  constructor(canvas) {
    this.renderer = new THREE.WebGLRenderer({
      canvas, antialias: true, alpha: true, powerPreference: 'high-performance',
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.01, 5000);
    this.camera.position.set(6.5, 4.5, 8);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.autoRotateSpeed = 0.7;

    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(5, 9, 7);
    const fill = new THREE.DirectionalLight(0x9db4ff, 0.7);
    fill.position.set(-7, 4, -5);
    const rim = new THREE.DirectionalLight(0x6ee7ff, 0.5);
    rim.position.set(0, -6, -8);
    this.scene.add(key, fill, rim,
      new THREE.HemisphereLight(0xbfd4ff, 0x0b1020, 0.55));

    this.grid = null;
    this.reference = null;   // ghost overlay + original for the first mesh
    this.current = null;     // main object
    this.transition = null;
    this.camTween = null;
    this.referenceVisible = true;

    this.resize();
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.canvasBox();
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  canvasBox() {
    return this.renderer.domElement.parentElement;
  }

  // -- framing --------------------------------------------------------------
  boxOf(object) {
    return new THREE.Box3().setFromObject(object);
  }

  frameTo(box, animate = true) {
    const center = box.getCenter(new THREE.Vector3());
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);
    const distance = radius / Math.sin((this.camera.fov * Math.PI / 180) / 2) * 1.15;
    const dir = this.camera.position.clone().sub(this.controls.target).normalize();
    if (dir.lengthSq() < 1e-6) dir.set(1, 0.85, 1).normalize();
    const toPos = center.clone().add(dir.multiplyScalar(distance));
    this.controls.minDistance = radius * 0.2;
    this.controls.maxDistance = radius * 20;
    if (!animate) {
      this.camera.position.copy(toPos);
      this.controls.target.copy(center);
      return;
    }
    this.camTween = {
      t0: performance.now(), dur: 900,
      fromPos: this.camera.position.clone(), toPos,
      fromTarget: this.controls.target.clone(), toTarget: center,
    };
    this._radius = radius;
  }

  setCameraDirection(dirVec) {
    const radius = this._radius || 5;
    const target = this.controls.target.clone();
    this.camTween = {
      t0: performance.now(), dur: 700,
      fromPos: this.camera.position.clone(),
      toPos: target.clone().add(dirVec.clone().normalize().multiplyScalar(radius * 3.2)),
      fromTarget: target.clone(), toTarget: target.clone(),
    };
  }

  setGrid(box) {
    if (this.grid) {
      this.scene.remove(this.grid);
      this.grid.geometry.dispose();
      this.grid.material.dispose();
    }
    const size = Math.max(box.getSize(new THREE.Vector3()).length() * 1.6, 1);
    const divisions = 24;
    this.grid = new THREE.GridHelper(size, divisions, 0x3a4a6e, 0x22304c);
    this.grid.position.y = box.min.y - size * 0.002;
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.35;
    this.scene.add(this.grid);
  }

  // -- meshes ---------------------------------------------------------------
  makeMaterial(kind) {
    if (kind === 'ghost') {
      return new THREE.MeshStandardMaterial({
        color: 0x38bdf8, transparent: true, opacity: 0.15,
        depthWrite: false, roughness: 0.6, metalness: 0.1,
        side: THREE.DoubleSide,
      });
    }
    return new THREE.MeshStandardMaterial({
      color: 0xc7d2e4, roughness: 0.42, metalness: 0.22,
      envMapIntensity: 1.1,
    });
  }

  setReference(object) {
    if (this.reference) {
      this.scene.remove(this.reference);
      disposeTree(this.reference);
    }
    this.reference = object;
    object.traverse((o) => {
      if (o.isMesh) o.material = this.makeMaterial('ghost');
    });
    this.scene.add(object);
    object.visible = this.referenceVisible && !this.current;
  }

  setReferenceVisible(visible) {
    this.referenceVisible = visible;
    if (this.reference) this.reference.visible = visible || !this.current;
  }

  setSolid(object) {
    object.traverse((o) => {
      if (o.isMesh) o.material = this.makeMaterial('solid');
    });
    this.scene.add(object);
    return object;
  }

  transitionTo(newObject, meta) {
    const isReference = !meta.generated;
    if (isReference) {
      this.setReference(newObject);
    } else {
      this.setSolid(newObject);
      if (this.reference) this.reference.visible = this.referenceVisible;
    }

    const box = this.boxOf(newObject);
    this.setGrid(box);
    this.frameTo(box);

    const previous = this.current;
    this.current = newObject;

    if (!meta.generated) {
      // First load: just fade the object in with a gentle sparkle burst.
      fadeTree(newObject, 0);
      this.transition = {
        t0: performance.now(), dur: 1000, burst: true,
        oldObj: null, newObj: newObject,
        oldBase: 1, newFinal: 1,
      };
      buildParticles(this.scene, null, newObject, box, this.transition);
      return;
    }

    const oldBase = previous && previous.visible ? 1 : 0;
    if (previous && previous !== this.reference) fadeTree(previous, oldBase);
    fadeTree(newObject, 0);
    this.transition = {
      t0: performance.now(), dur: 1300, burst: false,
      oldObj: previous && previous !== this.reference ? previous : null,
      newObj: newObject, oldBase, newFinal: 1,
    };
    buildParticles(this.scene, this.transition.oldObj, newObject, box, this.transition);
    if (previous && previous !== this.reference && !previous.userData.ghost) {
      previous.userData.ghost = true;
    }
  }

  update(now) {
    // camera tween
    if (this.camTween) {
      const p = Math.min(1, (now - this.camTween.t0) / this.camTween.dur);
      const e = p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2;
      this.camera.position.lerpVectors(this.camTween.fromPos, this.camTween.toPos, e);
      this.controls.target.lerpVectors(this.camTween.fromTarget, this.camTween.toTarget, e);
      if (p >= 1) this.camTween = null;
    }

    // particle transition
    const tr = this.transition;
    if (tr) {
      const p = Math.min(1, (now - tr.t0) / tr.dur);
      if (tr.points) tr.points.material.uniforms.uProgress.value = p;
      if (tr.oldObj) {
        const fade = 1 - smoothstep(0, 0.35, p);
        fadeTree(tr.oldObj, tr.oldBase * fade);
      }
      fadeTree(tr.newObj, tr.newFinal * smoothstep(0.45, 1, p));
      if (p >= 1) {
        if (tr.oldObj) {
          this.scene.remove(tr.oldObj);
          disposeTree(tr.oldObj);
        }
        if (tr.points) {
          this.scene.remove(tr.points);
          tr.points.geometry.dispose();
          tr.points.material.dispose();
        }
        fadeTree(tr.newObj, tr.newFinal);
        tr.newObj.traverse((o) => {
          if (o.isMesh) { o.material.transparent = false; o.material.opacity = 1; }
        });
        this.transition = null;
      }
    }

    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}

function smoothstep(a, b, x) {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
}

function fadeTree(object, opacity) {
  object.traverse((o) => {
    if (o.isMesh) {
      o.material.transparent = opacity < 1;
      o.material.opacity = opacity;
      o.visible = opacity > 0.01;
    }
  });
}

function disposeTree(object) {
  object.traverse((o) => {
    if (o.isMesh) {
      o.geometry.dispose();
      if (o.material.map) o.material.map.dispose();
      o.material.dispose();
    }
  });
}

// ------------------------------------------------------------- particles ---

function collectTriangles(object) {
  // World-space triangles: [{base6 matrix, geometry}] flattened into one list
  // of vertices per triangle, weighted by area.
  object.updateWorldMatrix(true, true);
  const meshes = [];
  object.traverse((o) => {
    if (o.isMesh && o.geometry && o.geometry.attributes.position) {
      meshes.push({ geometry: o.geometry, matrix: o.matrixWorld.clone() });
    }
  });
  return meshes;
}

function sampleSurface(object, count) {
  const meshes = collectTriangles(object);
  const out = new Float32Array(count * 3);
  if (!meshes.length) return out;

  // Build a cumulative triangle-area table per mesh for area-weighted picks.
  const tables = [];
  let totalArea = 0;
  for (const { geometry, matrix } of meshes) {
    const pos = geometry.attributes.position;
    const index = geometry.index;
    const triCount = index ? index.count / 3 : pos.count / 3;
    const cum = new Float64Array(triCount);
    const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
    for (let t = 0; t < triCount; t++) {
      const i0 = index ? index.getX(t * 3) : t * 3;
      const i1 = index ? index.getX(t * 3 + 1) : t * 3 + 1;
      const i2 = index ? index.getX(t * 3 + 2) : t * 3 + 2;
      a.fromBufferAttribute(pos, i0);
      b.fromBufferAttribute(pos, i1);
      c.fromBufferAttribute(pos, i2);
      const area = b.sub(a).cross(c.sub(a)).length() * 0.5;
      totalArea += area;
      cum[t] = totalArea;
    }
    tables.push({ geometry, matrix, cum, triCount, pos, index, offset: totalArea });
  }

  const v = new THREE.Vector3();
  for (let i = 0; i < count; i++) {
    const r = Math.random() * totalArea;
    // binary search across mesh offsets, then triangle cdf
    let lo = 0, hi = tables.length - 1, table = tables[0];
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (tables[mid].offset < r) lo = mid + 1; else hi = mid;
    }
    table = tables[lo];
    let tlo = 0, thi = table.triCount - 1;
    while (tlo < thi) {
      const mid = (tlo + thi) >> 1;
      if (table.cum[mid] < r) tlo = mid + 1; else thi = mid;
    }
    const t = tlo;
    const { pos, index, matrix } = table;
    const i0 = index ? index.getX(t * 3) : t * 3;
    const i1 = index ? index.getX(t * 3 + 1) : t * 3 + 1;
    const i2 = index ? index.getX(t * 3 + 2) : t * 3 + 2;
    let u = Math.random(), w = Math.random();
    if (u + w > 1) { u = 1 - u; w = 1 - w; }
    const p0 = new THREE.Vector3().fromBufferAttribute(pos, i0);
    const p1 = new THREE.Vector3().fromBufferAttribute(pos, i1);
    const p2 = new THREE.Vector3().fromBufferAttribute(pos, i2);
    v.copy(p0).multiplyScalar(1 - u - w)
      .addScaledVector(p1, u)
      .addScaledVector(p2, w)
      .applyMatrix4(matrix);
    out[i * 3] = v.x; out[i * 3 + 1] = v.y; out[i * 3 + 2] = v.z;
  }
  return out;
}

function buildParticles(scene, oldObj, newObj, box, transition) {
  const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);
  const triBudget = Math.min(24000, Math.max(5000, radius * 800));
  const count = Math.round(triBudget);

  const ends = sampleSurface(newObj, count);
  let starts;
  if (oldObj) {
    starts = sampleSurface(oldObj, count);
  } else {
    // Burst mode: explode outward from the new surface.
    starts = new Float32Array(ends.length);
    for (let i = 0; i < count; i++) {
      const theta = Math.random() * Math.PI * 2;
      const phi = Math.acos(2 * Math.random() - 1);
      const r = radius * (0.25 + Math.random() * 0.45);
      starts[i * 3] = ends[i * 3] + r * Math.sin(phi) * Math.cos(theta);
      starts[i * 3 + 1] = ends[i * 3 + 1] + r * Math.cos(phi);
      starts[i * 3 + 2] = ends[i * 3 + 2] + r * Math.sin(phi) * Math.sin(theta);
    }
  }

  const rands = new Float32Array(count * 3);
  for (let i = 0; i < count * 3; i++) rands[i] = Math.random();

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(starts, 3));
  geometry.setAttribute('aStart', new THREE.BufferAttribute(starts, 3));
  geometry.setAttribute('aEnd', new THREE.BufferAttribute(ends, 3));
  geometry.setAttribute('aRand', new THREE.BufferAttribute(rands, 3));

  const material = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uProgress: { value: 0 },
      uWobble: { value: radius * 0.12 },
      uPixelRatio: { value: Math.min(window.devicePixelRatio, 2) },
      uColorA: { value: new THREE.Color(0x6ee7ff) },
      uColorB: { value: new THREE.Color(0x818cf8) },
      uOpacity: { value: 0.85 },
    },
    vertexShader: /* glsl */`
      attribute vec3 aStart;
      attribute vec3 aEnd;
      attribute vec3 aRand;
      uniform float uProgress;
      uniform float uWobble;
      uniform float uPixelRatio;
      varying float vT;
      varying float vSeed;

      float easeInOutCubic(float t) {
        return t < 0.5 ? 4.0 * t * t * t : 1.0 - pow(-2.0 * t + 2.0, 3.0) / 2.0;
      }

      void main() {
        float delay = aRand.x * 0.5;
        float t = clamp((uProgress - delay) / 0.5, 0.0, 1.0);
        float e = easeInOutCubic(t);
        vec3 pos = mix(aStart, aEnd, e);
        float w = sin(3.14159265 * t);
        pos += w * uWobble * vec3(
          sin(aRand.y * 6.2831 + uProgress * 5.0),
          cos(aRand.z * 6.2831 + uProgress * 4.0),
          sin(aRand.x * 6.2831 + uProgress * 6.0));
        vT = t;
        vSeed = aRand.y;
        vec4 mv = modelViewMatrix * vec4(pos, 1.0);
        gl_PointSize = (2.2 + 2.8 * aRand.z) * uPixelRatio * (150.0 / max(1.0, -mv.z));
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */`
      uniform vec3 uColorA;
      uniform vec3 uColorB;
      uniform float uOpacity;
      varying float vT;
      varying float vSeed;

      void main() {
        vec2 uv = gl_PointCoord - 0.5;
        float d = length(uv);
        if (d > 0.5) discard;
        float glow = smoothstep(0.5, 0.0, d);
        float alpha = glow * sin(3.14159265 * vT) * uOpacity;
        gl_FragColor = vec4(mix(uColorA, uColorB, vSeed), alpha);
      }`,
  });

  const points = new THREE.Points(geometry, material);
  points.frustumCulled = false;
  scene.add(points);
  transition.points = points;
}

// ------------------------------------------------------------------ feed ---

function feedNearBottom() {
  return dom.feed.scrollHeight - dom.feed.scrollTop - dom.feed.clientHeight < 90;
}

function appendEntry(node) {
  const empty = dom.feed.querySelector('.feed-empty');
  if (empty) empty.remove();
  dom.feed.appendChild(node);
  state.events += 1;
  dom.feedCount.textContent = `${state.events} events`;
  if (state.feedStick) dom.feed.scrollTop = dom.feed.scrollHeight;
}

function agentEntry(text) {
  const node = el('div', 'entry agent');
  node.appendChild(el('div', 'who', 'Aida'));
  node.appendChild(document.createTextNode(text));
  return node;
}

function codeEntry(evt) {
  const node = el('div', 'entry code');
  const head = el('div', 'code-head');
  head.appendChild(el('span', null, `build123d · snippet ${evt.index}`));
  const copy = el('button', 'copy-btn', 'copy');
  copy.addEventListener('click', () => {
    navigator.clipboard.writeText(evt.code).then(() => {
      copy.textContent = 'copied';
      setTimeout(() => { copy.textContent = 'copy'; }, 1200);
    });
  });
  head.appendChild(copy);
  node.appendChild(head);
  const pre = el('pre');
  pre.appendChild(el('code', null, evt.code));
  node.appendChild(pre);
  return node;
}

function toolEntry(evt) {
  const node = el('div', 'entry tool');
  node.appendChild(el('span', 'tool-name', evt.name));
  const args = evt.args || {};
  let brief = '';
  if (evt.name === 'execute') brief = 'python';
  else if (args.objects) brief = args.objects;
  else if (args.object_name) brief = args.object_name;
  else if (args.direction) brief = args.direction;
  else if (args.path) brief = String(args.path).split('/').pop();
  if (brief) node.appendChild(el('span', 'tool-arg', String(brief)));
  node.dataset.toolName = evt.name;
  return node;
}

function renderEntry(evt) {
  const node = el('div', 'entry render');
  const img = el('img');
  img.loading = 'lazy';
  img.src = evt.url;
  node.appendChild(img);
  node.appendChild(el('div', 'cap', 'render_view'));
  return node;
}

function meshEntry(evt) {
  const node = el('div', 'entry mesh');
  node.appendChild(el('span', 'rev', `rev ${evt.revision}`));
  const detail = el('div');
  detail.appendChild(el('div', null, evt.generated ? 'Generated mesh updated' : 'Reference mesh loaded'));
  detail.appendChild(el('div', 'dim',
    `${fmtNum(evt.triangles)} triangles · ${fmtExtents(evt.extents || [0, 0, 0])} units`));
  node.appendChild(detail);
  return node;
}

function handleEvent(evt) {
  switch (evt.type) {
    case 'agent_text':
      if (evt.text && evt.text.trim()) appendEntry(agentEntry(evt.text));
      break;
    case 'code':
      appendEntry(codeEntry(evt));
      break;
    case 'tool_call':
      appendEntry(toolEntry(evt));
      break;
    case 'tool_result': {
      if (!evt.ok) {
        appendEntry(errorEntry(`Tool ${evt.name} failed`, evt.text));
      } else if (evt.text && evt.text.trim()) {
        const prev = [...dom.feed.querySelectorAll('.entry.tool')].reverse()
          .find((n) => n.dataset.toolName === evt.name);
        const line = evt.text.trim().split('\n')[0].slice(0, 160);
        const node = el('div', 'entry result', line);
        if (prev && prev.nextSibling) dom.feed.insertBefore(node, prev.nextSibling);
        else appendEntry(node);
        if (state.feedStick) dom.feed.scrollTop = dom.feed.scrollHeight;
      }
      break;
    }
    case 'render':
      appendEntry(renderEntry(evt));
      break;
    case 'mesh':
      state.revisions = Math.max(state.revisions, evt.revision + 1);
      dom.revLabel.textContent = `revision ${evt.revision}`;
      appendEntry(meshEntry(evt));
      loadMesh(evt);
      break;
    case 'mesh_skipped':
      appendEntry(el('div', 'entry status', `mesh snapshot skipped — ${evt.reason}`));
      break;
    case 'error':
      appendEntry(errorEntry('Session error', evt.message));
      setPill('failed');
      break;
    case 'status':
      setPill(evt.status);
      // A failed status is always followed by a dedicated 'error' event,
      // which renders the detail — avoid logging it twice.
      updateSummary();
      break;
    case 'stage':
      setStage(evt.stage);
      break;
    case 'turn_end':
      appendEntry(el('div', 'entry status', evt.is_error ? 'turn stopped' : 'turn complete'));
      break;
    case 'session_done':
      appendEntry(el('div', 'entry status', 'session finished'));
      setComposerEnabled(false);
      break;
    default:
      break;
  }
}

function errorEntry(title, detail) {
  const node = el('div', 'entry error');
  node.appendChild(el('div', 'who', title));
  if (detail) node.appendChild(document.createTextNode(detail));
  return node;
}

// --------------------------------------------------------------- status ----

const STAGE_LABELS = {
  preparing: 'Preparing mesh',
  cad_starting: 'Starting CAD engine',
  agent_running: 'Generating',
  exporting_mesh: 'Rebuilding mesh',
  idle: 'Ready',
  done: 'Done',
  failed: 'Failed',
};

function setStage(stage) {
  state.stage = stage;
  const label = STAGE_LABELS[stage] || stage;
  if (state.status === 'running') dom.stagePill.textContent = label;
  if (['cad_starting', 'agent_running', 'exporting_mesh', 'preparing'].includes(stage)) {
    setViewportStatus(stage === 'exporting_mesh' ? 'Rebuilding mesh…'
      : stage === 'agent_running' ? 'Agent is modeling…'
      : `${label}…`);
  } else {
    setViewportStatus(null);
  }
}

function setPill(status) {
  state.status = status;
  dom.stagePill.className = 'pill';
  if (status === 'running') {
    dom.stagePill.classList.add('pill-running');
    dom.stagePill.textContent = STAGE_LABELS[state.stage] || 'Running';
  } else if (status === 'done') {
    dom.stagePill.classList.add('pill-done');
    dom.stagePill.textContent = 'Done';
    setViewportStatus(null);
  } else if (status === 'failed') {
    dom.stagePill.classList.add('pill-failed');
    dom.stagePill.textContent = 'Failed';
    setViewportStatus(null);
  } else {
    dom.stagePill.classList.add('pill-idle');
    dom.stagePill.textContent = 'Idle';
  }
}

function setViewportStatus(text) {
  if (!text) {
    dom.status.classList.add('hidden');
  } else {
    dom.statusText.textContent = text;
    dom.status.classList.remove('hidden');
  }
}

function updateSummary() {
  if (!state.sessionId) return;
  fetch(`/api/v1/cad-sessions/${state.sessionId}`)
    .then((r) => r.json())
    .then((data) => {
      dom.summaryBody.innerHTML = '';
      const rows = [
        ['Session', data.session_id],
        ['Status', `${data.status} · ${data.stage}`],
        ['Revisions', String(data.revisions)],
      ];
      for (const [k, v] of rows) {
        const row = el('div');
        row.appendChild(el('b', null, `${k}: `));
        row.appendChild(document.createTextNode(v));
        dom.summaryBody.appendChild(row);
      }
    })
    .catch(() => {});
}

// ----------------------------------------------------------- mesh loading --

async function loadMesh(evt) {
  setViewportStatus(evt.generated ? `Applying revision ${evt.revision}…` : 'Loading mesh…');
  try {
    const response = await fetch(evt.url);
    if (!response.ok) throw new Error(`mesh fetch failed (${response.status})`);
    const buffer = await response.arrayBuffer();
    const gltf = await loader.parseAsync(buffer, '');
    viewer.transitionTo(gltf.scene, evt);
    dom.empty.classList.add('hidden');
  } catch (err) {
    appendEntry(errorEntry('Mesh load failed', err.message));
  } finally {
    if (['idle', 'done', 'failed'].includes(state.stage)) setViewportStatus(null);
    updateSummary();
  }
}

// ------------------------------------------------------------------- SSE ---

function connect(sessionId) {
  if (state.source) state.source.close();
  state.lastSeq = 0;
  const source = new EventSource(`/api/v1/cad-sessions/${sessionId}/events`);
  source.onmessage = (message) => {
    const evt = JSON.parse(message.data);
    if (evt.seq <= state.lastSeq) return;   // replayed after reconnect
    state.lastSeq = evt.seq;
    handleEvent(evt);
  };
  source.onerror = () => {
    if (state.status === 'running') dom.stagePill.textContent = 'Reconnecting…';
  };
  state.source = source;
}

// ------------------------------------------------------------------- form --

function wireDrop(zone, input, onFiles) {
  zone.addEventListener('click', () => input.click());
  zone.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); input.click(); }
  });
  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    zone.classList.add('dragover');
  });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    if (e.dataTransfer.files.length) onFiles([...e.dataTransfer.files]);
  });
  input.addEventListener('change', () => {
    if (input.files.length) onFiles([...input.files]);
    input.value = '';
  });
}

function renderMeshChip() {
  dom.meshChip.innerHTML = '';
  if (!state.meshFile) return;
  dom.meshChip.classList.remove('hidden');
  dom.meshChip.textContent = `${state.meshFile.name} · ${fmtBytes(state.meshFile.size)}`;
}

function renderImageChips() {
  dom.imageChips.innerHTML = '';
  state.imageFiles.forEach((file, index) => {
    const chip = el('span', 'chip', file.name);
    const x = el('span', 'chip-x', '×');
    x.addEventListener('click', (e) => {
      e.stopPropagation();
      state.imageFiles.splice(index, 1);
      renderImageChips();
    });
    chip.appendChild(x);
    dom.imageChips.appendChild(chip);
  });
}

function setComposerEnabled(enabled) {
  dom.followup.disabled = !enabled;
  dom.sendBtn.disabled = !enabled;
}

function showSetupError(message) {
  if (!message) {
    dom.setupError.classList.add('hidden');
  } else {
    dom.setupError.textContent = message;
    dom.setupError.classList.remove('hidden');
  }
}

async function startSession() {
  showSetupError(null);
  dom.startBtn.disabled = true;
  dom.startBtn.textContent = 'Starting…';
  const form = new FormData();
  form.append('mesh', state.meshFile);
  form.append('prompt', dom.promptInput.value.trim());
  if (dom.unitsInput.value) form.append('units', dom.unitsInput.value);
  for (const file of state.imageFiles) form.append('images', file);

  try {
    const response = await fetch('/api/v1/cad-sessions', { method: 'POST', body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `request failed (${response.status})`);
    state.sessionId = data.session_id;
    dom.summary.classList.remove('hidden');
    dom.closeBtn.classList.remove('hidden');
    setComposerEnabled(true);
    setPill('running');
    updateSummary();
    connect(data.session_id);
  } catch (err) {
    showSetupError(err.message);
  } finally {
    dom.startBtn.textContent = 'Generate CAD';
    validateForm();
  }
}

function validateForm() {
  dom.startBtn.disabled = !state.meshFile || !dom.promptInput.value.trim();
}

// ------------------------------------------------------------- bootstrap ---

const viewer = new Viewer(dom.canvas);

function animate(now) {
  requestAnimationFrame(animate);
  if (document.hidden) return;
  viewer.update(now);
}
requestAnimationFrame(animate);

wireDrop(dom.meshDrop, dom.meshInput, (files) => {
  state.meshFile = files[0];
  renderMeshChip();
  validateForm();
});
wireDrop(dom.imageDrop, dom.imageInput, (files) => {
  state.imageFiles.push(...files);
  renderImageChips();
});

dom.promptInput.addEventListener('input', validateForm);
dom.startBtn.addEventListener('click', startSession);

// Acquisition jobs can open an already-started CAD session in this viewer.
const existingSession = new URLSearchParams(window.location.search).get('session');
if (existingSession && /^[a-zA-Z0-9_-]+$/.test(existingSession)) {
  fetch(`/api/v1/cad-sessions/${existingSession}`).then(async response => {
    if (!response.ok) throw new Error('CAD session no longer exists in this server.');
    const data = await response.json();
    state.sessionId = existingSession;
    dom.summary.classList.remove('hidden');
    dom.closeBtn.classList.remove('hidden');
    setComposerEnabled(true);
    state.status = data.status;
    connect(existingSession);
    updateSummary();
  }).catch(error => appendEntry(errorEntry('Cannot open session', error.message)));
}

dom.closeBtn.addEventListener('click', async () => {
  if (!state.sessionId) return;
  await fetch(`/api/v1/cad-sessions/${state.sessionId}/close`, { method: 'POST' });
  setComposerEnabled(false);
});

dom.composer.addEventListener('submit', async (e) => {
  e.preventDefault();
  const prompt = dom.followup.value.trim();
  if (!prompt || !state.sessionId) return;
  dom.followup.value = '';
  await fetch(`/api/v1/cad-sessions/${state.sessionId}/turns`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt }),
  });
  setPill('running');
});

dom.followup.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    dom.composer.requestSubmit();
  }
});

dom.feed.addEventListener('scroll', () => {
  state.feedStick = feedNearBottom();
});

document.querySelectorAll('[data-view]').forEach((button) => {
  button.addEventListener('click', () => {
    const dirs = {
      iso: new THREE.Vector3(1, 0.85, 1),
      top: new THREE.Vector3(0, 1, 0.0001),
      front: new THREE.Vector3(0, 0.0001, 1),
      side: new THREE.Vector3(1, 0.0001, 0),
    };
    viewer.setCameraDirection(dirs[button.dataset.view]);
  });
});

dom.toggleReference.addEventListener('click', () => {
  viewer.setReferenceVisible(!viewer.referenceVisible);
  dom.toggleReference.classList.toggle('active', viewer.referenceVisible);
});

dom.toggleRotate.addEventListener('click', () => {
  viewer.controls.autoRotate = !viewer.controls.autoRotate;
  dom.toggleRotate.classList.toggle('active', viewer.controls.autoRotate);
});
dom.toggleRotate.classList.add('active');
viewer.controls.autoRotate = true;
dom.toggleReference.classList.add('active');
