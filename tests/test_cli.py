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
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    from aida import config
    monkeypatch.setattr(config, 'REPO_ROOT', tmp_path)  # ignore the developer's real .env
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert 'ANTHROPIC_API_KEY' in capsys.readouterr().err


def test_scan_dispatch_loads_local_env(monkeypatch, tmp_path):
    import os
    from types import ModuleType

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('COMFY_HOST', raising=False)
    (tmp_path / '.env').write_text('COMFY_HOST=127.0.0.1:9999\n')
    scan_cli = ModuleType('aida.scan.cli')

    def scan_main(args):
        assert args == ['--help']
        assert os.environ['COMFY_HOST'] == '127.0.0.1:9999'
        return 0

    scan_cli.main = scan_main
    monkeypatch.setitem(sys.modules, 'aida.scan.cli', scan_cli)
    monkeypatch.setattr(sys, 'argv', ['aida', 'scan', '--help'])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
