import { useEffect } from 'react';
import { RightPanel } from './components/RightPanel';
import { TopBar } from './components/TopBar';
import { Viewer } from './components/viewer/Viewer';
import { useStore } from './store';

/** Figma: Screen / 1 Empty, 2 Running, 3 Model (file "Aida CAD Studio UI"). */
export default function App() {
  // ?replay=<id> opens straight into a recorded run (for demos and the video).
  useEffect(() => {
    const id = new URLSearchParams(window.location.search).get('replay');
    if (id) void useStore.getState().startReplay(id);
  }, []);
  return (
    <div className="flex h-full flex-col bg-base">
      <TopBar />
      <main className="flex min-h-0 flex-1">
        <Viewer />
        <RightPanel />
      </main>
    </div>
  );
}
