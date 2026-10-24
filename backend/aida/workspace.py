"""A workspace: several parametric parts plus an assembly that places them.

Each part is a :class:`~aida.model.ParametricModel` with its own parameters,
features, history and (optionally) reference mesh. The assembly is a parametric
model too, but its features place parts instead of shaping one:

    for i in range(P.keys):
        place('keycap', Pos(i * P.pitch, 0, P.cap_z), width_u=P.widths[i])

``place`` takes a part name, a build123d Location, an optional instance name and
parameter overrides. Every distinct (part, overrides) pair is built once (a
*variant*) and every placement becomes an *instance* that moves a copy of it, so
sixty keycaps of four widths build four keycaps.
"""
import json
from collections import Counter
from pathlib import Path

from .model import INDENT, PARTS_TABLE, ModelError, ParametricModel, _check_name, _defaults_lines, \
    _feature_lines

ASSEMBLY = 'assembly'
LAYOUT_MARK = 'AIDA_LAYOUT '
WORKSPACE_FILE = 'workspace.json'
COMBINED_SCRIPT = 'workspace_model.py'


def generate_assembly_script(state):
    """Return (script, spans) for the assembly: run after the part scripts, which fill PARTS."""
    lines = ['from build123d import *', 'import json', '', '']
    lines += _defaults_lines(state, 'DEFAULTS_assembly')
    lines += ['', '', 'def build_assembly():',
              f"{INDENT}P = type('P', (), dict(DEFAULTS_assembly))",
              f'{INDENT}placed = []', '',
              f'{INDENT}def place(part, location=None, name=None, **overrides):',
              f'{INDENT * 2}placed.append((part, location, name, overrides))', '']
    spans = _feature_lines(state, lines)
    lines += [f'{INDENT}return placed', '', '', '''def _assemble(placed):
    if not placed:
        raise ValueError('The assembly places no parts: call place(part, location, **overrides)')
    variants, shapes, instances, children, names = {}, {}, [], [], set()
    for index, (part_name, location, name, overrides) in enumerate(placed):
        if part_name not in PARTS:
            raise ValueError(f'place(): no part {part_name!r}; parts are {sorted(PARTS)}')
        key = part_name + json.dumps(overrides, sort_keys=True, default=str)
        if key not in variants:
            variants[key] = {'name': f'variant_{len(variants)}', 'part': part_name,
                             'overrides': overrides}
            shapes[key] = PARTS[part_name](**overrides)
        location = Location() if location is None else location
        if not isinstance(location, Location):
            location = Location(location)
        label = name or f'{part_name}_{index + 1}'
        if label in names:
            raise ValueError(f'place(): two instances are named {label!r}')
        names.add(label)
        child = shapes[key].moved(location)
        child.label = label
        children.append(child)
        t = location.wrapped.Transformation()
        instances.append({'id': label, 'part': part_name, 'variant': variants[key]['name'],
                          'matrix': [[t.Value(r, c) for c in range(1, 5)] for r in range(1, 4)]})
    for key, variant in variants.items():
        show(shapes[key], variant['name'])
    result = Compound(children=children)
    result.label = 'assembly'
    return result, list(variants.values()), instances''', '', '',
              *PARTS_TABLE,
              'assembly, VARIANTS, INSTANCES = _assemble(build_assembly())',
              'show(assembly, "assembly")',
              f"print({LAYOUT_MARK!r} + json.dumps({{'variants': VARIANTS, 'instances': INSTANCES}}, default=str))",
              '']
    return '\n'.join(lines), spans


def parse_layout(output):
    """The variants and instances the assembly script printed."""
    for line in reversed(output.splitlines()):
        if line.startswith(LAYOUT_MARK):
            return json.loads(line[len(LAYOUT_MARK):])
    raise ModelError('The assembly built but did not report its layout')


