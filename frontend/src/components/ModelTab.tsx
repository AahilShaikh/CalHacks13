import { useState } from 'react';
import type { ModelFeature, ModelParam, ModelState } from '../lib/events';
import { useStore } from '../store';

const SOURCE_STYLE = {
  measured: 'bg-ok/16 text-ok',
  estimated: 'bg-warn/16 text-warn',
  assumed: 'bg-muted/16 text-muted',
} as const;
const TREND_STYLE: Record<string, string> = {
  better: 'text-ok',
  worse: 'text-error',
  mixed: 'text-warn',
  first: 'text-cad',
  restored: 'text-muted',
};

const fmt = (v: number | string) =>
  typeof v === 'number' ? String(Math.round(v * 10000) / 10000) : v;

/** The Model tab: parameters with provenance, features in build order, and revision history. */
export function ModelTab() {
  const model = useStore((s) => s.model);
  if (!model) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 text-center text-[13px] leading-[1.45] text-faint">
        The parametric model appears here once the agent records its first measurements.
      </div>
    );
  }
  const params = Object.entries(model.params);
  const counts = { measured: 0, estimated: 0, assumed: 0 };
  for (const [, p] of params) counts[p.source]++;

  return (
    <div className="scroll-thin flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto pr-1">
      <div className="flex flex-wrap items-center gap-1.5 pb-2 text-[11px]">
        <span className="font-mono text-muted">revision {model.revision}</span>
        <span className="flex-1" />
        {(['measured', 'estimated', 'assumed'] as const).map((source) => (
          <span key={source} className={`rounded-sm px-1.5 py-0.5 font-medium ${SOURCE_STYLE[source]}`}>
            {counts[source]} {source}
          </span>
        ))}
      </div>

      <Heading>Parameters</Heading>
      {params.length === 0 && <Empty>No parameters yet.</Empty>}
      {params.map(([name, param]) => (
        <ParamRow key={name} name={name} param={param} revision={model.revision} />
      ))}

      <Heading className="pt-4">Features</Heading>
      {model.features.length === 0 && <Empty>No features yet.</Empty>}
      {model.features.map((feature, index) => (
        <FeatureRow key={feature.id} feature={feature} index={index + 1} model={model} />
      ))}
      {model.placement && (
        <p className="px-3 py-1 font-mono text-[11px] text-muted">placed at {model.placement}</p>
      )}

      <History model={model} />
    </div>
  );
}

function Heading({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <h3 className={`pb-1 text-[10px] font-semibold uppercase tracking-[0.6px] text-faint ${className}`}>{children}</h3>;
}
function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-3 py-2 text-[13px] text-faint">{children}</p>;
}

/** Figma: ParamRow (2:112). Click for the evidence behind the number. */
function ParamRow({ name, param, revision }: { name: string; param: ModelParam; revision: number }) {
  const [open, setOpen] = useState(false);
  const changed = useStore((s) => s.changedParams.includes(name));
  const rounded = param.measured !== null && typeof param.value === 'number' && param.measured !== param.value;
  return (
    <div
      // Re-keyed per revision so the highlight replays when the value changes again.
      key={changed ? `${name}-${revision}` : name}
      className={`rounded-sm ${changed ? 'animate-flash' : ''}`}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-left hover:bg-raised"
      >
        <span className="min-w-0 truncate font-mono text-[12px]">{name}</span>
        <span className="flex-1" />
        <span className="font-mono text-[12px] font-medium">{fmt(param.value)}</span>
        <span className={`shrink-0 rounded-sm px-1.5 py-0.5 text-[10px] font-semibold capitalize ${SOURCE_STYLE[param.source]}`}>
          {param.source}
        </span>
      </button>
      {open && (
        <div className="flex flex-col gap-1 px-3 pb-2 text-[12px] leading-[1.45] text-muted">
          {rounded && <span className="font-mono text-[11px]">measured {fmt(param.measured!)}, rounded to {fmt(param.value)}</span>}
          <span>{param.evidence || 'No evidence recorded.'}</span>
          {param.note && <span className="text-faint">{param.note}</span>}
        </div>
      )}
    </div>
  );
}

/** The latest history entry that added or edited this feature, for its trend. */
function featureChange(model: ModelState, feature: ModelFeature) {
  return model.history.findLast((h) => h.action === `add ${feature.name}` || h.action === `edit ${feature.id}`);
}

function FeatureRow({ feature, index, model }: { feature: ModelFeature; index: number; model: ModelState }) {
  const [open, setOpen] = useState(false);
  const change = featureChange(model, feature);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-left hover:bg-raised"
      >
        <span className="font-mono text-[12px] text-faint">{index}</span>
        <span className="min-w-0 truncate font-mono text-[12px]">{feature.name}</span>
        <span className="flex-1" />
        {change?.deviation && (
          <span className="font-mono text-[11px] text-muted">p95 {change.deviation.p95.toFixed(3)}</span>
        )}
        {change && (
          <span className={`text-[11px] font-medium ${TREND_STYLE[change.trend] ?? 'text-muted'}`}>{change.trend}</span>
        )}
      </button>
      {open && (
        <div className="flex flex-col gap-2 px-3 pb-2">
          {feature.evidence && <p className="text-[12px] leading-[1.45] text-muted">{feature.evidence}</p>}
          {feature.used_params.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {feature.used_params.map((p) => (
                <span key={p} className="rounded-sm bg-line px-1.5 py-0.5 font-mono text-[10px] text-muted">P.{p}</span>
              ))}
            </div>
          )}
          <pre className="scroll-thin max-h-48 overflow-auto rounded-sm bg-sunken p-2 font-mono text-[11px] leading-relaxed text-muted">
            {feature.code}
          </pre>
        </div>
      )}
    </div>
  );
}

function History({ model }: { model: ModelState }) {
  const [open, setOpen] = useState(false);
  if (!model.history.length) return null;
  const entries = [...model.history].reverse();
  return (
    <div className="pt-4">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="w-full text-left">
        <Heading>
          Revision history ({model.history.length}) {open ? '▾' : '▸'}
        </Heading>
      </button>
      {open &&
        entries.map((h, i) => (
          <div key={`${h.revision}-${i}`} className="flex items-center gap-2 px-3 py-1.5 text-[11px]">
            <span className="w-8 shrink-0 font-mono text-faint">r{h.revision}</span>
            <span className="min-w-0 flex-1 truncate text-muted">{h.action}</span>
            {h.deviation && <span className="font-mono text-muted">{h.deviation.p95.toFixed(3)}</span>}
            <span className={`w-14 shrink-0 text-right font-medium ${TREND_STYLE[h.trend] ?? 'text-faint'}`}>{h.trend}</span>
          </div>
        ))}
    </div>
  );
}
