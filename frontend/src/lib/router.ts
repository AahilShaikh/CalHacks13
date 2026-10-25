import { useSyncExternalStore, type MouseEvent } from 'react';

/** The app's pages: home, one workspace, or a recorded run. */
export type Route = { page: 'home' } | { page: 'workspace'; id: string } | { page: 'replay'; id: string };

export function parseRoute(location: Location = window.location): Route {
  const path = location.pathname.replace(/\/+$/, '');
  const workspace = path.match(/^\/w\/([\w.-]+)$/);
  if (workspace) return { page: 'workspace', id: decodeURIComponent(workspace[1]) };
  const replay = path.match(/^\/replay\/([\w.-]+)$/);
  if (replay) return { page: 'replay', id: decodeURIComponent(replay[1]) };
  // Older links: /?replay=<id>.
  const legacy = new URLSearchParams(location.search).get('replay');
  if (legacy) return { page: 'replay', id: legacy };
  return { page: 'home' };
}

const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());
window.addEventListener('popstate', notify);

export function navigate(path: string, { replace = false } = {}) {
  if (path === window.location.pathname + window.location.search) return;
  if (replace) window.history.replaceState(null, '', path);
  else window.history.pushState(null, '', path);
  notify();
}

export const workspacePath = (id: string) => `/w/${encodeURIComponent(id)}`;

let cached = { href: '', route: parseRoute() as Route };
function snapshot(): Route {
  // Same object until the URL changes, as useSyncExternalStore requires.
  if (cached.href !== window.location.href) cached = { href: window.location.href, route: parseRoute() };
  return cached.route;
}

export function useRoute(): Route {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    snapshot,
  );
}

/** For links: plain clicks navigate in the app; modified clicks open a new tab as usual. */
export function linkProps(path: string) {
  return {
    href: path,
    onClick: (e: MouseEvent) => {
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      navigate(path);
    },
  };
}
