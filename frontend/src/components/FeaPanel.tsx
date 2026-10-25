import { Check, ChevronDown } from 'lucide-react';
import { useEffect, useState } from 'react';
import { DEFAULT_FEA, FEA_STAGES, MATERIALS, feaBusy, governingSafety, meshChange, feaNumber as n, oppositeFace, type Face, type FeaJob, type FeaResult, type FeaSettings } from '../lib/fea';
import { activeFea, revisionNumber, selectedRevision, useStore } from '../store';
import { Spinner, cx } from './ui';

const FACES: Face[] = ['x-', 'x+', 'y-', 'y+', 'z-', 'z+'];
const INPUT = 'w-full rounded-sm border border-line-strong bg-subtle px-2 py-1.5 font-mono text-[12px] text-ink focus:border-accent focus:outline-none';
const SECTION = 'text-[11px] font-semibold uppercase tracking-[0.04em] text-faint';

/** The number shown for a CAD revision, matching the revision scrubber. */
function useRevisionNumber() {
  const revisions = useStore((s) => s.revisions);
  return (revision: number | undefined) => revisionNumber(revisions, revision);
}

/**
 * The structural test tab, top to bottom: a running test's progress, the result for the
 * revision in view, every run so far, and (folded once there is a result) a form to run a new test.
 */
export function FeaPanel() {
  const revision = useStore((s) => selectedRevision(s)?.revision);
  const sessionUnits = useStore((s) => s.sessionUnits);
  const replay = useStore((s) => s.replay);
  const job = useStore((s) => s.feaJob);
  const runs = useStore((s) => s.feaRuns);
  const starting = useStore((s) => s.feaStarting);
  const error = useStore((s) => s.feaError);
  const shownResult = useStore(activeFea);
  const label = useRevisionNumber();
  const busy = starting || feaBusy(job);
  // The result for the revision in view; else the latest run's, flagged as another revision.
  const result = shownResult ?? runs.at(-1)?.result ?? null;
  const ready = revision !== undefined && sessionUnits === 'mm' && !replay;

  return (
    <div role="tabpanel" aria-label="Structural test" className="scroll-thin flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-3 py-3 text-[12px]">
      <p className="leading-relaxed text-muted">
        Clamps one end of the part and pushes on the opposite end. Ask Aida to check a part, or run a test yourself below.
      </p>
      {busy && job && <Progress job={job} number={label(job.revision)} />}
      {error && !busy && <p role="alert" className="rounded-md border border-error/30 bg-error/10 p-2 text-error">{error}</p>}
      {result && <Results result={result} number={label(result.revision)} current={result.revision === revision} />}
      {runs.length > 1 && <Runs runs={runs} label={label} />}
      <NewTest ready={ready} busy={busy} revision={label(revision)} hasResult={Boolean(result)}
        reason={replay ? 'FEA is available in live sessions.' : revision === undefined ? 'Build a CAD model first.'
          : 'This workspace has no known units. Start one with mm units to get physical stress.'} />
    </div>
  );
}

