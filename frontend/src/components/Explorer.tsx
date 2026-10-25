import { Box, Boxes, ChevronRight, Eye, EyeOff, type LucideIcon } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useActivity } from '../lib/activity';
import type { AssemblyState, ModelFeature, ModelParam, ModelState, ParamValue, PartState } from '../lib/events';
import { referenceRevision, useStore } from '../store';
import { cx } from './ui';

/**
 * The assembly as a tree, like layers: assembly -> parts -> parameters, features, history.
 * Parameters are the deepest level; click one for the evidence behind its value.
 */

const SOURCE = {
  measured: { dot: 'bg-ok', label: 'Measured from the scan' },
  estimated: { dot: 'bg-amber-400', label: 'Estimated' },
  assumed: { dot: 'bg-line-strong', label: 'Assumed' },
} as const;
const TREND: Record<string, string> = {
  better: 'text-ok',
  worse: 'text-error',
  mixed: 'text-warn',
  first: 'text-muted',
  restored: 'text-muted',
};

export const fmtValue = (v: ParamValue): string => {
  if (Array.isArray(v)) {
    const text = `[${v.map(fmtValue).join(', ')}]`;
    return text.length > 24 ? `${text.slice(0, 22)}…]` : text;
  }
  return typeof v === 'number' ? String(Math.round(v * 10000) / 10000) : v;
};

/** Open/closed state per node; nodes without an explicit choice use their default. */
function useExpanded() {
  const [choices, setChoices] = useState<Record<string, boolean>>({});
  return {
    isOpen: (key: string, fallback: boolean) => choices[key] ?? fallback,
    set: (key: string, open: boolean) => setChoices((c) => ({ ...c, [key]: open })),
  };
}
type Expanded = ReturnType<typeof useExpanded>;

export function Explorer() {
  const workspace = useStore((s) => s.workspace);
  const revisions = useStore((s) => s.revisions);
  const selected = useStore((s) => s.selectedPart);
  const selectPart = useStore((s) => s.selectPart);
  const expanded = useExpanded();

  // Before the agent's first model call there is no workspace snapshot, only the uploaded scans.
  const scanned = referenceRevision(revisions)?.parts ?? [];
  const parts: (PartState | string)[] = workspace?.parts.length ? workspace.parts : scanned;
  const assembly = workspace?.assembly;

  if (!parts.length && !assembly?.features.length) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-1.5 px-6 text-center">
        <Boxes size={20} strokeWidth={1.5} className="text-faint" aria-hidden />
        <p className="text-[12.5px] leading-[1.5] text-muted">
          Parts appear here as you add scans or as Aida creates them.
        </p>
      </div>
    );
  }

  const single = parts.length === 1;
  const rootOpen = expanded.isOpen('root', true);
  return (
    <ul className="flex flex-col py-1" aria-label="Assembly">
      <Node
        depth={0}
        icon={Boxes}
        label="Assembly"
        meta={assembly?.instance_count ? `${assembly.instance_count} placed` : `${parts.length} part${single ? '' : 's'}`}
        open={rootOpen}
        onToggle={() => expanded.set('root', !rootOpen)}
        selected={selected === 'assembly'}
        onSelect={() => selectPart(selected === 'assembly' ? null : 'assembly')}
      >
        {parts.map((part) =>
          typeof part === 'string' ? (
            <PartNode key={part} name={part} expanded={expanded} single={single} />
          ) : (
            <PartNode key={part.name} name={part.name} part={part} expanded={expanded} single={single}
              count={assembly?.instances[part.name]} />
          ),
        )}
        {assembly && <AssemblyGroups assembly={assembly} expanded={expanded} />}
      </Node>
    </ul>
  );
}

