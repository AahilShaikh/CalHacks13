"""Transport-independent inputs. All solvers use N, mm and MPa."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Face = Literal['x-', 'x+', 'y-', 'y+', 'z-', 'z+']


class FeaSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    engine: Literal['auto', 'ngsolve', 'voxel'] = 'auto'
    youngs_modulus_mpa: float = Field(default=69000, ge=1, le=1_000_000)
    poisson_ratio: float = Field(default=0.33, ge=0, le=0.45)
    yield_strength_mpa: float = Field(default=240, gt=0, le=100_000)
    force_n: float = Field(default=100, gt=0, le=1_000_000)
    fixed_face: Face = 'x-'
    load_direction: Face = 'z-'
    resolution: Literal[10, 16, 22] = 16
