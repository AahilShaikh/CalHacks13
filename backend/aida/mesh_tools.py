"""Agent-facing reference-mesh tools: framed views, slices and slice series.

Rendering (VTK) and probing (trimesh/matplotlib) run in subprocesses so a
pathological mesh can be killed on timeout without taking the session down.
"""
import asyncio
import json
import sys
from uuid import uuid4

from .mesh_render import PRESETS, SpecError, normalize_spec

_VEC3 = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 3, 'maxItems': 3}

VIEW_SCHEMA = {
    'type': 'object',
    'properties': {
        'direction': {'type': 'string', 'enum': sorted(PRESETS),
                      'description': 'Preset camera side. Omit when a slice is given to face the cut.'},
        'view_dir': {**_VEC3, 'description': 'Custom direction from the part toward the camera.'},
        'azimuth': {'type': 'number', 'description': 'Degrees of orbit about the view-up axis, after the preset.'},
        'elevation': {'type': 'number', 'description': 'Degrees of orbit up/down, after the preset.'},
        'center': {**_VEC3, 'description': 'Pan: model point placed at the image centre.'},
        'view_width': {'type': 'number', 'description': 'Zoom: model units shown across the 800 px image.'},
        'fit_box': {'type': 'array', 'items': _VEC3, 'minItems': 2, 'maxItems': 2,
                    'description': 'Zoom to a region: [[xmin,ymin,zmin],[xmax,ymax,zmax]]. Replaces center/view_width.'},
        'slice_axis': {'type': 'string', 'enum': ['x', 'y', 'z'],
                       'description': 'Cut the mesh with a plane normal to this axis.'},
        'slice_normal': {**_VEC3, 'description': 'Cut with an arbitrary plane normal instead of slice_axis.'},
        'slice_at': {'type': 'number', 'description': 'Plane position along the normal (default mesh centre).'},
        'slice_flip': {'type': 'boolean', 'description': 'Remove the other half of the cut.'},
        'grid': {'type': 'boolean', 'description': 'Coordinate grid on axis-aligned views (default true).'},
        'compare_to': {'type': 'string', 'description': 'Generated CAD object to overlay: CAD opaque green, '
                                                        'reference translucent blue.'},
    },
}

SLICE_SCHEMA = {
    'type': 'object',
    'properties': {
        'axis': {'type': 'string', 'enum': ['x', 'y', 'z'], 'description': 'Plane normal axis.'},
        'normal': {**_VEC3, 'description': 'Arbitrary plane normal instead of axis.'},
        'at': {'type': 'number', 'description': 'Plane position along the normal (default mesh centre). '
                                                'Avoid positions exactly on flat faces.'},
        'tolerance': {'type': 'number', 'description': 'Max fit deviation (default 0.25% of the mesh diagonal). '
                                                       'Raise it for noisy scans.'},
        'weld': {'type': 'number', 'description': 'Point-merge distance when chaining section segments.'},
        'compare_to': {'type': 'string', 'description': 'Generated CAD object to slice on the same plane and '
                                                        'compare loop by loop.'},
    },
}

COMPARE_SCHEMA = {
    'type': 'object',
    'properties': {
        'object_name': {'type': 'string', 'description': 'Generated CAD object (from show()) to compare.'},
        'threshold': {'type': 'number', 'description': 'Deviation that counts as a mismatch '
                                                       '(default 0.5% of the mesh diagonal).'},
        'samples': {'type': 'integer', 'minimum': 1000, 'maximum': 200000,
                    'description': 'Surface samples per side (default 20000).'},
        'view': {'type': 'object', 'properties': {k: v for k, v in VIEW_SCHEMA['properties'].items()
                                                  if k != 'compare_to'},
                 'description': 'view_mesh framing options for the heatmap (default iso).'},
    },
    'required': ['object_name'],
}

