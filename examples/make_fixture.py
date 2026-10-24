"""Create a small asymmetric plate with an off-center hole for mesh inspection."""
from pathlib import Path
from build123d import Box, Cylinder, Pos, export_stl

path = Path(__file__).with_name('asymmetric.stl')
shape = Box(60, 40, 6) - Pos(15, 5, 0) * Cylinder(4, 10)
export_stl(shape, str(path))
print(path.resolve())