/** Live stages of the running test. */
function Progress({ job, number }: { job: FeaJob; number: number | undefined }) {
  const stages = FEA_STAGES.filter((s) => s.id !== 'checking mesh' || job.settings?.check_mesh);
  const current = stages.findIndex((s) => s.id === job.stage);
  return (
    <section aria-live="polite" className="rounded-md border border-accent/30 bg-accent-soft/50 p-3">
      <div className="mb-2 flex items-center justify-between font-semibold text-accent-ink">
        <span>{job.source === 'agent' ? 'Aida is testing' : 'Testing'} rev {number}</span>
        {job.settings && <span className="font-mono text-[11px] font-normal">{n(job.settings.force_n)} N · {job.settings.load_direction.toUpperCase()}</span>}
      </div>
      <ol className="flex flex-col gap-1.5">
        {stages.map((stage, index) => {
          const done = index < current;
          const active = index === current;
          return (
            <li key={stage.id} className={cx('flex items-center gap-2', done || active ? 'text-ink' : 'text-faint')}>
              <span className="flex size-4 items-center justify-center">
                {done ? <Check size={13} strokeWidth={2.5} className="text-ok" aria-hidden />
                  : active ? <Spinner size={12} className="text-accent" /> : <span className="size-1.5 rounded-full bg-line-strong" />}
              </span>
              {stage.label}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Results({ result, number, current }: { result: FeaResult; number: number | undefined; current: boolean }) {
  const { value: safety, awayFromSupports } = governingSafety(result);
  const change = meshChange(result);
  const verdict = safety === null ? null : safety >= 2 ? { text: 'Passes', tone: 'text-ok bg-ok/10' }
    : safety >= 1 ? { text: 'Low margin', tone: 'text-warn bg-warn/10' } : { text: 'Yields', tone: 'text-error bg-error/10' };
  const show = (mode: 'fea-stress' | 'fea-displacement') => {
    const s = useStore.getState();
    s.showRevision(result.revision);
    s.setViewMode(mode);
    s.setFocus(null);
  };
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h3 className={SECTION}>Result · rev {number}</h3>
        {verdict && <span className={cx('rounded-full px-2 py-0.5 text-[11px] font-semibold', verdict.tone)}>{verdict.text}</span>}
      </div>
      {!current && <p className="rounded-sm bg-warn/10 p-2 text-warn">The viewer shows another revision. This result is for rev {number}.</p>}
      {result.setup_url && (
        <a href={result.setup_url} target="_blank" rel="noreferrer" title="Open the setup picture"
          className="block overflow-hidden rounded-md border border-line hover:border-accent">
          <img src={result.setup_url} alt={`Test setup: held at faces ${result.support_faces?.join(', ')}, loaded on faces ${result.load_faces?.join(', ')}`} className="w-full" />
        </a>
      )}
      <p className="text-[11px] text-muted">
        {n(result.settings.force_n)} N toward {result.settings.load_direction.toUpperCase()}
        {result.support_faces ? ` · held at face ${result.support_faces.join(', ')} · loaded on face ${result.load_faces?.join(', ')}` : ` · fixed ${result.settings.fixed_face.toUpperCase()}`}
        {' '}· E {n(result.settings.youngs_modulus_mpa)} MPa · yield {n(result.settings.yield_strength_mpa)} MPa
      </p>
      <div className="grid grid-cols-2 gap-2">
        <Metric label={awayFromSupports ? 'Safety factor (away from supports)' : 'Safety factor'} value={safety === null ? '—' : `${n(safety)}×`} tone={safety === null ? undefined : safety < 1 ? 'text-error' : safety < 2 ? 'text-warn' : 'text-ok'} />
        <Metric label={awayFromSupports ? 'Peak stress (away from supports)' : 'Peak stress'} value={`${n(awayFromSupports ? result.peak_away_from_supports_mpa! : result.max_von_mises_mpa)} MPa`} />
        <Metric label="Max deflection" value={`${n(result.max_displacement_mm)} mm`} />
        <Metric label="Mesh check" value={change === null ? 'not run' : `${n(change * 100)}% change`} tone={change === null ? 'text-muted' : change > 0.1 ? 'text-warn' : 'text-ok'} />
      </div>
      <div className="flex gap-2">
        <button type="button" onClick={() => show('fea-stress')} className="flex-1 rounded-sm border border-line-strong px-2 py-1.5 text-accent-ink hover:bg-subtle">View stress</button>
        <button type="button" onClick={() => show('fea-displacement')} className="flex-1 rounded-sm border border-line-strong px-2 py-1.5 text-accent-ink hover:bg-subtle">Animate deformation</button>
      </div>
      <details className="group rounded-md border border-line text-[11px] leading-relaxed text-muted">
        <summary className="flex cursor-pointer list-none items-center gap-1 px-2.5 py-2 font-medium text-ink">
          <ChevronDown size={13} className="-rotate-90 transition-transform group-open:rotate-0" aria-hidden />
          Details and assumptions
          <a href={result.report_url} download onClick={(e) => e.stopPropagation()} className="ml-auto font-normal text-accent-ink hover:underline">Report ↓</a>
        </summary>
        <div className="flex flex-col gap-2 border-t border-line px-2.5 py-2">
          <p>{result.elements.toLocaleString()} elements · {result.nodes.toLocaleString()} nodes · element size {n(result.mesh_size_mm)} mm · volume difference {n(result.volume_error_fraction * 100)}% · residual {result.relative_residual.toExponential(1)}</p>
          <ul className="list-disc space-y-1.5 pl-4">{result.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      </details>
    </section>
  );
}

/** Every finished run, so the design loop's progress is visible. Click a row to see it. */
function Runs({ runs, label }: { runs: FeaJob[]; label: (revision: number | undefined) => number | undefined }) {
  const shown = useStore((s) => selectedRevision(s)?.revision);
  const open = (revision: number) => {
    const s = useStore.getState();
    s.showRevision(revision);
    s.setViewMode('fea-stress');
  };
  return (
    <section className="flex flex-col gap-2">
      <h3 className={SECTION}>All runs</h3>
      <table className="w-full overflow-hidden rounded-md border border-line text-left text-[11.5px]">
        <thead className="bg-subtle text-[10px] text-muted">
          <tr><th className="px-2.5 py-1.5 font-normal">Rev</th><th className="py-1.5 font-normal">Safety</th><th className="py-1.5 font-normal">Peak MPa</th><th className="py-1.5 pr-2.5 font-normal">Defl. mm</th></tr>
        </thead>
        <tbody className="font-mono">
          {runs.map((run) => {
            const r = run.result!;
            const safety = governingSafety(r).value;
            return (
              <tr key={run.analysis_id} onClick={() => open(r.revision)} aria-selected={r.revision === shown}
                className={cx('cursor-pointer border-t border-line hover:bg-subtle', r.revision === shown && 'bg-accent-soft/60')}>
                <td className="px-2.5 py-1.5">{label(r.revision)}{run.source === 'agent' && <span className="ml-1 font-sans text-[10px] text-accent-ink">Aida</span>}</td>
                <td className={cx('py-1.5', safety === null ? '' : safety < 1 ? 'text-error' : safety < 2 ? 'text-warn' : 'text-ok')}>
                  {safety === null ? '—' : `${n(safety)}×`}
                </td>
                <td className="py-1.5">{n(r.max_von_mises_mpa)}</td>
                <td className="py-1.5 pr-2.5">{n(r.max_displacement_mm)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function NewTest({ ready, busy, revision, hasResult, reason }: {
  ready: boolean; busy: boolean; revision: number | undefined; hasResult: boolean; reason: string;
}) {
  const job = useStore((s) => s.feaJob);
  const runFea = useStore((s) => s.runFea);
  const [settings, setSettings] = useState<FeaSettings>(DEFAULT_FEA);
  const [open, setOpen] = useState(!hasResult);
  // Aida's runs fill the form with what it tested, so a manual re-run starts from there.
  useEffect(() => {
    if (job?.settings && job.source === 'agent') setSettings(job.settings);
  }, [job?.analysis_id]); // eslint-disable-line react-hooks/exhaustive-deps
  const update = <K extends keyof FeaSettings>(key: K, value: FeaSettings[K]) => setSettings((s) => ({ ...s, [key]: value }));
  const expanded = open || !hasResult;

  return (
    <section className="flex flex-col gap-3 border-t border-line pt-3">
      <button type="button" onClick={() => setOpen(!expanded)} aria-expanded={expanded} disabled={!hasResult}
        className="flex items-center gap-1 text-left disabled:cursor-default">
        <h3 className={SECTION}>Run a new test{revision === undefined ? '' : ` on rev ${revision}`}</h3>
        {hasResult && <ChevronDown size={13} className={cx('ml-auto text-faint transition-transform', !expanded && '-rotate-90')} aria-hidden />}
      </button>
      {!ready && <p className="rounded-md bg-warn/10 p-2 text-warn">{reason}</p>}
      {expanded && (
        <form onSubmit={(e) => { e.preventDefault(); void runFea(settings); }} className="flex flex-col gap-3">
          <fieldset disabled={busy} className="flex flex-col gap-3 disabled:opacity-60">
            <div className="rounded-md border border-line p-3">
              <div className="mb-2 flex items-center justify-between"><span className="font-semibold">Material</span><span className="text-[10px] text-muted">Typical values · editable</span></div>
              <div className="mb-3 flex flex-wrap gap-1.5">
                {MATERIALS.map(({ label, ...properties }) => {
                  const picked = properties.youngs_modulus_mpa === settings.youngs_modulus_mpa && properties.yield_strength_mpa === settings.yield_strength_mpa;
                  return <button key={label} type="button" aria-pressed={picked} onClick={() => setSettings((s) => ({ ...s, ...properties }))}
                    className={cx('rounded-sm border px-2 py-1', picked ? 'border-accent bg-accent-soft text-accent-ink' : 'border-line-strong text-muted hover:border-accent hover:text-accent-ink')}>{label}</button>;
                })}
              </div>
              <div className="grid grid-cols-2 gap-2">
                <NumberField label="Young’s modulus (MPa)" value={settings.youngs_modulus_mpa} min={1} max={1000000} onChange={(v) => update('youngs_modulus_mpa', v)} />
                <NumberField label="Poisson’s ratio" value={settings.poisson_ratio} min={0} max={0.45} onChange={(v) => update('poisson_ratio', v)} />
                <NumberField label="Yield strength (MPa)" value={settings.yield_strength_mpa} min={0.000001} max={100000} onChange={(v) => update('yield_strength_mpa', v)} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 rounded-md border border-line p-3">
              <span className="col-span-2 font-semibold">Load</span>
              <label className="flex flex-col gap-1 text-[11px] text-muted">Fixed end
                <select className={INPUT} value={settings.fixed_face} onChange={(e) => update('fixed_face', e.target.value as Face)}>
                  {FACES.map((f) => <option key={f} value={f}>{f.toUpperCase()} face</option>)}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-[11px] text-muted">Force direction
                <select className={INPUT} value={settings.load_direction} onChange={(e) => update('load_direction', e.target.value as Face)}>
                  {FACES.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
                </select>
              </label>
              <NumberField label="Total force (N)" value={settings.force_n} min={0.000001} max={1000000} onChange={(v) => update('force_n', v)} />
              <label className="flex flex-col gap-1 text-[11px] text-muted">Mesh
                <select className={INPUT} value={settings.resolution} onChange={(e) => update('resolution', Number(e.target.value) as FeaSettings['resolution'])}>
                  <option value={10}>Fast</option><option value={16}>Standard</option><option value={22}>Fine</option>
                </select>
              </label>
              {settings.support_faces?.length ? (
                <p className="col-span-2 mt-1 rounded-sm bg-accent-soft/60 p-2 text-[11px] leading-relaxed text-ink">
                  Using Aida's faces: held at face {settings.support_faces.join(', ')}, loaded on face {settings.load_faces?.join(', ')}.{' '}
                  <button type="button" className="text-accent-ink underline" onClick={() => setSettings((s) => ({ ...s, support_faces: null, load_faces: null }))}>
                    Use the {settings.fixed_face.toUpperCase()} / {oppositeFace(settings.fixed_face).toUpperCase()} ends instead
                  </button>
                </p>
              ) : (
                <p className="col-span-2 mt-1 text-[11px] leading-relaxed text-muted">
                  The {settings.fixed_face.toUpperCase()} end is clamped; the force acts on the {oppositeFace(settings.fixed_face).toUpperCase()} end. Both ends must be flat faces. For other shapes, ask Aida: it picks the real support and load faces.
                </p>
              )}
            </div>
          </fieldset>
          <button type="submit" disabled={!ready || busy}
            className="rounded-md bg-accent-ink px-3 py-2 font-semibold text-white hover:bg-accent-ink-hover disabled:cursor-not-allowed disabled:opacity-40">
            {busy ? `Testing rev ${revision}…` : `Run FEA${revision === undefined ? '' : ` on rev ${revision}`}`}
          </button>
        </form>
      )}
    </section>
  );
}

function NumberField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void }) {
  return <label className="flex flex-col gap-1 text-[11px] text-muted">{label}
    <input type="number" required step="any" min={min} max={max} value={Number.isNaN(value) ? '' : value}
      onChange={(e) => onChange(e.target.valueAsNumber)} className={INPUT} />
  </label>;
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return <div className="rounded-md border border-line p-2.5">
    <div className="text-[10px] text-muted">{label}</div><div className={cx('mt-1 font-mono text-[14px]', tone ?? 'text-ink')}>{value}</div>
  </div>;
}
