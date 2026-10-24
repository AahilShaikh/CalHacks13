"""Agent-facing tools for the parametric model (see model.py)."""
import json

from .model import SOURCES, ModelError, ParametricModel

_PARAM_VALUE = {
    'anyOf': [
        {'type': 'number'},
        {'type': 'string'},
        {'type': 'object', 'properties': {
            'value': {'anyOf': [{'type': 'number'}, {'type': 'string'}]},
            'source': {'type': 'string', 'enum': list(SOURCES),
                       'description': 'measured: from slice/series/frame numbers; estimated: read off '
                                      'images; assumed: design intent such as rounding or symmetry.'},
            'evidence': {'type': 'string', 'description': 'Which tool result supports the value.'},
            'measured': {'type': 'number', 'description': 'Raw measured value when value is rounded.'},
            'note': {'type': 'string'},
        }},
    ],
}
_VERIFY = {'type': 'boolean', 'description': 'Check against the mesh after rebuilding (default true).'}

SCHEMAS = {
    'model_set_params': {
        'type': 'object',
        'properties': {
            'params': {'type': 'object', 'additionalProperties': _PARAM_VALUE,
                       'description': 'name: value, or name: {value, source, evidence, measured, note}. '
                                      'Updating only metadata keeps the value.'},
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
            'name': {'type': 'string', 'description': 'Short feature name, e.g. base plate, mounting holes.'},
            'code': {'type': 'string', 'description': 'build123d code run inside build(). Read parameters '
                                                      'as P.name and update part (part = ..., part -= ...).'},
            'evidence': {'type': 'string', 'description': 'Which slices/views this feature comes from.'},
            'before': {'type': 'string', 'description': 'Insert before this feature id instead of appending.'},
            'verify': _VERIFY,
        },
        'required': ['name', 'code'],
    },
    'model_edit_feature': {
        'type': 'object',
        'properties': {
            'id': {'type': 'string', 'description': 'Feature id such as F2.'},
            'code': {'type': 'string'}, 'name': {'type': 'string'}, 'evidence': {'type': 'string'},
            'remove': {'type': 'boolean', 'description': 'Delete the feature.'},
            'verify': _VERIFY,
        },
        'required': ['id'],
    },
    'model_undo': {'type': 'object', 'properties': {}},
    'model_status': {'type': 'object', 'properties': {}},
    'model_report': {'type': 'object', 'properties': {}},
}

DESCRIPTIONS = {
    'model_set_params': (
        'Add, update or remove named model parameters (with source and evidence), or set the part '
        'placement. Rebuilds generated_cad from the whole script and checks it against the mesh.'),
    'model_add_feature': (
        'Add a feature (a block of build123d code) to the parametric model. The full script is rebuilt '
        'as generated_cad; a failed build is rolled back and names the failing feature. Returns the '
        'size, deviation from the mesh, and whether it got better or worse than the last revision.'),
    'model_edit_feature': 'Change or remove a feature of the parametric model, then rebuild and check.',
    'model_undo': 'Revert the last model change and rebuild generated_cad from the restored script.',
    'model_status': 'Show the model parameters, features, latest deviation, history and full script.',
    'model_report': (
        'Write report.md (verdict, parameters with sources and evidence, features, revision history) '
        'and return it. Use when the reconstruction is done or the user asks for a summary.'),
}


def _text(value):
    return {'content': [{'type': 'text', 'text': value if isinstance(value, str)
                         else json.dumps(value, indent=1, default=str)}]}


def model_tools(cad, artifacts):
    """Return the model and its tool table: [(name, description, schema, async handler)]."""
    model = ParametricModel(cad, artifacts)

    async def set_params(args):
        return await model.set_params(args.get('params'), args.get('remove') or (),
                                      args['placement'] if 'placement' in args else ...,
                                      args.get('verify', True))

    async def add_feature(args):
        return await model.add_feature(args.get('name'), args.get('code'), args.get('evidence', ''),
                                       args.get('before'), args.get('verify', True))

    async def edit_feature(args):
        return await model.edit_feature(args.get('id'), args.get('code'), args.get('name'),
                                        args.get('evidence'), bool(args.get('remove')),
                                        args.get('verify', True))

    async def undo(args):
        return await model.undo()

    async def status(args):
        return model.status()

    async def report(args):
        return model.report(cad.mesh.units)

    operations = {'model_set_params': set_params, 'model_add_feature': add_feature,
                  'model_edit_feature': edit_feature, 'model_undo': undo,
                  'model_status': status, 'model_report': report}
    table = []
    for name, operation in operations.items():
        async def handler(args, name=name, operation=operation):
            artifacts.log('tool_call', {'name': name, 'arguments': args})
            try:
                result = _text(await operation(args))
            except (ModelError, ValueError, RuntimeError) as exc:
                result = {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
            artifacts.log('tool_result', {'name': name, **result})
            return result
        table.append((name, DESCRIPTIONS[name], SCHEMAS[name], handler))
    return model, table