/** One row of the tree. Clicking the label selects; the chevron opens and closes. */
function Node({ depth, icon: Icon, label, meta, trailing, open, onToggle, selected = false, onSelect, muted = false,
  mono = false, children }: {
  depth: number;
  icon?: LucideIcon;
  label: ReactNode;
  meta?: ReactNode;
  trailing?: ReactNode;
  open?: boolean;
  onToggle?: () => void;
  selected?: boolean;
  onSelect?: () => void;
  muted?: boolean;
  mono?: boolean;
  children?: ReactNode;
}) {
  const expandable = onToggle !== undefined;
  return (
    <li>
      <div
        className={cx('group relative flex h-7 items-center gap-1 rounded-md pr-1.5',
          selected ? 'bg-accent-soft' : 'hover:bg-subtle')}
        style={{ paddingLeft: 4 + depth * 14 }}
      >
        {expandable ? (
          <button type="button" onClick={onToggle} aria-expanded={open}
            aria-label={`${open ? 'Collapse' : 'Expand'} ${typeof label === 'string' ? label : ''}`}
            className="flex size-5 shrink-0 items-center justify-center rounded-sm text-faint hover:text-ink">
            <ChevronRight size={13} className={cx('transition-transform', open && 'rotate-90')} aria-hidden />
          </button>
        ) : (
          <span className="size-5 shrink-0" />
        )}
        <button
          type="button"
          onClick={onSelect ?? onToggle}
          aria-pressed={onSelect ? selected : undefined}
          className={cx('flex h-full min-w-0 flex-1 items-center gap-1.5 text-left', muted && 'opacity-50')}
        >
          {Icon && <Icon size={14} strokeWidth={1.75} className={cx('shrink-0', selected ? 'text-accent-ink' : 'text-faint')} aria-hidden />}
          <span className={cx('min-w-0 truncate text-[12.5px]', mono && 'font-mono text-[12px]',
            selected ? 'font-medium text-ink' : 'text-ink')}>
            {label}
          </span>
          {meta !== undefined && <span className="ml-auto shrink-0 pl-2 font-mono text-[11px] text-faint">{meta}</span>}
        </button>
        {trailing}
      </div>
      {expandable && open && children && <ul className="flex flex-col">{children}</ul>}
    </li>
  );
}

