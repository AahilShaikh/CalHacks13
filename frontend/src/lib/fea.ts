export type Face = 'x-' | 'x+' | 'y-' | 'y+' | 'z-' | 'z+';

export type FeaSettings = {
  engine: 'auto' | 'ngsolve' | 'voxel';
  youngs_modulus_mpa: number;
  poisson_ratio: number;
  yield_strength_mpa: number;
  force_n: number;
  fixed_face: Face;
  load_direction: Face;
  resolution: 10 | 16 | 22;
};

export type FeaResult = {
  engine: 'ngsolve' | 'voxel';
  method: string;
  element_type: string;
  mesh_size_mm: number;
  analysis_id: string;
  revision: number;
  settings: FeaSettings;
  elements: number;
  nodes: number;
  cell_size_mm: number[] | null;
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
  stress_url: string;
  displacement_url: string;
  report_url: string;
};

export type FeaJob = {
  engine: 'ngsolve' | 'voxel';
  analysis_id: string;
  revision: number;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  error: string | null;
  result: FeaResult | null;
};

export const DEFAULT_FEA: FeaSettings = {
  engine: 'auto',
  youngs_modulus_mpa: 69000, poisson_ratio: 0.33, yield_strength_mpa: 240,
  force_n: 100, fixed_face: 'x-', load_direction: 'z-', resolution: 16,
};

export const FEA_GRADIENT = 'linear-gradient(90deg, #2659d9, #22d3ee, #facc15, #ef4444)';
export const oppositeFace = (face: Face) => `${face[0]}${face[1] === '-' ? '+' : '-'}`;
export const feaNumber = (value: number) => value === 0 ? '0' : value < 0.001 || value >= 100000
  ? value.toExponential(2) : Number(value.toPrecision(4)).toString();
