import { useEffect } from 'react';
import { LeftPanel } from './components/LeftPanel';
import { StepsPanel } from './components/StepsPanel';
import { TopBar } from './components/TopBar';
import { Viewer } from './components/viewer/Viewer';
import { closeSession } from './lib/api';
import { useStore } from './store';

/** The workspace: explorer on the left, the 3D view with the agent dock, and the agent's steps on the right. */
export default function App() {
  // This screen is a workspace: opening it starts a session (CAD engine + agent).
  // ?replay=<id> opens a recorded run instead (for demos and the video).
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('replay');
    if (id) void useStore.getState().startReplay(id);
    else void useStore.getState().openWorkspace();
    const close = () => {
      const { sessionId, replay } = useStore.getState();
      if (sessionId && !replay) closeSession(sessionId);
    };
    window.addEventListener('pagehide', close);
    return () => window.removeEventListener('pagehide', close);
  }, []);
  return (
    <div className="flex h-full flex-col bg-canvas">
      <TopBar />
      <main className="flex min-h-0 flex-1">
        <LeftPanel />
        <Viewer />
        <StepsPanel />
      </main>
    </div>
  );
}
