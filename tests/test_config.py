import os

from aida.config import load_local_env


def test_loads_key_from_working_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    (tmp_path / '.env').write_text('ANTHROPIC_API_KEY="test-key-from-file"\n')
    load_local_env()
    assert os.environ['ANTHROPIC_API_KEY'] == 'test-key-from-file'
    monkeypatch.delenv('ANTHROPIC_API_KEY')


def test_shell_key_takes_precedence(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'test-key-from-shell')
    (tmp_path / '.env').write_text('ANTHROPIC_API_KEY=test-key-from-file\n')
    load_local_env()
    assert os.environ['ANTHROPIC_API_KEY'] == 'test-key-from-shell'
