import { after, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'vite';
import { fileURLToPath } from 'node:url';

const server = await createServer({ configFile: false, root: fileURLToPath(new URL('..', import.meta.url)),
  logLevel: 'silent', server: { middlewareMode: true, hmr: false }, appType: 'custom' });
after(() => server.close());
const { useStore, commitHistory, selectedRevision, activeFea, canShowGeometry, referenceRevision } = await server.ssrLoadModule('/src/store.ts');
const initial = useStore.getState();
const mesh = (revision) => ({ type: 'mesh', seq: revision+1, at: revision+1, generated: true,
  revision, triangles: 12, extents: [10,10,10], url: `/mesh-${revision}` });
const commit = (number, revision) => ({ type: 'commit', seq: number+10, at: number+10, number,
  message: `Checkpoint ${number}`, geometry_id: revision, steps: [], geometry: revision === null ? null :
    { revision, triangles: 12, extents: [10,10,10] } });
beforeEach(() => {
  useStore.setState({ ...initial, sessionId: 'test-workspace' }, true);
  useStore.getState().apply([mesh(0), mesh(1), commit(1,0), commit(2,1),
    { type: 'working_state', seq: 20, at: 20, current_commit: 2, dirty: false, geometry_id: 1 }]);
});

test('slider and panel share commit selection and immutable preview geometry', () => {
  assert.equal(commitHistory(useStore.getState()).length, 2);
  useStore.getState().selectCommit(1);
  const state = useStore.getState();
  assert.equal(state.selectedCommit, 1);
  assert.equal(state.shownRevision, 0);
  assert.equal(selectedRevision(state).url, '/api/v1/workspaces/test-workspace/commits/1/mesh.glb');
  assert.equal(selectedRevision(state), selectedRevision(state), 'React selectors must return stable object references');
});

test('new live geometry does not move an explicit commit selection', () => {
  useStore.getState().selectCommit(1);
  useStore.getState().apply([mesh(2), { type: 'working_state', seq: 21, at: 21, current_commit: 2, dirty: true, geometry_id: 2 }]);
  assert.equal(selectedRevision(useStore.getState()).revision, 0);
  useStore.getState().selectCommit(null);
  assert.equal(selectedRevision(useStore.getState()).revision, 2);
  assert.equal(useStore.getState().dirty, true);
});

test('restoring an earlier checkpoint retains later history', () => {
  useStore.getState().apply([{ type: 'commit_restored', seq: 22, at: 22, number: 1 },
    { type: 'working_state', seq: 23, at: 23, current_commit: 1, dirty: false, geometry_id: 0 }]);
  assert.equal(useStore.getState().currentCommit, 1);
  assert.deepEqual(commitHistory(useStore.getState()).map(c => c.number), [1,2]);
  assert.equal(selectedRevision(useStore.getState()).revision, 0);
});

test('FEA results only apply to their exact geometry', () => {
  const result = { revision: 0 };
  useStore.setState({ feaRuns: [{ result }] });
  assert.equal(activeFea(useStore.getState()), null);
  useStore.getState().selectCommit(1);
  assert.equal(activeFea(useStore.getState()), result);
});

test('parameter-only commits show no stale geometry', () => {
  useStore.getState().apply([commit(3, null)]);
  useStore.getState().selectCommit(3);
  assert.equal(selectedRevision(useStore.getState()), undefined);
});


test('selecting a commit clears an incompatible FEA overlay', () => {
  useStore.setState({ feaRuns: [{ result: { revision: 1 } }], viewMode: 'fea-stress' });
  useStore.getState().selectCommit(1);
  assert.equal(useStore.getState().viewMode, 'overlay');
});


test('historical uncommitted geometry does not become another history entry', () => {
  useStore.getState().apply([mesh(2), mesh(3)]);
  assert.equal(canShowGeometry(useStore.getState(), 2), false);
  assert.equal(canShowGeometry(useStore.getState(), 3), true);
  assert.equal(commitHistory(useStore.getState()).length, 2);
});


test('new scan reference scenes replace the displayed reference and update metric units', () => {
  const old = { ...mesh(10), generated: false, parts: ['old'] };
  const latest = { ...mesh(11), generated: false, parts: ['old','scanned_object'] };
  useStore.getState().apply([old, latest, { type: 'workspace_units', units: 'mm', seq: 30, at: 30 }]);
  assert.equal(referenceRevision(useStore.getState().revisions).revision, 11);
  assert.deepEqual(referenceRevision(useStore.getState().revisions).parts, ['old','scanned_object']);
  assert.equal(useStore.getState().sessionUnits, 'mm');
});
