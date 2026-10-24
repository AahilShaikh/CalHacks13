"""Agent-facing tools for the workspace: parts (model_*), the workspace itself
(workspace_*) and the assembly (assembly_*). See model.py and workspace.py."""
import json
from contextlib import asynccontextmanager

from .cad import text_of
from .model import SOURCES, ModelError
from .workspace import Workspace

_SCALAR = {'anyOf': [{'type': 'number'}, {'type': 'string'}]}
_VALUE = {'anyOf': [{'type': 'number'}, {'type': 'string'},
                    {'type': 'array', 'items': {'anyOf': [{'type': 'number'}, {'type': 'string'},
                                                          {'type': 'array', 'items': _SCALAR}]}}]}
_PARAM_VALUE = {
    'anyOf': [
        *_VALUE['anyOf'],
        {'type': 'object', 'properties': {
            'value': _VALUE,
            'source': {'type': 'string', 'enum': list(SOURCES),
                       'description': 'measured: from slice/series/frame numbers; estimated: read off '
                                      'images; assumed: design intent such as rounding, symmetry or a '
                                      'standard size.'},
            'evidence': {'type': 'string', 'description': 'Which tool result or design rule supports the value.'},
            'measured': {'type': 'number', 'description': 'Raw measured value when value is rounded.'},
            'note': {'type': 'string'},
        }},
    ],
}
_VERIFY = {'type': 'boolean', 'description': 'Check against the reference mesh after rebuilding (default true).'}
_PART = {'type': 'string', 'description': 'Part to change (default: the active part).'}
_PARAMS = {'type': 'object', 'additionalProperties': _PARAM_VALUE,
           'description': 'name: {value, source, evidence, measured, note}. New parameters need a source; '
                          'a bare value only updates an existing one, and updating only metadata keeps the '
                          'value. Values are numbers, strings, or lists of them.'}

SCHEMAS = {
    'model_set_params': {
        'type': 'object',
        'properties': {
            'part': _PART,
            'params': _PARAMS,
            'remove': {'type': 'array', 'items': {'type': 'string'}},
            'placement': {'type': ['string', 'null'],
                          'description': 'build123d Location expression applied to the finished part '
                                         '(from mesh_frame for tilted meshes); null clears it.'},
            'verify': _VERIFY,
        },
    },
    'model_add_feature': {
        'type': 'object',
        'properties': {
            'part': _PART,
            'name': {'type': 'string', 'description': 'Short feature name, e.g. base plate, mounting holes.'},
            'code': {'type': 'string', 'description': 'build123d code run inside build_<part>(). Read every '
                                                      'dimension as P.name (no numeric literals except 0, 1, '
                                                      '2, 0.5) and update part (part = ..., part -= ...).'},
            'evidence': {'type': 'string', 'description': 'Which slices/views or design rules this feature '
                                                          'comes from.'},
            'before': {'type': 'string', 'description': 'Insert before this feature id instead of appending.'},
            'verify': _VERIFY,
        },
        'required': ['name', 'code', 'evidence'],
    },
    'model_edit_feature': {
        'type': 'object',
        'properties': {
            'part': _PART,
            'id': {'type': 'string', 'description': 'Feature id such as F2.'},
            'code': {'type': 'string'}, 'name': {'type': 'string'}, 'evidence': {'type': 'string'},
            'remove': {'type': 'boolean', 'description': 'Delete the feature.'},
            'verify': _VERIFY,
        },
        'required': ['id'],
    },
    'model_undo': {'type': 'object', 'properties': {'part': _PART}},
    'model_status': {'type': 'object', 'properties': {'part': _PART}},
    'model_report': {'type': 'object', 'properties': {'part': _PART}},
    'workspace_add_part': {
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'description': 'Python identifier, e.g. plate, case, keycap_stem.'},
            'description': {'type': 'string', 'description': 'What the part is and how it fits the others.'},
        },
        'required': ['name', 'description'],
    },
    'workspace_set_active': {'type': 'object', 'properties': {'part': {'type': 'string'}},
                             'required': ['part']},
    'workspace_status': {'type': 'object', 'properties': {}},
    'assembly_set_params': {
        'type': 'object',
        'properties': {'params': _PARAMS, 'remove': {'type': 'array', 'items': {'type': 'string'}}},
    },
    'assembly_add_feature': {
        'type': 'object',
        'properties': {
            'name': {'type': 'string', 'description': 'Short layout step name, e.g. number row, plate.'},
            'code': {'type': 'string', 'description': (
                'build123d code run inside build_assembly() that calls place(part, location, name=None, '
                '**overrides) for each instance. location is a build123d Location (Pos(...), '
                'Pos(...) * Rot(...)); overrides change that part\'s parameters for this instance '
                '(width_u=P.widths[i]). Read every number from P, like part features.')},
            'evidence': {'type': 'string', 'description': 'Design rule or measurement behind this layout.'},
            'before': {'type': 'string', 'description': 'Insert before this feature id instead of appending.'},
        },
        'required': ['name', 'code', 'evidence'],
    },
    'assembly_edit_feature': {
        'type': 'object',
        'properties': {
            'id': {'type': 'string', 'description': 'Layout feature id such as F2.'},
            'code': {'type': 'string'}, 'name': {'type': 'string'}, 'evidence': {'type': 'string'},
            'remove': {'type': 'boolean', 'description': 'Delete the feature.'},
        },
        'required': ['id'],
    },
    'assembly_undo': {'type': 'object', 'properties': {}},
    'assembly_check': {
        'type': 'object',
        'properties': {
            'a': {'type': 'string', 'description': 'Instance name (as given to place(), or <part>_<n>).'},
            'b': {'type': 'string', 'description': 'Second instance name.'},
        },
        'required': ['a', 'b'],
    },
}

