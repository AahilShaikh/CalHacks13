import { useStore } from '../store';

/**
 * The Model tab: the parametric model's features, in build order.
 * Parameters (Figma: ParamRow, 2:112) arrive with the model_state event planned for Phase 3.
 */
export function ModelTab() {
  const features = useStore((s) => s.features);
  const deviations = useStore((s) => s.deviations);
  const trends = deviations.map((d) => d.trend).filter(Boolean);

  return (
    <div className="scroll-thin flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto">
      <h3 className="pb-1 text-[10px] font-semibold uppercase tracking-[0.6px] text-faint">Features</h3>
      {features.length === 0 ? (
        <p className="px-3 py-2 text-[13px] text-faint">No features yet. They appear as the agent builds the model.</p>
      ) : (
        features.map((feature, index) => {
          const [id, ...rest] = feature.split(' ');
          const trend = trends[index];
          return (
            <div key={feature} className="flex items-center gap-2 px-3 py-2">
              <span className="font-mono text-[12px] text-faint">{id}</span>
              <span className="font-mono text-[12px]">{rest.join(' ') || id}</span>
              <span className="flex-1" />
              {trend && (
                <span className={`text-[11px] font-medium ${trend === 'better' ? 'text-ok' : trend === 'worse' ? 'text-error' : 'text-muted'}`}>
                  {trend}
                </span>
              )}
            </div>
          );
        })
      )}
      <h3 className="pt-4 pb-1 text-[10px] font-semibold uppercase tracking-[0.6px] text-faint">Parameters</h3>
      <p className="px-3 py-2 text-[13px] text-faint">
        Measured, estimated and assumed dimensions will be listed here (Phase 3).
      </p>
    </div>
  );
}