function PartNode({ name, part, expanded, single, count }: {
  name: string;
  part?: PartState;
  expanded: Expanded;
  single: boolean;
  count?: number;
}) {
  const selected = useStore((s) => s.selectedPart === name);
  const selectPart = useStore((s) => s.selectPart);
  const hidden = useStore((s) => s.hiddenParts.includes(name));
  const toggleHidden = useStore((s) => s.toggleHidden);
  const agentHere = useStore((s) => s.workspace?.active === name && (s.workspace?.parts.length ?? 0) > 1);
  const { working } = useActivity();
  const key = `part:${name}`;
  const open = expanded.isOpen(key, single || selected);
  const deviation = part?.deviation;

  const meta = (
    <span className="flex items-center gap-1.5">
      {count !== undefined && count > 1 && <span>×{count}</span>}
      {deviation && (
        <span title={`p95 ${deviation.p95.toFixed(3)} (${deviation.verdict.replaceAll('_', ' ')})`}
          className={cx('size-1.5 rounded-full', deviation.verdict === 'within_threshold' ? 'bg-ok' : 'bg-amber-400')} />
      )}
    </span>
  );

  return (
    <Node
      depth={1}
      icon={Box}
      label={
        <span className="flex items-center gap-1.5">
          {name}
          {agentHere && (
            <span title="Aida is working on this part"
              className={cx('size-1.5 rounded-full bg-accent', working && 'animate-pulse-dot')} />
          )}
        </span>
      }
      meta={meta}
      open={open}
      onToggle={() => expanded.set(key, !open)}
      selected={selected}
      onSelect={() => {
        selectPart(selected ? null : name);
        if (!selected) expanded.set(key, true);
      }}
      muted={hidden}
      trailing={
        <button
          type="button"
          onClick={() => toggleHidden(name)}
          aria-pressed={hidden}
          aria-label={hidden ? `Show ${name}` : `Hide ${name}`}
          title={hidden ? 'Show in 3D' : 'Hide in 3D'}
          className={cx('flex size-6 shrink-0 items-center justify-center rounded-sm text-faint hover:bg-surface hover:text-ink',
            hidden ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-visible:opacity-100')}
        >
          {hidden ? <EyeOff size={13} aria-hidden /> : <Eye size={13} aria-hidden />}
        </button>
      }
    >
      {part ? <ModelGroups model={part} owner={name} expanded={expanded} single={single || selected} /> : (
        <Hint depth={2}>Waiting for Aida to measure this scan.</Hint>
      )}
    </Node>
  );
}

function Hint({ depth, children }: { depth: number; children: ReactNode }) {
  return (
    <li className="py-1 pr-3 text-[12px] leading-[1.45] text-faint" style={{ paddingLeft: 4 + depth * 14 + 24 }}>
      {children}
    </li>
  );
}

/** Parameters, features and history of one part (or of the assembly). */
function ModelGroups({ model, owner, expanded, single, featuresLabel = 'Features', depth = 2 }: {
  model: ModelState & { description?: string; has_reference?: boolean };
  owner: string;
  expanded: Expanded;
  single: boolean;
  featuresLabel?: string;
  depth?: number;
}) {
  const params = Object.entries(model.params);
  const counts = { measured: 0, estimated: 0, assumed: 0 };
  for (const [, p] of params) counts[p.source]++;
  const paramsKey = `${owner}:params`;
  const featuresKey = `${owner}:features`;
  const historyKey = `${owner}:history`;
  const paramsOpen = expanded.isOpen(paramsKey, single);
  const featuresOpen = expanded.isOpen(featuresKey, false);
  const historyOpen = expanded.isOpen(historyKey, false);
  const breakdown = (['measured', 'estimated', 'assumed'] as const)
    .filter((s) => counts[s]).map((s) => `${counts[s]} ${s}`).join(', ');

  return (
    <>
      {model.description && <Hint depth={depth}>{model.description}</Hint>}
      {owner !== 'assembly' && model.has_reference === false && <Hint depth={depth}>Designed from a description, no scan to check against.</Hint>}
      {(params.length > 0 || owner !== 'assembly') && (
        <Node depth={depth} label="Parameters" meta={<span title={breakdown || undefined}>{params.length}</span>}
          open={paramsOpen} onToggle={() => expanded.set(paramsKey, !paramsOpen)}>
          {params.length ? params.map(([name, param]) => (
            <ParamRow key={name} name={name} owner={owner} param={param} revision={model.revision} depth={depth + 1} />
          )) : <Hint depth={depth + 1}>None yet.</Hint>}
        </Node>
      )}
      <Node depth={depth} label={featuresLabel} meta={model.features.length}
        open={featuresOpen} onToggle={() => expanded.set(featuresKey, !featuresOpen)}>
        {model.features.length ? model.features.map((feature, index) => (
          <FeatureRow key={feature.id} feature={feature} index={index + 1} model={model} depth={depth + 1} />
        )) : <Hint depth={depth + 1}>None yet.</Hint>}
        {model.placement && <Hint depth={depth + 1}>Placed at {model.placement}</Hint>}
      </Node>
      {model.history.length > 0 && (
        <Node depth={depth} label="History" meta={`r${model.revision}`}
          open={historyOpen} onToggle={() => expanded.set(historyKey, !historyOpen)}>
          {[...model.history].reverse().map((h, i) => (
            <li key={`${h.revision}-${i}`} className="flex h-6 items-center gap-2 pr-2 text-[11.5px]"
              style={{ paddingLeft: 4 + (depth + 1) * 14 + 24 }}>
              <span className="w-7 shrink-0 font-mono text-faint">r{h.revision}</span>
              <span className="min-w-0 flex-1 truncate text-muted" title={h.action}>{h.action}</span>
              {h.deviation && <span className="font-mono text-[11px] text-faint">{h.deviation.p95.toFixed(3)}</span>}
              <span className={cx('w-12 shrink-0 text-right text-[11px]', TREND[h.trend] ?? 'text-faint')}>{h.trend}</span>
            </li>
          ))}
        </Node>
      )}
    </>
  );
}

function AssemblyGroups({ assembly, expanded }: { assembly: AssemblyState; expanded: Expanded }) {
  const hasLayout = assembly.features.length > 0 || Object.keys(assembly.params).length > 0;
  const variantsKey = 'assembly:variants';
  const variantsOpen = expanded.isOpen(variantsKey, false);
  if (!hasLayout) return null;
  return (
    <>
      <ModelGroups model={assembly} owner="assembly" expanded={expanded} single={false} featuresLabel="Layout" depth={1} />
      {assembly.variants.length > 1 && (
        <Node depth={1} label="Variants" meta={assembly.variants.length}
          open={variantsOpen} onToggle={() => expanded.set(variantsKey, !variantsOpen)}>
          {assembly.variants.map((v) => {
            const overrides = Object.entries(v.overrides);
            return (
              <li key={v.name} className="flex h-6 items-center gap-2 pr-2 text-[11.5px]" style={{ paddingLeft: 4 + 2 * 14 + 24 }}>
                <span className="shrink-0 font-mono text-ink">{v.part}</span>
                <span className="min-w-0 flex-1 truncate font-mono text-faint">
                  {overrides.length ? overrides.map(([k, val]) => `${k}=${fmtValue(val)}`).join(', ') : 'defaults'}
                </span>
                <span className="font-mono text-faint">×{v.count}</span>
              </li>
            );
          })}
        </Node>
      )}
    </>
  );
}

/** A parameter: name, value and where the value came from. Click for the evidence. */
function ParamRow({ name, owner, param, revision, depth }: {
  name: string;
  owner: string;
  param: ModelParam;
  revision: number;
  depth: number;
}) {
  const [open, setOpen] = useState(false);
  const changed = useStore((s) => s.changedParams.includes(`${owner}.${name}`));
  const rounded = param.measured !== null && typeof param.value === 'number' && param.measured !== param.value;
  return (
    <li key={changed ? `${name}-${revision}` : name} className={cx('rounded-md', changed && 'animate-flash')}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex h-6 w-full items-center gap-2 rounded-md pr-2 text-left hover:bg-subtle"
        style={{ paddingLeft: 4 + depth * 14 + 24 }}
      >
        <span className={cx('size-1.5 shrink-0 rounded-full', SOURCE[param.source].dot)} title={SOURCE[param.source].label} />
        <span className="min-w-0 truncate font-mono text-[11.5px] text-muted">{name}</span>
        <span className="ml-auto shrink-0 pl-2 font-mono text-[11.5px] font-medium text-ink">{fmtValue(param.value)}</span>
      </button>
      {open && (
        <div className="flex flex-col gap-1 pb-2 pr-3 text-[11.5px] leading-[1.5] text-muted"
          style={{ paddingLeft: 4 + depth * 14 + 38 }}>
          <span className="text-faint">{SOURCE[param.source].label}</span>
          {rounded && <span className="font-mono">measured {fmtValue(param.measured!)}, rounded to {fmtValue(param.value)}</span>}
          {Array.isArray(param.value) && <span className="break-all font-mono">{JSON.stringify(param.value)}</span>}
          <span>{param.evidence || 'No evidence recorded.'}</span>
          {param.note && <span className="text-faint">{param.note}</span>}
        </div>
      )}
    </li>
  );
}

/** The latest history entry that added or edited this feature, for its trend. */
function featureChange(model: ModelState, feature: ModelFeature) {
  return model.history.findLast((h) => h.action === `add ${feature.name}` || h.action === `edit ${feature.id}`);
}

function FeatureRow({ feature, index, model, depth }: { feature: ModelFeature; index: number; model: ModelState; depth: number }) {
  const [open, setOpen] = useState(false);
  const change = featureChange(model, feature);
  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex h-6 w-full items-center gap-2 rounded-md pr-2 text-left hover:bg-subtle"
        style={{ paddingLeft: 4 + depth * 14 + 24 }}
      >
        <span className="w-4 shrink-0 font-mono text-[11px] text-faint">{index}</span>
        <span className="min-w-0 truncate font-mono text-[11.5px] text-ink">{feature.name}</span>
        {change && change.trend !== 'first' && (
          <span className={cx('ml-auto shrink-0 pl-2 text-[11px]', TREND[change.trend] ?? 'text-faint')}>{change.trend}</span>
        )}
      </button>
      {open && (
        <div className="flex flex-col gap-1.5 pb-2 pr-3" style={{ paddingLeft: 4 + depth * 14 + 24 }}>
          {feature.evidence && <p className="text-[11.5px] leading-[1.5] text-muted">{feature.evidence}</p>}
          {change?.deviation && <p className="font-mono text-[11px] text-faint">p95 {change.deviation.p95.toFixed(3)} after this step</p>}
          {feature.used_params.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {feature.used_params.map((p) => (
                <span key={p} className="rounded-sm bg-subtle px-1.5 py-0.5 font-mono text-[10.5px] text-muted">{p}</span>
              ))}
            </div>
          )}
          <pre className="scroll-thin max-h-48 overflow-auto rounded-md border border-line bg-subtle p-2 font-mono text-[11px] leading-relaxed text-muted">
            {feature.code}
          </pre>
        </div>
      )}
    </li>
  );
}
