import { useEffect, useRef, useState } from 'react';
import { DEFAULT_FEA, feaNumber as n, oppositeFace, type Face, type FeaSettings } from '../lib/fea';
import { selectedRevision, useStore } from '../store';

const FACES: Face[] = ['x-', 'x+', 'y-', 'y+', 'z-', 'z+'];
const INPUT = 'w-full rounded-sm border border-line-strong bg-sunken px-2 py-1.5 font-mono text-[12px] text-ink focus:border-cad focus:outline-none';
const PRESETS = [
  { label: 'Aluminum', youngs_modulus_mpa: 69000, poisson_ratio: 0.33, yield_strength_mpa: 240 },
  { label: 'Steel', youngs_modulus_mpa: 210000, poisson_ratio: 0.3, yield_strength_mpa: 250 },
  { label: 'PLA', youngs_modulus_mpa: 3500, poisson_ratio: 0.35, yield_strength_mpa: 50 },
];

export function FeaPanel() {
  const revision = useStore((s) => selectedRevision(s)?.revision);
  const sessionUnits = useStore((s) => s.sessionUnits);
  const replay = useStore((s) => s.replay);
  const job = useStore((s) => s.feaJob);
  const starting = useStore((s) => s.feaStarting);
  const error = useStore((s) => s.feaError);
  const runFea = useStore((s) => s.runFea);
  const [settings, setSettings] = useState<FeaSettings>(DEFAULT_FEA);
  const busy = starting || job?.status === 'queued' || job?.status === 'running';
  const result = job?.result;
  const resultsRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (result) resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, [result?.analysis_id]);
  const ready = revision !== undefined && sessionUnits === 'mm' && !replay;
  const update = <K extends keyof FeaSettings>(key: K, value: FeaSettings[K]) => setSettings((s) => ({ ...s, [key]: value }));

  return (
    <div role="tabpanel" aria-label="FEA" className="scroll-thin flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pb-2 text-[12px]">
      <div>
        <h2 className="text-[14px] font-semibold">Structural test <span className="font-mono text-cad">{revision === undefined ? '' : `· rev ${revision}`}</span></h2>
        <p className="mt-1 leading-relaxed text-muted">Clamp one end and distribute a total force over the opposite end. Test the CAD revision currently shown.</p>
      </div>
      {!ready && <p className="rounded-md bg-warn/10 p-2 text-warn">{replay ? 'FEA is available in live CAD sessions.' : revision === undefined
        ? 'Build a CAD model to enable FEA.' : 'Start a session with known mm units to calculate physical stress.'}</p>}
      <form onSubmit={(e) => { e.preventDefault(); void runFea(settings); }} className="flex flex-col gap-3">
        <fieldset disabled={busy} className="flex flex-col gap-3 disabled:opacity-60">
          <div className="rounded-md border border-line bg-raised p-3">
            <div className="mb-2 flex items-center justify-between"><span className="font-semibold">Material</span><span className="text-[10px] text-muted">Assumed presets · editable</span></div>
            <div className="mb-3 flex gap-1.5">
              {PRESETS.map(({ label, ...properties }) => <button key={label} type="button" onClick={() => setSettings((s) => ({ ...s, ...properties }))}
                className="rounded-sm border border-line-strong px-2 py-1 text-muted hover:border-cad hover:text-cad">{label}</button>)}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <NumberField label="Young’s modulus (MPa)" value={settings.youngs_modulus_mpa} min={1} max={1000000} onChange={(v) => update('youngs_modulus_mpa', v)} />
              <NumberField label="Poisson’s ratio" value={settings.poisson_ratio} min={0} max={0.45} onChange={(v) => update('poisson_ratio', v)} />
              <NumberField label="Yield strength (MPa)" value={settings.yield_strength_mpa} min={0.000001} max={100000} onChange={(v) => update('yield_strength_mpa', v)} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2 rounded-md border border-line bg-raised p-3">
            <label className="col-span-2 flex flex-col gap-1 text-[11px] text-muted">Analysis solver
              <select className={INPUT} value={settings.engine} onChange={(e) => update('engine', e.target.value as FeaSettings['engine'])}>
                <option value="auto">Automatic · CAD mesh when available</option>
                <option value="ngsolve">CAD mesh · Netgen / NGSolve</option>
                <option value="voxel">Voxel mesh · quick approximation</option>
              </select>
            </label>
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
            <label className="flex flex-col gap-1 text-[11px] text-muted">Mesh resolution
              <select className={INPUT} value={settings.resolution} onChange={(e) => update('resolution', Number(e.target.value) as FeaSettings['resolution'])}>
                <option value={10}>Fast · 10 divisions</option><option value={16}>Standard · 16 divisions</option><option value={22}>Fine · 22 divisions</option>
              </select>
            </label>
            <p className="col-span-2 mt-1 text-[11px] text-muted">Load on {oppositeFace(settings.fixed_face).toUpperCase()} end. All three displacements fixed on {settings.fixed_face.toUpperCase()}. Divisions set the mesh size along the longest axis.</p>
          </div>
        </fieldset>
        <button type="submit" disabled={!ready || busy}
          className="rounded-md bg-cad px-3 py-2 font-semibold text-base hover:bg-cad/90 disabled:cursor-not-allowed disabled:opacity-40">
          {busy ? `${job?.status === 'queued' ? 'Queued' : 'Meshing & solving'} rev ${job?.revision ?? revision}…` : `Run FEA${revision === undefined ? '' : ` on rev ${revision}`}`}
        </button>
      </form>
      <p aria-live="polite" className="text-[11px] text-muted">{busy ? 'Running locally. You can keep working while the test finishes.' : '3D linear elasticity · N / mm / MPa'}</p>
      {error && <p role="alert" className="rounded-md border border-error/30 bg-error/10 p-2 text-error">{error}</p>}
      {result && <>
        <div ref={resultsRef} className="flex items-center justify-between border-t border-line pt-3">
          <h3 className="font-semibold">Results · rev {result.revision}</h3>
          <a href={result.report_url} download className="text-cad hover:underline">Download report ↓</a>
        </div>
        <p className="text-[11px] font-medium text-cad">{result.engine === 'ngsolve' ? 'Netgen / NGSolve · CAD mesh' : 'Voxel approximation'} · {result.element_type}</p>
        <p className="text-[11px] text-muted">{n(result.settings.force_n)} N toward {result.settings.load_direction.toUpperCase()} · E {n(result.settings.youngs_modulus_mpa)} MPa · ν {result.settings.poisson_ratio}</p>
        {result.revision !== revision && <p className="rounded-sm bg-warn/10 p-2 text-warn">These results are for rev {result.revision}. The viewer is showing another revision.</p>}
        <div className="grid grid-cols-2 gap-2">
          <Metric label="Peak von Mises" value={`${n(result.max_von_mises_mpa)} MPa`} />
          <Metric label="Max displacement" value={`${n(result.max_displacement_mm)} mm`} />
          <Metric label="Yield / peak stress" value={result.yield_ratio === null ? '—' : `${n(result.yield_ratio)}×`} warn={result.yield_ratio !== null && result.yield_ratio < 1} />
          <Metric label="Solid elements" value={result.elements.toLocaleString()} />
        </div>
        <div className="flex gap-2">
          {(['fea-stress', 'fea-displacement'] as const).map((mode) => <button key={mode} type="button"
            onClick={() => { const s = useStore.getState(); s.showRevision(result.revision); s.setViewMode(mode); s.setFollow(false); s.setFocus(null); }}
            className="flex-1 rounded-sm border border-line-strong px-2 py-1.5 text-cad hover:bg-raised">
            {mode === 'fea-stress' ? 'View stress' : 'View deformation'}
          </button>)}
        </div>
        <p className="text-[11px] leading-relaxed text-muted">{result.nodes.toLocaleString()} nodes · {result.cell_size_mm ? `cell size ${result.cell_size_mm.map(n).join(' × ')}` : `target element size ${n(result.mesh_size_mm)}`} mm<br />
          Volume difference {n(result.volume_error_fraction * 100)}% · solve residual {result.relative_residual.toExponential(1)}<br />
          Estimated yield ratio for this load case; assess convergence before using it for design.</p>
        <div className="rounded-md border border-warn/20 bg-warn/5 p-3 text-[11px] leading-relaxed text-muted">
          <h4 className="mb-2 font-semibold text-warn">Analysis assumptions</h4>
          <ul className="list-disc space-y-2 pl-3">{result.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      </>}
    </div>
  );
}

function NumberField({ label, value, min, max, onChange }: { label: string; value: number; min: number; max: number; onChange: (v: number) => void }) {
  return <label className="flex flex-col gap-1 text-[11px] text-muted">{label}
    <input type="number" required step="any" min={min} max={max} value={Number.isNaN(value) ? '' : value}
      onChange={(e) => onChange(e.target.valueAsNumber)} className={INPUT} />
  </label>;
}

function Metric({ label, value, warn = false }: { label: string; value: string; warn?: boolean }) {
  return <div className="rounded-md border border-line bg-raised p-2.5">
    <div className="text-[10px] text-muted">{label}</div><div className={`mt-1 font-mono text-[14px] ${warn ? 'text-warn' : 'text-ink'}`}>{value}</div>
  </div>;
}
