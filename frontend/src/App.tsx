import { useEffect } from 'react';
import { Home } from './components/Home';
import { LeftPanel } from './components/LeftPanel';
import { StepsPanel } from './components/StepsPanel';
import { TopBar } from './components/TopBar';
import { Viewer } from './components/viewer/Viewer';
import { useRoute } from './lib/router';
import { useStore } from './store';

/** Pages: home (/), a workspace (/w/<id>), or a recorded run (/replay/<id>). */
export default function App() {
  const route = useRoute();

  useEffect(() => {
    const state = useStore.getState();
    if (route.page === 'workspace') {
      if (state.sessionId !== route.id || state.replay) void state.openWorkspace(route.id);
    } else if (route.page === 'replay') {
      if (state.replay?.id !== route.id) void state.startReplay(route.id);
    } else {
      // The workspace keeps running on the server and frees itself once nobody watches.
      state.leaveWorkspace();
    }
  }, [route]);

  if (route.page === 'home') return <Home />;
  return <Workspace />;
}

/** The workspace: explorer on the left, the 3D view with the agent dock, and the agent's steps on the right. */
function Workspace() {
  const name = useStore((s) => (s.replay ? 'Recorded run' : s.workspaceName));
  useEffect(() => {
    document.title = name ? `${name} · Aida` : 'Aida';
  }, [name]);
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
