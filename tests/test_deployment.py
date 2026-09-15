"""Local preflight and shell argument checks; no real server or network."""

from pathlib import Path
import os
import shutil
import socket
import subprocess

import pytest

from scripts import preflight


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("Network is forbidden")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setenv("LLM_ENABLED", "false")


def test_preflight_local(tmp_path):
    (tmp_path / "data").mkdir()
    workbook = tmp_path / "data/after_sales.xlsx"
    workbook.write_bytes(b"not opened by preflight")
    assert preflight.check_preflight(tmp_path) == ()
    assert workbook.read_bytes() == b"not opened by preflight"
    assert list((tmp_path / "runtime").iterdir()) == []


def test_preflight_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.sys, "version_info", (3, 10))
    (tmp_path / "runtime").write_text("blocked")
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_API_KEY", "")
    failures = preflight.check_preflight(tmp_path)
    assert len(failures) == 4


def test_import_failure_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.importlib, "import_module", lambda name: (_ for _ in ()).throw(
        RuntimeError("private credential detail")))
    assert "private credential" not in str(preflight.check_preflight(tmp_path))


@pytest.mark.parametrize("port,success", [(None, True), ("9000", True), ("0", False), ("abc", False)])
def test_launch_arguments(tmp_path, port, success):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "run_ec2.sh"
    shutil.copy(Path(__file__).resolve().parents[1] / "scripts/run_ec2.sh", script)
    binary = tmp_path / ".venv/bin"
    binary.mkdir(parents=True)
    (binary / "activate").write_text("# test activation\n")
    fake = binary / "python"
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@"\n')
    fake.chmod(0o755)
    env = dict(os.environ)
    env.pop("PORT", None)
    if port is not None:
        env["PORT"] = port
    result = subprocess.run(["bash", str(script)], cwd="/tmp", env=env, text=True, capture_output=True)
    assert (result.returncode == 0) == success
    if success:
        assert "--server.address=0.0.0.0" in result.stdout
        assert f"--server.port={port or '8501'}" in result.stdout
        assert "-m\nstreamlit\nrun\napp.py" in result.stdout


def test_missing_venv(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "run_ec2.sh"
    shutil.copy(Path(__file__).resolve().parents[1] / "scripts/run_ec2.sh", script)
    result = subprocess.run(["bash", str(script)], text=True, capture_output=True)
    assert result.returncode == 1
    assert "Missing .venv" in result.stderr