SERIES_SCHEMA = {
    'type': 'object',
    'properties': {
        'axis': {'type': 'string', 'enum': ['x', 'y', 'z']},
        'count': {'type': 'integer', 'minimum': 2, 'maximum': 60, 'description': 'Evenly spaced slices (default 12).'},
        'start': {'type': 'number'},
        'stop': {'type': 'number'},
        'tolerance': {'type': 'number'},
        'refine': {'type': 'boolean', 'description': 'Bisect each change to within tolerance (default true).'},
    },
    'required': ['axis'],
}

DESCRIPTIONS = {
    'view_mesh': (
        'Render reference_mesh with exact framing. Zoom/pan with fit_box, or center plus view_width; '
        'orbit with direction/view_dir plus azimuth/elevation. A slice cuts the mesh and shades the cut '
        'face orange; the half facing the camera is removed. The image has a coordinate grid, '
        'scale bar and axis triad. The JSON gives units_per_pixel and screen axes, so pixels map to '
        'model coordinates. Returns the image directly; view_image is not needed.'),
    'slice_mesh': (
        'Exact cross-section of reference_mesh. Returns each loop (outer or hole, nesting, area, '
        'bbox) with a fitted primitive (circle, rectangle, rounded_rectangle, slot, or a line/arc '
        'profile), its max_deviation, and a build123d snippet in the returned plane. Also draws the '
        'section with fits dashed. Use the numbers, not the picture, as dimensions.'),
    'slice_series': (
        'Slice reference_mesh at evenly spaced positions along an axis and report constant-profile '
        'ranges plus the transitions between them (refined to the tolerance). Use it to find where '
        'extrusions, steps, pockets and holes start and end, then slice_mesh near each one.'),
    'compare_to_mesh': (
        'Measure how far a generated CAD object is from reference_mesh. Returns a heatmap of the '
        'reference coloured by signed distance (red = CAD missing material, blue = CAD has extra '
        'material), distance statistics both ways, bounding box and volume deltas, and the worst '
        'regions with bounding boxes to zoom or slice into. verdict is within_threshold or deviates.'),
}


def _error(text):
    return {'isError': True, 'content': [{'type': 'text', 'text': text}]}


