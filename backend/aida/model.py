"""A parametric part rebuilt from scratch on every change.

A part is a list of named parameters (each with its source and evidence) and an
ordered list of feature code blocks. Every change regenerates one build123d
script, runs it in the CAD session as ``part_<name>``, and, when the part has a
reference mesh, checks the result against it. Failed builds are rolled back, and
every successful revision can be undone, so the agent never has to repair a
half-built session by hand.

The script defines ``build_<name>(**overrides)`` and registers it in the CAD
session's ``PARTS`` table, so an assembly can build variants of the part with
some parameters changed (a 2.25u keycap from a 1u keycap).
"""
import ast
import copy
import json
import keyword
import re
import textwrap

from .cad import failed, text_of

SOURCES = ('measured', 'estimated', 'assumed')
INDENT = '    '
# Literals that are arithmetic, not dimensions (halving a diameter, doubling a depth).
PLAIN_NUMBERS = {0, 1, 2, 0.5}


class ModelError(ValueError):
    """The requested model change is invalid; nothing was changed."""


def _empty_state():
    return {'params': {}, 'features': [], 'placement': None, 'next_id': 1, 'revision': 0,
            'deviation': None, 'history': []}


def object_name(part):
    """The CAD session object a part is shown as."""
    return f'part_{part}'


def _check_name(name, what='Parameter name'):
    if not isinstance(name, str) or not name.isidentifier() or keyword.iskeyword(name) \
            or name.startswith('_'):
        raise ModelError(f'{what} {name!r} must be a Python identifier not starting with _')


def _check_value(name, value):
    """Numbers, strings, and lists of them (a keyboard row of key widths)."""
    if isinstance(value, list):
        for item in value:
            _check_value(name, item)
        return
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ModelError(f'Parameter {name} must be a number, string or list, not {type(value).__name__}')
    if isinstance(value, float) and value != value:
        raise ModelError(f'Parameter {name} is NaN')


def _param_entry(name, raw, previous=None):
    if previous is None and not (isinstance(raw, dict) and raw.get('source') in SOURCES):
        raise ModelError(f'New parameter {name} needs {{value, source, evidence}}; source is one of '
                         f'{SOURCES}. A bare value only updates an existing parameter.')
    entry = dict(previous or {'source': 'assumed', 'evidence': '', 'measured': None, 'note': ''})
    if isinstance(raw, dict):
        unknown = set(raw) - {'value', 'source', 'evidence', 'measured', 'note'}
        if unknown:
            raise ModelError(f'Parameter {name}: unknown fields {sorted(unknown)}')
        if 'value' not in raw and previous is None:
            raise ModelError(f'Parameter {name} needs a value')
        entry.update({k: v for k, v in raw.items() if k != 'value'})
        value = raw.get('value', entry.get('value'))
    else:
        value = raw
    _check_value(name, value)
    if entry['source'] not in SOURCES:
        raise ModelError(f'Parameter {name}: source must be one of {SOURCES}')
    if entry['measured'] is not None and not isinstance(entry['measured'], (int, float)):
        raise ModelError(f'Parameter {name}: measured must be a number')
    entry['value'] = value
    return entry


def _outside_functions(tree):
    """Nodes of the feature's own body, not inside helper functions it defines (whose return is fine)."""
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                stack.append(child)


def check_feature_code(code, first, show_name='the part'):
    """Reject feature code that cannot sit inside build(); return its used P.names."""
    if not isinstance(code, str) or not code.strip():
        raise ModelError('Feature code is empty')
    code = textwrap.dedent(code)
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ModelError(f'Feature code has a syntax error on its line {exc.lineno}: {exc.msg}') from None
    assigns_part = False
    for node in _outside_functions(tree):
        if isinstance(node, (ast.Return, ast.Global, ast.Nonlocal)):
            raise ModelError('Feature code must not use return, global or nonlocal outside a helper '
                             'function; assign part instead')
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == 'build':
            raise ModelError('Feature code must not define build()')
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ModelError('Feature code must not import; build123d is already imported')
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'show':
            raise ModelError(f'Feature code must not call show(); the model shows {show_name} itself')
        if isinstance(node, ast.Name) and node.id == 'part' and isinstance(node.ctx, ast.Store):
            assigns_part = True
    if first and not assigns_part:
        raise ModelError('The first feature must assign part (for example part = extrude(...))')
    literals = sorted({node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                       and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
                       and node.value not in PLAIN_NUMBERS})
    if literals:
        shown = ', '.join(f'{v:g}' for v in literals[:12])
        raise ModelError(f'Feature hard-codes numbers ({shown}). Record each dimension, angle or count '
                         'with model_set_params and use P.name in the code, so the model stays parametric.')
    used = sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                   and isinstance(n.value, ast.Name) and n.value.id == 'P'})
    return code.rstrip('\n'), used


