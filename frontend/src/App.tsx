import { RightPanel } from './components/RightPanel';
import { TopBar } from './components/TopBar';
import { Viewer } from './components/viewer/Viewer';

/** Figma: Screen / 1 Empty, 2 Running, 3 Model (file "Aida CAD Studio UI"). */
export default function App() {
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