class MeshTools:
    def __init__(self, artifacts, units=None, timeout=180, tessellate=None):
        self.artifacts = artifacts
        self.units = units
        self.timeout = timeout
        self.tessellate = tessellate  # async (object_name) -> STL path of generated CAD
        self.lock = asyncio.Lock()

    @property
    def mesh(self):
        return self.artifacts.root / 'reference.stl'

    async def _run(self, module, argv):
        command = [sys.executable, '-m', module, *argv]
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout)
        except TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise RuntimeError(f'{module} exceeded {self.timeout}s and was stopped') from None
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        errors = stderr.decode(errors='replace')
        self.artifacts.log('mesh_tool_process', {'module': module, 'returncode': process.returncode,
                                                'stderr': errors[-4000:]})
        if process.returncode:
            lines = [line for line in errors.strip().splitlines() if line.strip()]
            last = lines[-1] if lines else 'no error output'
            # Validation errors are the agent's to fix; show just the message.
            for prefix in ('__main__.SpecError: ', '__main__.ProbeError: ', '__main__.CompareError: ',
                           'aida.mesh_render.SpecError: ', 'aida.mesh_probe.ProbeError: ',
                           'SpecError: ', 'ProbeError: ', 'CompareError: ', 'ValueError: '):
                if last.startswith(prefix):
                    raise ValueError(last[len(prefix):])
            raise RuntimeError(f'{module} failed: {errors[-2000:]}')
        return stdout.decode()

    def _units_args(self):
        return ['--units', self.units] if self.units else []

    async def _cad_stl(self, object_name):
        if self.tessellate is None:
            raise ValueError('CAD comparison is not available in this session')
        if not isinstance(object_name, str) or not object_name.strip():
            raise ValueError('Name the generated CAD object to compare (as passed to show())')
        return await self.tessellate(object_name.strip())

    async def render(self, spec, save_to, overlay=None):
        """Render reference_mesh to ``save_to`` and return image + metadata content."""
        normalize_spec(spec)  # fail fast with a clean message
        extra = ['--overlay', str(overlay)] if overlay else []
        async with self.lock:
            out = await self._run('aida.mesh_render', [str(self.mesh), str(save_to), '--spec',
                                                       json.dumps(spec), *self._units_args(), *extra])
        meta = json.loads(out)
        result = self.artifacts.image(str(save_to))
        result['content'].append({'type': 'text', 'text': json.dumps(meta)})
        result['content'].append({'type': 'text', 'text': f'[SEND: {save_to}]'})
        return result

    async def view(self, args):
        cad = None
        try:
            self.artifacts.log('tool_call', {'name': 'view_mesh', 'arguments': args})
            spec = dict(args)
            target = spec.pop('compare_to', None)
            normalize_spec(spec)
            if target:
                cad = await self._cad_stl(target)
            path = self.artifacts.output_path(f'view-{uuid4().hex[:8]}.png')
            result = await self.render(spec, path, overlay=cad)
        except (SpecError, ValueError, RuntimeError, OSError) as exc:
            result = _error(str(exc))
        finally:
            _discard(cad)
        self.artifacts.log('tool_result', {'name': 'view_mesh', **result})
        return result

    async def slice(self, args):
        cad = None
        try:
            self.artifacts.log('tool_call', {'name': 'slice_mesh', 'arguments': args})
            spec = dict(args)
            target = spec.pop('compare_to', None)
            extra = []
            if target:
                cad = await self._cad_stl(target)
                extra = ['--cad', str(cad)]
            path = self.artifacts.output_path(f'slice-{uuid4().hex[:8]}.png')
            async with self.lock:
                out = await self._run('aida.mesh_probe', ['slice', str(self.mesh), '--spec', json.dumps(spec),
                                                          '--out', str(path), *self._units_args(), *extra])
            result = self.artifacts.image(str(path))
            result['content'].append({'type': 'text', 'text': out.strip()})
            result['content'].append({'type': 'text', 'text': f'[SEND: {path}]'})
        except (ValueError, RuntimeError, OSError) as exc:
            result = _error(str(exc))
        finally:
            _discard(cad)
        self.artifacts.log('tool_result', {'name': 'slice_mesh', **result})
        return result

    async def series(self, args):
        try:
            self.artifacts.log('tool_call', {'name': 'slice_series', 'arguments': args})
            async with self.lock:
                out = await self._run('aida.mesh_probe', ['series', str(self.mesh), '--spec', json.dumps(args)])
            result = {'content': [{'type': 'text', 'text': out.strip()}]}
        except (ValueError, RuntimeError, OSError) as exc:
            result = _error(str(exc))
        self.artifacts.log('tool_result', {'name': 'slice_series', **result})
        return result

    async def compare(self, args):
        cad = None
        try:
            self.artifacts.log('tool_call', {'name': 'compare_to_mesh', 'arguments': args})
            spec = dict(args)
            target = spec.pop('object_name', None)
            normalize_spec(spec.get('view') or {})
            cad = await self._cad_stl(target)
            path = self.artifacts.output_path(f'deviation-{uuid4().hex[:8]}.png')
            async with self.lock:
                out = await self._run('aida.mesh_compare', [str(self.mesh), str(cad), '--spec', json.dumps(spec),
                                                            '--out', str(path), *self._units_args()])
            report = json.loads(out)
            report['object_name'] = target
            result = self.artifacts.image(str(path))
            result['content'].append({'type': 'text', 'text': json.dumps(report)})
            result['content'].append({'type': 'text', 'text': f'[SEND: {path}]'})
        except (SpecError, ValueError, RuntimeError, OSError) as exc:
            result = _error(str(exc))
        finally:
            _discard(cad)
        self.artifacts.log('tool_result', {'name': 'compare_to_mesh', **result})
        return result


def _discard(path):
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
