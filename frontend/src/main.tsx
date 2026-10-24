import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { useStore } from './store';
import './styles.css';

// Development only: lets the console (and UI checks) inspect and drive the store.
if (import.meta.env.DEV) (window as unknown as { aida: typeof useStore }).aida = useStore;

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
