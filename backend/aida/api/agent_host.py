"""Session-bound Aida tools: scanning, photographs, commits, FEA and questions."""
import uuid

from ..analysis.settings import FeaSettings

KINDS = {'choice', 'number', 'text'}


def _text(value, limit: int) -> str:
    return str(value or '').strip()[:limit]


def clean_questions(questions: list) -> list[dict]:
    """Keep only well-formed fields, with bounded lengths, so the UI can render them safely."""
    cleaned = []
    for index, question in enumerate(questions[:4]):
        if not isinstance(question, dict) or not _text(question.get('prompt'), 300):
            raise ValueError('Each question needs a prompt.')
        kind = question.get('kind') if question.get('kind') in KINDS else 'text'
        options = [{'label': _text(o.get('label'), 60), 'detail': _text(o.get('detail'), 120)}
                   for o in (question.get('options') or [])[:8]
                   if isinstance(o, dict) and _text(o.get('label'), 60)]
        if kind == 'choice' and not options:
            kind = 'text'
        cleaned.append({
            'id': _text(question.get('id'), 40) or f'q{index + 1}',
            'prompt': _text(question.get('prompt'), 300),
            'kind': kind,
            'options': options,
            'unit': _text(question.get('unit'), 20),
            'allow_other': bool(question.get('allow_other', kind == 'choice')),
        })
    return cleaned


class SessionHost:
    def __init__(self, session):
        self.session = session

    async def commit_changes(self, message: str) -> dict:
        from .commits import save_commit
        from .cad_sessions import snapshot_hook

        async with self.session.parts_lock:
            async with self.session.cad.lock:
                if self.session.workspace.objects() and self.session.cad.on_execute is not None:
                    # Refresh even when the preceding operation only changed parameters.
                    await snapshot_hook(self.session.cad, self.session, self.session.workspace, strict=True)()
                commit = save_commit(self.session, message)
        return {'committed': True, **{k: commit[k] for k in ('number', 'message', 'at', 'geometry_id')}} if commit else {
            'committed': False, 'message': 'Nothing to commit.'}

    async def run_fea(self, args: dict) -> dict:
        from .fea import FeaError, start_analysis

        revision = args.pop('revision', None)
        if revision is None:
            revision = self._latest()
        settings = FeaSettings(**args)
        try:
            job = start_analysis(self.session, int(revision), settings, source='agent')
        except FeaError as exc:
            raise ValueError(str(exc)) from exc
        await job['done'].wait()
        from .commits import list_commits
        commit = next((c for c in reversed(list_commits(self.session.artifacts.root))
                       if c['geometry_id'] == job['revision']), None)
        job['design'] = f"Commit {commit['number']}" if commit else 'Live geometry'
        return job

    def _latest(self) -> int:
        generated = [m['revision'] for m in self.session.meshes if m['generated']]
        if not generated:
            raise ValueError('There is no generated CAD to test yet. Build the part first.')
        return generated[-1]

    async def list_faces(self) -> dict:
        from .fea import FeaError, list_faces

        try:
            return await list_faces(self.session, self._latest())
        except FeaError as exc:
            raise ValueError(str(exc)) from exc

    async def ask_user(self, questions: list) -> None:
        self.session.publish({'type': 'question', 'id': uuid.uuid4().hex[:12],
                              'questions': clean_questions(questions)})