def _defaults_lines(state, defaults):
    lines = [f'{defaults} = {{']
    for name, entry in state['params'].items():
        comment = f"  # {entry['source']}" + (f": {entry['evidence']}" if entry['evidence'] else '')
        lines.append(f"{INDENT}{name!r}: {entry['value']!r},{comment}".replace('\n', ' '))
    return lines + ['}']


def _feature_lines(state, lines, indent=INDENT):
    """Append each feature's code to ``lines``; return {feature_id: (first_line, last_line)}."""
    spans = {}
    for feature in state['features']:
        header = f"{indent}# {feature['id']} {feature['name']}"
        if feature['evidence']:
            header += f" | evidence: {feature['evidence']}".replace('\n', ' ')
        lines.append(header)
        start = len(lines) + 1
        lines += [indent + line if line.strip() else '' for line in feature['code'].splitlines()]
        spans[feature['id']] = (start, len(lines))
    return spans


PARTS_TABLE = ['try:', f'{INDENT}PARTS', 'except NameError:', f'{INDENT}PARTS = {{}}']


def generate_script(state, name='part', description=''):
    """Return (script, {feature_id: (first_line, last_line)}) for a part's state."""
    defaults = f'DEFAULTS_{name}'
    lines = ['from build123d import *', 'import math', '', '']
    lines += _defaults_lines(state, defaults)
    doc = f'{name}: {description}' if description else name
    lines += ['', '', f'def build_{name}(**overrides):', f'{INDENT}{doc!r}',
              f'{INDENT}unknown = sorted(set(overrides) - set({defaults}))',
              f'{INDENT}if unknown:',
              f"{INDENT * 2}raise ValueError(f'{name} has no parameters {{unknown}}')",
              f"{INDENT}P = type('P', (), {{**{defaults}, **overrides}})",
              f'{INDENT}part = None']
    spans = _feature_lines(state, lines)
    if state['placement']:
        lines.append(f"{INDENT}part = ({state['placement']}) * part")
    lines += [f'{INDENT}return part', '', '', *PARTS_TABLE, f'PARTS[{name!r}] = build_{name}',
              f'part = build_{name}()',
              "if part is None or not hasattr(part, 'volume'):",
              f"{INDENT}raise ValueError('build_{name}() left no solid in part')",
              f'show(part, "{object_name(name)}")', '']
    return '\n'.join(lines), spans


def deviation_summary(report):
    """The few numbers worth carrying between revisions."""
    ref, cad = report['reference_to_cad'], report['cad_to_reference']
    summary = {'verdict': report['verdict'], 'threshold': report['threshold'],
               'p95': max(ref['p95_abs'], cad['p95_abs']),
               'max': max(ref['max_abs'], cad['max_abs']),
               'share_within': min(ref['share_within_threshold'], cad['share_within_threshold']),
               'missing_share': ref['share_cad_missing_material'],
               'extra_share': ref['share_cad_extra_material'],
               'worst_regions': [{k: r[k] for k in ('kind', 'center', 'bbox_min', 'bbox_max', 'max_deviation')}
                                 for r in report['regions'][:3]]}
    if 'volume' in report:
        summary['volume_ratio'] = report['volume']['cad_over_reference']
    return summary


def trend(before, after):
    """'first', 'better', 'worse', 'mixed' or 'same' between two deviation summaries."""
    if before is None:
        return 'first'
    gained = after['share_within'] - before['share_within']
    p95_change = after['p95'] - before['p95']
    better = gained > 0.02 or p95_change < -0.1 * after['threshold']
    worse = gained < -0.02 or p95_change > 0.1 * after['threshold']
    if better and worse:
        return 'mixed'
    return 'better' if better else 'worse' if worse else 'same'


def check_placement(placement):
    if placement is None or placement == '':
        return None
    if not isinstance(placement, str):
        raise ModelError('placement must be a build123d expression string, e.g. Location((0, 0, 5), (0, 0, 30))')
    try:
        ast.parse(placement, mode='eval')
    except SyntaxError:
        raise ModelError(f'placement is not a single expression: {placement!r}') from None
    return placement.strip()


