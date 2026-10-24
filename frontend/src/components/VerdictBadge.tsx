import type { Deviation } from '../lib/events';

const TONES = {
  ok: 'bg-ok/16 text-ok',
  cad: 'bg-cad/16 text-cad',
  error: 'bg-error/16 text-error',
  muted: 'bg-muted/16 text-muted',
} as const;
const DOTS = { ok: 'bg-ok', cad: 'bg-cad', error: 'bg-error', muted: 'bg-muted' } as const;

/** Figma: VerdictBadge (2:122). Within threshold / Improving / Worse. */
export function VerdictBadge({ deviation }: { deviation: Deviation }) {
  let tone: keyof typeof TONES = 'muted';
  let label = deviation.verdict.replaceAll('_', ' ');
  if (deviation.verdict === 'within_threshold') {
    tone = 'ok';
    label = 'Within threshold';
  } else if (deviation.trend === 'better') {
    tone = 'cad';
    label = 'Improving';
  } else if (deviation.trend === 'worse') {
    tone = 'error';
    label = 'Worse';
  }
  return (
    <span className={`flex shrink-0 items-center gap-2 rounded-lg px-3 py-1 text-[11px] font-semibold ${TONES[tone]}`}>
      <span className={`size-1.5 rounded-full ${DOTS[tone]}`} />
      {label}
    </span>
  );
}
