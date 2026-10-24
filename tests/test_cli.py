import sys

import pytest

from aida.cli import main


def test_missing_mesh(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['aida', '--mesh', '/does/not/exist.stl'])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert 'existing .stl' in capsys.readouterr().err


def test_missing_credentials(monkeypatch, tmp_path, capsys):
    mesh = tmp_path / 'test.stl'
    mesh.write_text('solid test\nendsolid test\n')
    monkeypatch.setattr(sys, 'argv', ['aida', '--mesh', str(mesh)])
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert 'ANTHROPIC_API_KEY' in capsys.readouterr().err