class ParametricModel:
    """One part. ``reference`` is its mesh (an STL path) or None for a part designed
    from scratch, which is built but never checked against a mesh."""

    # The first feature must create ``part``; the assembly has no such rule.
    requires_part = True
    tool_prefix = 'model'
    unchecked = 'not checked (no reference mesh)'

    def __init__(self, cad, artifacts, name='part', reference=None, description=''):
        _check_name(name, 'Part name')
        self.cad = cad
        self.artifacts = artifacts
        self.name = name
        self.reference = reference
        self.description = description
        self.object = object_name(name)
        self.state = _empty_state()
        self.undo_stack = []
        self.last_output = ''
        # Called with no arguments after every saved revision (the workspace
        # rewrites its combined script then).
        self.on_saved = None

    @property
    def folder(self):
        return self.artifacts.root / 'parts' / self.name

    def script(self):
        return generate_script(self.state, self.name, self.description)

    # ---------------------------------------------------------- persistence

    def _save(self, script):
        folder = self.folder
        history = folder / 'model-history'
        history.mkdir(parents=True, exist_ok=True)
        (folder / 'model.py').write_text(script, encoding='utf-8')
        (folder / 'model.json').write_text(json.dumps(self.state, indent=2), encoding='utf-8')
        (history / f"model-r{self.state['revision']:03}.py").write_text(script, encoding='utf-8')
        if self.on_saved is not None:
            self.on_saved()

    def load_saved(self):
        """Take the state saved in model.json by an earlier session, if any. True when loaded.
        The undo stack is not saved, so a reopened part starts with nothing to undo."""
        path = self.folder / 'model.json'
        if not path.is_file():
            return False
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return False
        if not isinstance(state, dict) or not isinstance(state.get('features'), list):
            return False
        self.state = {**_empty_state(), **state}
        return True

    async def restore(self):
        """Rebuild a loaded part in the CAD engine (no new revision). None, or why it failed."""
        if not self.state['features']:
            return None
        try:
            await self._build()
        except Exception as exc:
            return f'{self.name} could not be rebuilt: {str(exc)[:400]}'
        return None

    # ---------------------------------------------------------- building

    async def _feature_at(self, spans, result):
        """Name the feature whose code raised, using the server's last_error line."""
        line = None
        try:
            detail = json.loads(text_of(await self.cad.call('last_error', {})))
            line = detail.get('line') if isinstance(detail, dict) else None
        except (ValueError, TypeError, RuntimeError):
            line = None
        if line is None:
            match = re.search(r'<mcp>, line (\d+)', text_of(result))
            line = int(match.group(1)) if match else None
        if line is None:
            return None
        for feature_id, (start, end) in spans.items():
            if start <= line <= end:
                return feature_id
        return None

    async def _build(self):
        script, spans = self.script()
        result = await self.cad.call('execute', {'code': script})
        self.last_output = text_of(result)
        if failed(result):
            culprit = await self._feature_at(spans, result)
            names = {f['id']: f['name'] for f in self.state['features']}
            where = f"in {culprit} ({names[culprit]})" if culprit else 'in the generated script'
            raise ModelError(f'Build failed {where}: {text_of(result).strip()[:1500]}')
        measure = await self.cad.call('measure', {'object_name': self.object})
        try:
            report = json.loads(text_of(measure))
            size = {'volume': report.get('volume'), 'bbox': report.get('bbox')}
        except ValueError:
            size = {'measure': text_of(measure)[:500]}
        return script, size

    async def _verify(self, samples):
        stats = await self.cad.mesh.stats(self.object, samples, reference=self.reference)
        return deviation_summary(stats)

    async def _commit(self, action, mutate, verify=True, samples=8000):
        """Apply ``mutate`` to a copy of the state, rebuild, verify, and keep it on success."""
        previous = copy.deepcopy(self.state)
        try:
            mutate(self.state)
            if self.state['features']:
                script, size = await self._build()
            else:  # parameters only: nothing to build or check yet
                script, size, verify = self.script()[0], None, False
        except Exception:
            self.state = previous
            raise
        self.state['revision'] += 1
        before = previous['deviation']
        deviation, change = None, 'not checked'
        if self.reference is None:
            verify, change = False, self.unchecked
        if verify:
            try:
                deviation = await self._verify(samples)
                change = trend(before, deviation)
            except (ValueError, RuntimeError) as exc:
                change = f'check failed: {exc}'
        self.state['deviation'] = deviation if verify else previous['deviation']
        self.state['history'].append({'revision': self.state['revision'], 'action': action,
                                      'deviation': deviation, 'trend': change})
        self.undo_stack.append(previous)
        self._save(script)
        self.artifacts.log('model_revision', {'revision': self.state['revision'], 'action': action,
                                              'trend': change})
        out = {'part': self.name, 'revision': self.state['revision'], 'action': action, 'size': size,
               'deviation': deviation, 'trend': change,
               'features': [f"{f['id']} {f['name']}" for f in self.state['features']]}
        if change == 'worse':
            out['hint'] = f'Deviation got worse; {self.tool_prefix}_undo reverts this revision.'
        return out

    # ---------------------------------------------------------- operations

    async def set_params(self, params=None, remove=(), placement=..., verify=True):
        params = params or {}
        if not isinstance(params, dict):
            raise ModelError('params must be an object of name: value or name: {value, source, ...}')
        for name in list(params) + list(remove):
            _check_name(name)
        if not params and not remove and placement is ...:
            raise ModelError('Nothing to change')
        if placement is not ...:
            placement = check_placement(placement)
        used = {u for f in self.state['features'] for u in f['used_params']}
        blocked = sorted(set(remove) & used)
        if blocked:
            raise ModelError(f'Parameters still used by features: {blocked}')

        def mutate(state):
            for name, raw in params.items():
                state['params'][name] = _param_entry(name, raw, state['params'].get(name))
            for name in remove:
                state['params'].pop(name, None)
            if placement is not ...:
                state['placement'] = placement

        changed = sorted(params) + [f'-{n}' for n in remove] + (['placement'] if placement is not ... else [])
        return await self._commit(f"set params {', '.join(changed)}", mutate, verify)

    async def add_feature(self, name, code, evidence='', before=None, verify=True):
        if not isinstance(name, str) or not name.strip():
            raise ModelError('Give the feature a short name')
        if not isinstance(evidence, str) or not evidence.strip():
            raise ModelError('Give evidence: which slice, series, frame or view this feature comes from')
        features = self.state['features']
        first = not features or (before is not None and before == features[0]['id'])
        code, used = check_feature_code(code, first=first and self.requires_part, show_name=self.object)
        missing = sorted(set(used) - set(self.state['params']))
        if missing:
            raise ModelError(f'Feature uses undefined parameters {missing}; add them with model_set_params')
        ids = [f['id'] for f in self.state['features']]
        if before is not None and before not in ids:
            raise ModelError(f'No feature {before}; features are {ids}')

        def mutate(state):
            feature = {'id': f"F{state['next_id']}", 'name': name.strip(), 'code': code,
                       'evidence': evidence or '', 'used_params': used}
            state['next_id'] += 1
            index = ids.index(before) if before is not None else len(state['features'])
            state['features'].insert(index, feature)

        return await self._commit(f'add {name.strip()}', mutate, verify)

    async def edit_feature(self, feature_id, code=None, name=None, evidence=None, remove=False,
                           verify=True):
        ids = [f['id'] for f in self.state['features']]
        if feature_id not in ids:
            raise ModelError(f'No feature {feature_id}; features are {ids}')
        index = ids.index(feature_id)
        if remove:
            if len(ids) == 1:
                raise ModelError('Cannot remove the only feature')
            if index == 0:
                check_feature_code(self.state['features'][1]['code'], first=self.requires_part,
                                   show_name=self.object)
        used = None
        if code is not None:
            code, used = check_feature_code(code, first=index == 0 and self.requires_part,
                                            show_name=self.object)
            missing = sorted(set(used) - set(self.state['params']))
            if missing:
                raise ModelError(f'Feature uses undefined parameters {missing}')

        def mutate(state):
            if remove:
                del state['features'][index]
                return
            feature = state['features'][index]
            if code is not None:
                feature['code'], feature['used_params'] = code, used
            if name:
                feature['name'] = name.strip()
            if evidence is not None:
                feature['evidence'] = evidence

        action = f'remove {feature_id}' if remove else f'edit {feature_id}'
        return await self._commit(action, mutate, verify)

    async def undo(self):
        if not self.undo_stack:
            raise ModelError('Nothing to undo')
        current = self.state
        self.state = self.undo_stack.pop()
        try:
            if self.state['features']:
                script, size = await self._build()
            else:
                script, size = self.script()[0], None
        except Exception:
            self.undo_stack.append(self.state)
            self.state = current
            raise
        self.state['history'] = current['history'] + [
            {'revision': self.state['revision'], 'action': f"undo to revision {self.state['revision']}",
             'deviation': self.state['deviation'], 'trend': 'restored'}]
        self._save(script)
        return {'part': self.name, 'revision': self.state['revision'], 'size': size,
                'deviation': self.state['deviation'],
                'features': [f"{f['id']} {f['name']}" for f in self.state['features']],
                'note': f'{self.object} was rebuilt from the restored script.'}

    def snapshot(self):
        """Compact state for the UI's model panel: parameters with provenance, features in
        build order, and the revision history with each check's deviation."""
        state = self.state

        def brief(deviation):
            if not deviation:
                return None
            return {k: deviation.get(k) for k in ('verdict', 'threshold', 'p95', 'max', 'share_within')}

        return {
            'name': self.name,
            'object': self.object,
            'description': self.description,
            'has_reference': self.reference is not None,
            'revision': state['revision'],
            'params': {name: {k: entry.get(k) for k in ('value', 'source', 'evidence', 'measured', 'note')}
                       for name, entry in state['params'].items()},
            'features': [{k: f[k] for k in ('id', 'name', 'evidence', 'used_params', 'code')}
                         for f in state['features']],
            'placement': state['placement'],
            'deviation': brief(state['deviation']),
            'history': [{'revision': h['revision'], 'action': h['action'], 'trend': h['trend'],
                         'deviation': brief(h['deviation'])} for h in state['history']],
            'can_undo': bool(self.undo_stack),
        }

    def status(self):
        script, _ = self.script()
        return {'part': self.name, 'object': self.object, 'has_reference': self.reference is not None,
                'revision': self.state['revision'], 'params': self.state['params'],
                'features': [{k: f[k] for k in ('id', 'name', 'evidence', 'used_params')}
                             for f in self.state['features']],
                'placement': self.state['placement'], 'deviation': self.state['deviation'],
                'history': self.state['history'][-10:], 'script': script}

    # ---------------------------------------------------------- report

    def report(self, units=None):
        """Write report.md beside the part's model.py and return its text."""
        unit = units or 'model units'
        state = self.state
        lines = [f"# {self.name}: report (revision {state['revision']})", '']
        deviation = state['deviation']
        if deviation:
            lines += [f"**Verdict: {deviation['verdict']}** at threshold {deviation['threshold']:.3g} {unit}. "
                      f"95% of surface within {deviation['p95']:.3g} {unit}; worst {deviation['max']:.3g} {unit}; "
                      f"{deviation['share_within']:.0%} of samples within threshold."
                      + (f" CAD volume is {deviation['volume_ratio']:.3f}x the reference."
                         if deviation.get('volume_ratio') else ''), '']
            if deviation['worst_regions']:
                lines += ['Largest remaining differences:', '']
                for region in deviation['worst_regions']:
                    center = ', '.join(f'{c:.2f}' for c in region['center'])
                    lines.append(f"- {region['kind'].replace('_', ' ')} near ({center}), "
                                 f"up to {region['max_deviation']:.3g} {unit}")
                lines.append('')
        elif self.reference is None:
            lines += ['Designed without a reference mesh, so there is no deviation check.', '']
        else:
            lines += ['The current revision has not been checked against the mesh.', '']
        lines += ['## Parameters', '', f'| Name | Value ({unit}) | Source | Measured | Evidence |',
                  '|---|---|---|---|---|']
        for name, entry in state['params'].items():
            measured = '' if entry['measured'] is None else f"{entry['measured']:.4g}"
            evidence = (entry['evidence'] + (f" ({entry['note']})" if entry['note'] else '')).replace('|', '/')
            lines.append(f"| {name} | {entry['value']} | {entry['source']} | {measured} | {evidence} |")
        counts = {s: sum(e['source'] == s for e in state['params'].values()) for s in SOURCES}
        lines += ['', f"{counts['measured']} measured, {counts['estimated']} estimated, "
                      f"{counts['assumed']} assumed.", '', '## Features', '']
        for feature in state['features']:
            uses = ', '.join(feature['used_params']) or 'no parameters'
            lines.append(f"{feature['id']}. **{feature['name']}** uses {uses}."
                         + (f" Evidence: {feature['evidence']}" if feature['evidence'] else ''))
        lines += ['', '## Revision history', '', '| Rev | Change | p95 | Within | Trend |', '|---|---|---|---|---|']
        for entry in state['history']:
            d = entry['deviation']
            p95 = f"{d['p95']:.3g}" if d else ''
            within = f"{d['share_within']:.0%}" if d else ''
            lines.append(f"| {entry['revision']} | {entry['action']} | {p95} | {within} | {entry['trend']} |")
        lines += ['', f'The model script is parts/{self.name}/model.py in this session folder.', '']
        text = '\n'.join(lines)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / 'report.md').write_text(text, encoding='utf-8')
        return text