DESCRIPTIONS = {
    'model_set_params': (
        'Add, update or remove a part\'s named parameters (with source and evidence), or set its '
        'placement. Rebuilds part_<name> from its whole script and, if the part has a reference mesh, '
        'checks it against the mesh.'),
    'model_add_feature': (
        'Add a feature (a block of build123d code) to a part. The part\'s whole script is rebuilt as '
        'part_<name>; a failed build is rolled back and names the failing feature. Returns the size, the '
        'deviation from the reference mesh (parts with one), and whether it got better or worse.'),
    'model_edit_feature': 'Change or remove a feature of a part, then rebuild and check.',
    'model_undo': 'Revert the last change to a part and rebuild it from the restored script.',
    'model_status': 'Show a part\'s parameters, features, latest deviation, history and full script.',
    'model_report': (
        'Write the part\'s report.md (verdict, parameters with sources and evidence, features, revision '
        'history) and return it. Use when a part is done or the user asks for a summary.'),
    'workspace_add_part': (
        'Create a new, empty part with no reference mesh (a plate, a case, a bracket) and make it the '
        'active part. Then build it with model_set_params and model_add_feature.'),
    'workspace_set_active': 'Make a part the default for model_* and mesh tools.',
    'workspace_status': (
        'List the parts (reference mesh or not, revision, features, parameters, verdict) and the '
        'assembly (instances per part, variants, layout features).'),
    'assembly_set_params': (
        'Add, update or remove assembly parameters: pitches, counts, offsets, and lists such as the key '
        'widths of a row. The layout rebuilds with them.'),
    'assembly_add_feature': (
        'Add a layout step to the assembly: code that calls place(part, location, **overrides). Each '
        'distinct part + overrides is built once and copied, so many identical instances are cheap. '
        'The assembly rebuilds whenever a part it uses changes. Returns instance counts and variants.'),
    'assembly_edit_feature': 'Change or remove a layout step of the assembly, then rebuild it.',
    'assembly_undo': 'Revert the last assembly change and rebuild it.',
    'assembly_check': (
        'Check how two placed instances fit: overlap volume (0 when they do not intersect) and minimum '
        'distance. Use it on a few representative pairs, e.g. a keycap and the plate.'),
}

PART_OPERATIONS = {'model_set_params', 'model_add_feature', 'model_edit_feature', 'model_undo'}
CHANGES = PART_OPERATIONS | {'assembly_set_params', 'assembly_add_feature', 'assembly_edit_feature',
                             'assembly_undo', 'workspace_add_part'}


def _text(value):
    return {'content': [{'type': 'text', 'text': value if isinstance(value, str)
                         else json.dumps(value, indent=1, default=str)}]}


@asynccontextmanager
async def _one_snapshot(cad):
    """Builds inside one tool call (a part, then the assembly using it) do not each
    refresh the viewer; the caller snapshots once afterwards."""
    paused = getattr(cad, 'paused_snapshots', None)
    if paused is None:
        yield
        return
    cad.paused_snapshots = paused + 1
    try:
        yield
    finally:
        cad.paused_snapshots -= 1


CHECK_MARK = 'AIDA_CHECK '


