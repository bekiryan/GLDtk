import os
import subprocess
import sys

import app_dirs
import server


def test_app_support_dir_per_platform(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert app_dirs.app_support_dir() == tmp_path / "GLDtk"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert app_dirs.app_support_dir().parts[-3:] == ("Library", "Application Support", "GLDtk")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert app_dirs.app_support_dir() == tmp_path / "GLDtk"


def test_pid_alive():
    assert server._pid_alive(os.getpid())
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not server._pid_alive(child.pid)
