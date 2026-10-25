"""Transport-independent inputs. All quantities use N, mm and MPa."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Face = Literal['x-', 'x+', 'y-', 'y+', 'z-', 'z+']
FaceIds = list[int] | None


class FeaSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

    youngs_modulus_mpa: float = Field(default=69000, ge=1, le=1_000_000)
    poisson_ratio: float = Field(default=0.33, ge=0, le=0.45)
    yield_strength_mpa: float = Field(default=240, gt=0, le=100_000)
    force_n: float = Field(default=100, gt=0, le=1_000_000)
    # Where the part is held and pushed: CAD face numbers (from the face listing), or else
    # the two bounding-box ends of fixed_face's axis.
    support_faces: FaceIds = Field(default=None, min_length=1, max_length=64)
    load_faces: FaceIds = Field(default=None, min_length=1, max_length=64)
    fixed_face: Face = 'x-'
    load_direction: Face = 'z-'
    resolution: Literal[10, 16, 22] = 16
    # Re-solve on a finer mesh and report how much the answer moved.
    check_mesh: bool = False

    @model_validator(mode='after')
    def _distinct(self):
        if self.support_faces and self.load_faces and set(self.support_faces) & set(self.load_faces):
            raise ValueError('A face cannot be both a support and a load face.')
        return self