def _check_script(a, b):
    return f'''import json
_by_label = {{c.label: c for c in assembly.children}}
for _name in ({a!r}, {b!r}):
    if _name not in _by_label:
        raise ValueError(f'No instance {{_name!r}}; instances include {{sorted(_by_label)[:12]}}')
_a, _b = _by_label[{a!r}], _by_label[{b!r}]
_common = _a & _b
_overlap = sum(s.volume for s in _common.solids()) if _common is not None else 0.0
print({CHECK_MARK!r} + json.dumps({{'a': {a!r}, 'b': {b!r}, 'overlap_volume': _overlap,
                                   'min_distance': _a.distance_to(_b)}}))
'''


def model_tools(cad, artifacts, workspace=None):
    """Return the workspace and its tool table: [(name, description, schema, async handler)]."""
    workspace = workspace or Workspace.load(cad, artifacts)
    assembly = workspace.assembly

    async def changed_part(model, result):
        note = await workspace.after_part_change(model.name)
        if note:
            result['assembly'] = note
        return result

    async def set_params(args):
        model = workspace.part(args.get('part'))
        result = await model.set_params(args.get('params'), args.get('remove') or (),
                                        args['placement'] if 'placement' in args else ...,
                                        args.get('verify', True))
        return await changed_part(model, result)

    async def add_feature(args):
        model = workspace.part(args.get('part'))
        result = await model.add_feature(args.get('name'), args.get('code'), args.get('evidence', ''),
                                         args.get('before'), args.get('verify', True))
        return await changed_part(model, result)

    async def edit_feature(args):
        model = workspace.part(args.get('part'))
        result = await model.edit_feature(args.get('id'), args.get('code'), args.get('name'),
                                          args.get('evidence'), bool(args.get('remove')),
                                          args.get('verify', True))
        return await changed_part(model, result)

    async def undo(args):
        model = workspace.part(args.get('part'))
        return await changed_part(model, await model.undo())

    async def status(args):
        return workspace.part(args.get('part')).status()

    async def report(args):
        return workspace.part(args.get('part')).report(getattr(cad.mesh, 'units', None))

    async def add_part(args):
        return workspace.add_part(args.get('name'), args.get('description', ''))

    async def set_active(args):
        return workspace.set_active(args.get('part'))

    async def workspace_status(args):
        return workspace.status()

    async def assembly_set_params(args):
        return await assembly.set_params(args.get('params'), args.get('remove') or ())

    async def assembly_add_feature(args):
        return await assembly.add_feature(args.get('name'), args.get('code'), args.get('evidence', ''),
                                          args.get('before'), verify=False)

    async def assembly_edit_feature(args):
        return await assembly.edit_feature(args.get('id'), args.get('code'), args.get('name'),
                                           args.get('evidence'), bool(args.get('remove')), verify=False)

    async def assembly_undo(args):
        return await assembly.undo()

    async def assembly_check(args):
        if not assembly.layout['instances']:
            raise ModelError('The assembly places nothing yet')
        result = await cad.call('execute', {'code': _check_script(str(args.get('a')), str(args.get('b')))})
        text = text_of(result)
        for line in reversed(text.splitlines()):
            if line.startswith(CHECK_MARK):
                return json.loads(line[len(CHECK_MARK):])
        raise ModelError(f'Check failed: {text.strip()[:800]}')

    operations = {'model_set_params': set_params, 'model_add_feature': add_feature,
                  'model_edit_feature': edit_feature, 'model_undo': undo,
                  'model_status': status, 'model_report': report,
                  'workspace_add_part': add_part, 'workspace_set_active': set_active,
                  'workspace_status': workspace_status,
                  'assembly_set_params': assembly_set_params, 'assembly_add_feature': assembly_add_feature,
                  'assembly_edit_feature': assembly_edit_feature, 'assembly_undo': assembly_undo,
                  'assembly_check': assembly_check}
    table = []
    for name, operation in operations.items():
        async def handler(args, name=name, operation=operation):
            artifacts.log('tool_call', {'name': name, 'arguments': args})
            ok = False
            try:
                async with _one_snapshot(cad):
                    result = _text(await operation(args))
                ok = True
            except (ModelError, ValueError, RuntimeError) as exc:
                result = {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
            if ok and name in CHANGES and getattr(cad, 'on_execute', None) is not None:
                try:
                    await cad.on_execute()
                except Exception as exc:  # a missed viewer refresh never fails the tool
                    artifacts.log('snapshot_error', {'error': str(exc)})
            artifacts.log('tool_result', {'name': name, **result})
            return result
        table.append((name, DESCRIPTIONS[name], SCHEMAS[name], handler))
    return workspace, table