class AssemblyModel(ParametricModel):
    """The workspace's assembly: parameters plus layout features that call place()."""

    requires_part = False
    tool_prefix = 'assembly'
    unchecked = 'not checked (an assembly has no reference mesh)'

    def __init__(self, cad, artifacts):
        super().__init__(cad, artifacts, ASSEMBLY, None, 'parts placed together')
        self.object = ASSEMBLY
        self.layout = {'variants': [], 'instances': []}

    @property
    def folder(self):
        return self.artifacts.root / ASSEMBLY

    def script(self):
        return generate_assembly_script(self.state)

    async def _build(self):
        script, size = await super()._build()
        self.layout = parse_layout(self.last_output)
        size = dict(size or {})
        size['instances'] = dict(Counter(i['part'] for i in self.layout['instances']))
        size['variants'] = [{'part': v['part'], 'overrides': v['overrides']} for v in self.layout['variants']]
        return script, size

    async def set_params(self, params=None, remove=(), placement=..., verify=True):
        if placement is not ...:
            raise ModelError('The assembly has no placement; give each place() call a Location')
        return await super().set_params(params, remove, verify=False)

    async def rebuild(self):
        """Rebuild after a part changed, keeping the revision. Returns a short note."""
        if not self.state['features']:
            return None
        try:
            script, size = await self._build()
        except ModelError as exc:
            return f'The assembly no longer builds: {str(exc)[:600]}'
        self._save(script)
        return f"assembly rebuilt: {sum(size['instances'].values())} instances"

    def used_parts(self):
        return sorted({i['part'] for i in self.layout['instances']})

    def snapshot(self):
        snap = super().snapshot()
        instances = self.layout['instances']
        snap['instances'] = dict(Counter(i['part'] for i in instances))
        snap['instance_count'] = len(instances)
        snap['variants'] = [{'name': v['name'], 'part': v['part'], 'overrides': v['overrides'],
                             'count': sum(i['variant'] == v['name'] for i in instances)}
                            for v in self.layout['variants']]
        return snap

    def report(self, units=None):
        unit = units or 'model units'
        state = self.state
        lines = [f"# Assembly (revision {state['revision']})", '']
        counts = Counter(i['part'] for i in self.layout['instances'])
        lines += [f"{len(self.layout['instances'])} instances: "
                  + ', '.join(f'{n} x {part}' for part, n in sorted(counts.items())), '']
        if self.layout['variants']:
            lines += ['## Variants', '']
            for variant in self.layout['variants']:
                overrides = ', '.join(f'{k}={v}' for k, v in variant['overrides'].items()) or 'defaults'
                lines.append(f"- {variant['part']} ({overrides})")
            lines.append('')
        lines += ['## Parameters', '', f'| Name | Value ({unit}) | Source | Evidence |', '|---|---|---|---|']
        for name, entry in state['params'].items():
            lines.append(f"| {name} | {entry['value']} | {entry['source']} | {entry['evidence'].replace('|', '/')} |")
        lines += ['', '## Layout features', '']
        for feature in state['features']:
            lines.append(f"{feature['id']}. **{feature['name']}**")
        text = '\n'.join(lines) + '\n'
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / 'report.md').write_text(text, encoding='utf-8')
        return text


def part_name_from(text, taken=()):
    """A valid, unused part name from a file name or a free-form label."""
    stem = Path(text).stem if '.' in text else text
    name = ''.join(c if c.isalnum() else '_' for c in stem.lower()).strip('_') or 'part'
    if name[0].isdigit():
        name = f'part_{name}'
    while '__' in name:
        name = name.replace('__', '_')
    base, n = name, 2
    while name in taken or name == ASSEMBLY:
        name, n = f'{base}_{n}', n + 1
    return name


def write_workspace(root, parts, active=None):
    """Record the workspace's parts: [{name, reference (path or None), description}]."""
    root = Path(root)
    entries = [{'name': p['name'], 'description': p.get('description', ''),
                'reference': str(Path(p['reference']).resolve().relative_to(root.resolve()))
                if p.get('reference') else None} for p in parts]
    data = {'parts': entries, 'active': active or (entries[0]['name'] if entries else None)}
    (root / WORKSPACE_FILE).write_text(json.dumps(data, indent=2), encoding='utf-8')
    return data


def read_workspace(root):
    """Part specs from workspace.json, or one part for a bare reference.stl."""
    root = Path(root)
    path = root / WORKSPACE_FILE
    if path.is_file():
        data = json.loads(path.read_text(encoding='utf-8'))
        parts = [{**p, 'reference': root / p['reference'] if p.get('reference') else None}
                 for p in data.get('parts', [])]
        return parts, data.get('active')
    if (root / 'reference.stl').is_file():
        return [{'name': 'part', 'reference': root / 'reference.stl', 'description': ''}], 'part'
    return [], None


