// Isolated UI fixture: no requests reach a real workspace.
import React from 'react';
import { createRoot } from 'react-dom/client';
import { TopBar } from '../src/components/TopBar';
import { StepsPanel } from '../src/components/StepsPanel';
import { FollowControl } from '../src/components/viewer/Controls';
import { Home } from '../src/components/Home';
import { useStore } from '../src/store';
import '../src/styles.css';

const workspaces = [{ id: 'fixture', name: 'Test bracket', named: true, prompt: 'Build a bracket', parts: ['bracket'],
  commits: 2, created_at: Date.now()/1000, updated_at: Date.now()/1000, thumbnail: null, status: 'closed', empty: false }];
window.fetch = async (input, init) => {
  const url = String(input);
  if (init?.method === 'DELETE') { workspaces.splice(0); return Response.json({ deleted: 'fixture' }); }
  if (init?.method === 'PATCH') return Response.json({ ...workspaces[0], ...JSON.parse(String(init.body)) });
  if (url.includes('/replays')) return Response.json([]);
  if (url === '/api/v1/workspaces') return Response.json(workspaces);
  if (url.endsWith('/restore')) return Response.json({ restored: 1, notes: [] });
  return Response.json({});
};
const base = Date.now()/1000;
const mesh = (revision: number) => ({ type: 'mesh' as const, seq: revision + 1, at: base, revision,
  generated: true, url: `/geometry-${revision}`, triangles: 12, extents: [10,10,10] });
const tool = (seq: number) => ({ type: 'tool_call' as const, seq, at: base + seq, name: 'model_add_feature', args: { name: 'Body' } });
const result = (seq: number) => ({ type: 'tool_result' as const, seq: seq+20, at: base+seq+0.5, name: 'model_add_feature', ok: true, text: 'Built body' });
const commit = (number: number, geometry: number, events: ReturnType<typeof tool>[]) => ({ type: 'commit' as const,
  seq: 10+number, at: base+10+number, number, message: number === 1 ? 'Build bracket body' : 'Add mounting holes',
  geometry_id: geometry, geometry: { revision: geometry, triangles: 12, extents: [10,10,10] }, steps: events });
useStore.setState({ sessionId: 'fixture', status: 'running', stage: 'idle', stepsOpen: true,
  workspaceName: 'A very long bracket workspace title that should stay bounded while editing', workspaceNamed: true });
useStore.getState().apply([mesh(0), tool(2), result(2), commit(1,0,[tool(2)]), mesh(1), tool(3), result(3), commit(2,1,[tool(3)]),
  { type: 'working_state', seq: 15, at: base+15, current_commit: 2, dirty: false, geometry_id: 1 }]);
const root = createRoot(document.getElementById('root')!);
root.render(new URLSearchParams(location.search).has('home') ? <Home /> :
  <div className="flex h-screen flex-col"><TopBar /><div className="flex min-h-0 flex-1">
    <main className="flex flex-1 items-start justify-between p-4"><FollowControl />
      <button onClick={() => useStore.getState().apply([mesh(2), { type: 'working_state', seq: 16, at: base+16,
        current_commit: 2, dirty: true, geometry_id: 2 }])}>Simulate live edit</button></main><StepsPanel />
  </div></div>);
