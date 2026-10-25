export type Face = 'x-' | 'x+' | 'y-' | 'y+' | 'z-' | 'z+';

export type FeaSettings = {
  youngs_modulus_mpa: number;
  poisson_ratio: number;
  yield_strength_mpa: number;
  force_n: number;
  fixed_face: Face;
  load_direction: Face;
  resolution: 10 | 16 | 22;
  /** CAD face numbers held and loaded; without them, the two bounding-box ends of fixed_face's axis. */
  support_faces?: number[] | null;
  load_faces?: number[] | null;
  /** Re-solve on a finer mesh and report the change. */
  check_mesh?: boolean;
};

export type MeshCheck = { peak_away_change: number; displacement_change: number; finer_mesh_size_mm: number } | { skipped: string };

export type FeaResult = {
  engine: 'ngsolve';
  method: string;
  element_type: string;
  mesh_size_mm: number;
  dofs?: number;
  analysis_id: string;
  revision: number;
  settings: FeaSettings;
  elements: number;
  nodes: number;
  max_von_mises_mpa: number;
  max_displacement_mm: number;
  yield_ratio: number | null;
  deformation_scale: number;
  relative_residual: number;
  volume_error_fraction: number;
  reaction_force_n: number[];
  force_vector_n: number[];
  fixed_center_mm: number[];
  load_center_mm: number[];
  bounds_mm: number[][];
  warnings: string[];
  support_faces?: number[];
  load_faces?: number[];
  peak_at_support?: boolean;
  peak_away_from_supports_mpa?: number;
  yield_ratio_away_from_supports?: number | null;
  mesh_check?: MeshCheck | null;
  setup_url?: string;
  stress_url: string;
  displacement_url: string;
  report_url: string;
};

/** Where a run is: queued, then the worker's stages, then done. */
export type FeaStage = 'queued' | 'meshing' | 'solving' | 'post-processing' | 'checking mesh';

export type FeaJob = {
  analysis_id: string;
  revision: number;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  stage?: FeaStage | string;
  /** Who started it: the FEA panel or Aida. */
  source?: 'user' | 'agent';
  settings?: FeaSettings;
  error: string | null;
  result: FeaResult | null;
};

/** Typical isotropic values; printed parts are weaker across layers. */
export const MATERIALS = [
  { label: 'PLA', youngs_modulus_mpa: 3500, poisson_ratio: 0.35, yield_strength_mpa: 50 },
  { label: 'PETG', youngs_modulus_mpa: 2100, poisson_ratio: 0.38, yield_strength_mpa: 50 },
  { label: 'ABS', youngs_modulus_mpa: 2200, poisson_ratio: 0.35, yield_strength_mpa: 40 },
  { label: 'Aluminum 6061', youngs_modulus_mpa: 69000, poisson_ratio: 0.33, yield_strength_mpa: 276 },
  { label: 'Steel', youngs_modulus_mpa: 210000, poisson_ratio: 0.3, yield_strength_mpa: 250 },
] as const;

export const DEFAULT_FEA: FeaSettings = {
  youngs_modulus_mpa: 69000, poisson_ratio: 0.33, yield_strength_mpa: 276,
  force_n: 100, fixed_face: 'x-', load_direction: 'z-', resolution: 16, check_mesh: true,
};

export const FEA_STAGES: { id: FeaStage; label: string }[] = [
  { id: 'meshing', label: 'Meshing the CAD' },
  { id: 'solving', label: 'Solving' },
  { id: 'post-processing', label: 'Colouring stress' },
  { id: 'checking mesh', label: 'Checking on a finer mesh' },
];

/**
 * The safety factor to judge the part by: a peak at a rigid support's edge is a modelling
 * artifact that grows with refinement, so then use the peak away from supports.
 */
export function governingSafety(r: FeaResult): { value: number | null; awayFromSupports: boolean } {
  if (r.peak_at_support && r.yield_ratio_away_from_supports != null) return { value: r.yield_ratio_away_from_supports, awayFromSupports: true };
  return { value: r.yield_ratio, awayFromSupports: false };
}

/** Largest relative change the finer mesh made, or null if not checked. */
export function meshChange(r: FeaResult): number | null {
  const check = r.mesh_check;
  return check && 'peak_away_change' in check ? Math.max(check.peak_away_change, check.displacement_change) : null;
}

export const FEA_GRADIENT = 'linear-gradient(90deg, #2659d9, #22d3ee, #facc15, #ef4444)';
export const oppositeFace = (face: Face) => `${face[0]}${face[1] === '-' ? '+' : '-'}`;
export const feaNumber = (value: number) => value === 0 ? '0' : value < 0.001 || value >= 100000
  ? value.toExponential(2) : Number(value.toPrecision(4)).toString();
export const feaBusy = (job: FeaJob | null) => job?.status === 'queued' || job?.status === 'running';