class Workspace:
    """Parts by name, the assembly, and which part tools act on by default."""

    def __init__(self, cad, artifacts, parts=(), active=None):
        self.cad = cad
        self.artifacts = artifacts
        self.parts = {}
        for spec in parts:
            self._add(spec['name'], spec.get('reference'), spec.get('description', ''))
        self.assembly = AssemblyModel(cad, artifacts)
        self.assembly.load_saved()
        self.assembly.on_saved = self.save
        self.active = active if active in self.parts else next(iter(self.parts), None)
        mesh = getattr(cad, 'mesh', None)
        if mesh is not None and hasattr(mesh, 'references'):
            mesh.references = {name: p.reference for name, p in self.parts.items() if p.reference}
            mesh.active_part = lambda: self.active

    @classmethod
    def load(cls, cad, artifacts):
        parts, active = read_workspace(artifacts.root)
        return cls(cad, artifacts, parts, active)

    def _add(self, name, reference=None, description=''):
        model = ParametricModel(self.cad, self.artifacts, name, Path(reference) if reference else None,
                                description)
        model.load_saved()  # a reopened workspace continues where it left off
        model.on_saved = self.save
        self.parts[name] = model
        return model

    # ---------------------------------------------------------- parts

    def part(self, name=None):
        if name:
            if name == ASSEMBLY:
                raise ModelError('Change the assembly with the assembly_* tools')
            if name not in self.parts:
                raise ModelError(f'No part {name!r}; parts are {sorted(self.parts)}. '
                                 'Create one with workspace_add_part.')
            return self.parts[name]
        if self.active is None:
            raise ModelError('The workspace has no parts yet; create one with workspace_add_part')
        return self.parts[self.active]

    def add_part(self, name, description=''):
        _check_name(name, 'Part name')
        if name in self.parts or name == ASSEMBLY:
            raise ModelError(f'A part named {name!r} already exists')
        if not isinstance(description, str) or not description.strip():
            raise ModelError('Describe the part in a sentence (what it is and how it fits the others)')
        self._add(name, None, description.strip())
        self.active = name
        self.save()
        return {'part': name, 'active': name, 'parts': sorted(self.parts),
                'note': f'{name} has no reference mesh: set its parameters as design intent (assumed) '
                        f'and build it with model_* tools, part={name!r}.'}

    def add_reference_part(self, name, reference, description=''):
        """A part for a mesh the user uploaded while the session runs."""
        if name in self.parts or name == ASSEMBLY:
            raise ModelError(f'A part named {name!r} already exists')
        model = self._add(name, reference, description)
        mesh = getattr(self.cad, 'mesh', None)
        if mesh is not None and hasattr(mesh, 'references'):
            mesh.references[name] = model.reference
        if self.active is None:
            self.active = name
        self.save()
        return model

    def set_active(self, name):
        self.part(name)
        self.active = name
        self.save()
        return {'active': name}

    def has_saved_models(self):
        """True when any part or the assembly has saved features (a reopened workspace)."""
        return any(p.state['features'] for p in [*self.parts.values(), self.assembly])

    async def restore(self):
        """Rebuild every saved part, then the assembly, in a fresh CAD engine.
        Returns notes about anything that did not rebuild."""
        notes = []
        for part in self.parts.values():
            note = await part.restore()
            if note:
                notes.append(note)
        if self.assembly.state['features']:
            note = await self.assembly.restore()
            if note:
                notes.append(note)
        return notes

    def references(self):
        """(part, reference STL) for each part that has one."""
        return [(name, p.reference) for name, p in self.parts.items() if p.reference]

    async def after_part_change(self, name):
        """Rebuild the assembly when it uses the part that just changed."""
        if name in self.assembly.used_parts():
            return await self.assembly.rebuild()
        return None

    # ---------------------------------------------------------- views

    def objects(self):
        """CAD session objects the viewer should show: the assembly once it places
        anything, otherwise every part that has been built."""
        if self.assembly.layout['instances']:
            return [ASSEMBLY]
        return [p.object for p in self.parts.values() if p.state['features']]

    def snapshot(self):
        return {'active': self.active,
                'parts': [p.snapshot() for p in self.parts.values()],
                'assembly': self.assembly.snapshot(),
                'instances': self.assembly.layout['instances'],
                'variants': self.assembly.layout['variants']}

    def status(self):
        parts = []
        for name, p in self.parts.items():
            parts.append({'name': name, 'object': p.object, 'description': p.description,
                          'has_reference': p.reference is not None, 'revision': p.state['revision'],
                          'features': [f"{f['id']} {f['name']}" for f in p.state['features']],
                          'params': {k: v['value'] for k, v in p.state['params'].items()},
                          'verdict': (p.state['deviation'] or {}).get('verdict')})
        snap = self.assembly.snapshot()
        return {'active': self.active, 'parts': parts,
                'assembly': {'revision': snap['revision'], 'instances': snap['instances'],
                             'variants': snap['variants'],
                             'features': [f"{f['id']} {f['name']}" for f in snap['features']],
                             'params': {k: v['value'] for k, v in snap['params'].items()}}}

    # ---------------------------------------------------------- persistence

    def combined_script(self):
        """Every built part's script, then the assembly's: one file that rebuilds it all."""
        chunks = [f'# {COMBINED_SCRIPT}: every part, then the assembly. Generated by Aida.\n']
        for p in self.parts.values():
            if p.state['features']:
                chunks.append(f'# ---- part {p.name} ' + '-' * 40 + '\n' + p.script()[0])
        if self.assembly.state['features']:
            chunks.append('# ---- assembly ' + '-' * 40 + '\n' + self.assembly.script()[0])
        return '\n'.join(chunks)

    def save(self):
        root = self.artifacts.root
        write_workspace(root, [{'name': n, 'reference': p.reference, 'description': p.description}
                               for n, p in self.parts.items()], self.active)
        if any(p.state['features'] for p in self.parts.values()):
            (root / COMBINED_SCRIPT).write_text(self.combined_script(), encoding='utf-8')
