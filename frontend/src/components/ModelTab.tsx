import { useState } from 'react';
import type { AssemblyState, ModelFeature, ModelParam, ModelState, ParamValue, WorkspaceState } from '../lib/events';
import { partOf, useStore } from '../store';

const SOURCE_STYLE = {
  measured: 'bg-ok/10 text-ok',
  estimated: 'bg-warn/10 text-warn',
  assumed: 'bg-muted/10 text-muted',
} as const;
const TREND_STYLE: Record<string, string> = {
  better: 'text-ok',
  worse: 'text-error',
  mixed: 'text-warn',
  first: 'text-accent-ink',
  restored: 'text-muted',
};

const fmt = (v: ParamValue): string => {
  if (Array.isArray(v)) {
    const text = `[${v.map(fmt).join(', ')}]`;
    return text.length > 28 ? `${text.slice(0, 26)}…]` : text;
  }
  return typeof v === 'number' ? String(Math.round(v * 10000) / 10000) : v;
};

/** The Model tab: pick a part (or the assembly), then its parameters, features and history. */
export function ModelTab() {
  const workspace = useStore((s) => s.workspace);
  const selected = useStore((s) => s.selectedPart);
  if (!workspace || (!workspace.parts.length && !workspace.assembly.features.length)) {
    return (
      <div className="flex flex-1 items-center justify-center px-6 text-center text-[13px] leading-[1.45] text-faint">
        The parametric model appears here once the agent records its first measurements.
      </div>
    );
  }
  const showAssembly = selected === 'assembly';
  const part = showAssembly ? null : partOf(workspace, selected) ?? workspace.parts[0] ?? null;
  return (
    <div className="scroll-thin flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto pr-1">
      <PartPicker workspace={workspace} current={showAssembly ? 'assembly' : part?.name ?? null} />
      {showAssembly ? (
        <AssemblyView assembly={workspace.assembly} />
      ) : part ? (
        <ModelView model={part} owner={part.name} note={part.description || undefined}
          unchecked={!part.has_reference} />
      ) : null}
    </div>
  );
}

/** Chips for every part plus the assembly; the agent's active part is marked. */
function PartPicker({ workspace, current }: { workspace: WorkspaceState; current: string | null }) {
  const selectPart = useStore((s) => s.selectPart);
  const selected = useStore((s) => s.selectedPart);
  const hasAssembly = workspace.assembly.features.length > 0;
  if (workspace.parts.length < 2 && !hasAssembly) return null;
  const chip = (name: string, label: string, extra?: React.ReactNode) => (
    <button
      key={name}
      type="button"
      aria-pressed={current === name}
      onClick={() => selectPart(selected === name ? null : name)}
      className={`flex items-center gap-1 rounded-lg px-2 py-0.5 font-mono text-[11px] ${
        current === name ? 'bg-accent-soft text-accent-ink' : 'bg-line text-muted hover:text-ink'
      }`}
    >
      {label}
      {extra}
    </button>
  );
  return (
    <div className="flex flex-wrap items-center gap-1.5 pb-2" role="group" aria-label="Parts">
      {workspace.parts.map((p) =>
        chip(p.name, p.name, <>
          {p.has_reference && <span title="Has a reference scan" className="size-1.5 rounded-full bg-mesh" />}
          {workspace.active === p.name && <span title="The agent is working on this part" className="text-[9px] text-faint">●</span>}
        </>),
      )}
      {hasAssembly && chip('assembly', `Assembly · ${workspace.assembly.instance_count}`)}
    </div>
  );
}

function AssemblyView({ assembly }: { assembly: AssemblyState }) {
  const counts = Object.entries(assembly.instances);
  return (
    <>
      <Heading>Instances</Heading>
      {counts.length === 0 && <Empty>Nothing placed yet.</Empty>}
      {counts.map(([part, count]) => (
        <div key={part} className="flex items-center gap-2 px-3 py-1.5 text-[12px]">
          <span className="font-mono">{part}</span>
          <span className="flex-1" />
          <span className="font-mono text-muted">× {count}</span>
        </div>
      ))}
      {assembly.variants.length > 1 && (
        <>
          <Heading className="pt-3">Variants</Heading>
          {assembly.variants.map((v) => {
            const overrides = Object.entries(v.overrides);
            return (
              <div key={v.name} className="flex items-center gap-2 px-3 py-1.5 text-[11px]">
                <span className="font-mono text-[12px]">{v.part}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-muted">
                  {overrides.length ? overrides.map(([k, val]) => `${k}=${fmt(val)}`).join(', ') : 'defaults'}
                </span>
                <span className="font-mono text-muted">× {v.count}</span>
              </div>
            );
          })}
        </>
      )}
      <div className="pt-3" />
      <ModelView model={assembly} owner="assembly" unchecked featuresLabel="Layout" />
    </>
  );
}

/** Parameters with provenance, features in build order, and revision history of one model. */
function ModelView({ model, owner, note, unchecked = false, featuresLabel = 'Features' }: {
  model: ModelState;
  owner: string;
  note?: string;
  unchecked?: boolean;
  featuresLabel?: string;
}) {
  const params = Object.entries(model.params);
  const counts = { measured: 0, estimated: 0, assumed: 0 };
  for (const [, p] of params) counts[p.source]++;

  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5 pb-2 text-[11px]">
        <span className="font-mono text-muted">revision {model.revision}</span>
        {unchecked && owner !== 'assembly' && <span className="text-faint">· designed, no scan</span>}
        <span className="flex-1" />
        {(['measured', 'estimated', 'assumed'] as const).map((source) => (
          <span key={source} className={`rounded-sm px-1.5 py-0.5 font-medium ${SOURCE_STYLE[source]}`}>
            {counts[source]} {source}
          </span>
        ))}
      </div>
      {note && <p className="px-3 pb-2 text-[12px] leading-[1.45] text-muted">{note}</p>}

      <Heading>Parameters</Heading>
      {params.length === 0 && <Empty>No parameters yet.</Empty>}
      {params.map(([name, param]) => (
        <ParamRow key={name} name={name} owner={owner} param={param} revision={model.revision} />
      ))}

      <Heading className="pt-4">{featuresLabel}</Heading>
      {model.features.length === 0 && <Empty>No features yet.</Empty>}
      {model.features.map((feature, index) => (
        <FeatureRow key={feature.id} feature={feature} index={index + 1} model={model} />
      ))}
      {model.placement && (
        <p className="px-3 py-1 font-mono text-[11px] text-muted">placed at {model.placement}</p>
      )}

      <History model={model} />
    </>
  );
}

function Heading({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return <h3 className={`pb-1 text-[10px] font-semibold uppercase tracking-[0.6px] text-faint ${className}`}>{children}</h3>;
}
function Empty({ children }: { children: React.ReactNode }) {
  return <p className="px-3 py-2 text-[13px] text-faint">{children}</p>;
}

/** Figma: ParamRow (2:112). Click for the evidence behind the number. */
function ParamRow({ name, owner, param, revision }: { name: string; owner: string; param: ModelParam; revision: number }) {
  const [open, setOpen] = useState(false);
  const changed = useStore((s) => s.changedParams.includes(`${owner}.${name}`));
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
        className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-left hover:bg-surface"
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
          {Array.isArray(param.value) && <span className="break-all font-mono text-[11px]">{JSON.stringify(param.value)}</span>}
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
        className="flex w-full items-center gap-2 rounded-sm px-3 py-2 text-left hover:bg-surface"
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
          <pre className="scroll-thin max-h-48 overflow-auto rounded-sm bg-subtle p-2 font-mono text-[11px] leading-relaxed text-muted">
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
