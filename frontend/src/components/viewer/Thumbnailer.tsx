import { useThree } from '@react-three/fiber';
import { useEffect } from 'react';
import { putThumbnail } from '../../lib/api';
import { useStore } from '../../store';

const WIDTH = 480;
const HEIGHT = 300;
// Long enough for a new revision to load and the camera to settle; retried while the view is empty.
const DELAY_MS = 2500;
const ATTEMPTS = 4;

/**
 * Saves a small picture of the viewer as the workspace's thumbnail (home screen cards)
 * a moment after each new revision shows up. Live workspaces only; never replays.
 */
export function Thumbnailer() {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const camera = useThree((s) => s.camera);
  const sessionId = useStore((s) => s.sessionId);
  const replaying = useStore((s) => s.replay !== null);
  const revisions = useStore((s) => s.revisions.length);

  useEffect(() => {
    if (!sessionId || replaying || revisions === 0) return;
    let timer = 0;
    let attempt = 0;
    const capture = () => {
      attempt++;
      // Draw a fresh frame so the buffer holds the current view (the canvas renders on demand).
      gl.render(scene, camera);
      const canvas = frame(gl.domElement);
      if (!canvas) return;
      if (blank(canvas)) {
        if (attempt < ATTEMPTS) timer = window.setTimeout(capture, DELAY_MS);
        return;
      }
      canvas.toBlob((blob) => {
        if (blob && useStore.getState().sessionId === sessionId) putThumbnail(sessionId, blob).catch(() => {});
      }, 'image/png');
    };
    timer = window.setTimeout(capture, DELAY_MS);
    return () => clearTimeout(timer);
  }, [gl, scene, camera, sessionId, replaying, revisions]);

  return null;
}

/** The viewer cover-cropped into the card's aspect ratio. */
function frame(source: HTMLCanvasElement) {
  if (!source.width || !source.height) return null;
  const canvas = document.createElement('canvas');
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const context = canvas.getContext('2d');
  if (!context) return null;
  const scale = Math.max(WIDTH / source.width, HEIGHT / source.height);
  const w = source.width * scale;
  const h = source.height * scale;
  context.drawImage(source, (WIDTH - w) / 2, (HEIGHT - h) / 2, w, h);
  return canvas;
}

/** Only the background: nothing worth saving yet. */
function blank(canvas: HTMLCanvasElement) {
  const data = canvas.getContext('2d')!.getImageData(0, 0, WIDTH, HEIGHT).data;
  for (let i = 4; i < data.length; i += 4 * 37) {
    if (Math.abs(data[i] - data[0]) > 6 || Math.abs(data[i + 1] - data[1]) > 6 || Math.abs(data[i + 2] - data[2]) > 6) {
      return false;
    }
  }
  return true;
}
