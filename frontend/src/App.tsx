import { AlertCircle } from 'lucide-react';
import { useEffect } from 'react';
import { Home } from './components/Home';
import { LeftPanel } from './components/LeftPanel';
import { StepsPanel } from './components/StepsPanel';
import { Logo, TopBar } from './components/TopBar';
import { Button } from './components/ui';
import { Viewer } from './components/viewer/Viewer';
import { linkProps, useRoute } from './lib/router';
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
  return <Workspace retry={() => {
    const id = route.page === 'workspace' ? route.id : null;
    if (id) void useStore.getState().openWorkspace(id);
    else if (route.page === 'replay') void useStore.getState().startReplay(route.id);
  }} />;
}

/** The workspace: explorer on the left, the 3D view with the agent dock, and the agent's steps on the right. */
function Workspace({ retry }: { retry: () => void }) {
  const name = useStore((s) => (s.replay ? 'Recorded run' : s.workspaceName));
  // Nothing could be loaded at all (a deleted workspace, a bad link, the server is down).
  const unreachable = useStore((s) => (s.status === 'failed' || (s.error && !s.replay && s.status === 'idle'))
    && s.feed.length === 0 && s.revisions.length === 0 ? s.error : null);
  useEffect(() => {
    document.title = name ? `${name} · Aida` : 'Aida';
  }, [name]);
  if (unreachable) return <Unreachable message={unreachable} retry={retry} />;
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

function Unreachable({ message, retry }: { message: string; retry: () => void }) {
  const missing = message.includes('no longer exists');
  return (
    <div className="flex h-full flex-col bg-canvas">
      <header className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-surface px-4">
        <a {...linkProps('/')} className="flex items-center gap-2" aria-label="All workspaces">
          <Logo />
          <span className="text-[14px] font-semibold tracking-[-0.01em]">Aida</span>
        </a>
      </header>
      <main className="flex flex-1 items-center justify-center p-6">
        <div role="alert" className="flex max-w-[420px] flex-col items-center gap-3 text-center">
          <AlertCircle size={22} strokeWidth={1.75} className="text-error" aria-hidden />
          <h1 className="text-[16px] font-semibold">{missing ? 'Workspace not found' : 'Could not open this workspace'}</h1>
          <p className="text-[13px] leading-[1.5] text-muted">
            {missing ? 'It may have been deleted, or the link is wrong.' : message}
          </p>
          <div className="flex gap-2 pt-1">
            {!missing && <Button onClick={retry}>Try again</Button>}
            <a {...linkProps('/')} className="inline-flex h-8 items-center rounded-md bg-accent-ink px-3 text-[13px] font-medium text-white hover:bg-accent-ink-hover">
              All workspaces
            </a>
          </div>
        </div>
      </main>
    </div>
  );
}
