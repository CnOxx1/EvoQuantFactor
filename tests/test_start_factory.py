import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start_factory.sh"


def _run(args, env=None, fake_exit=0):
    fake = Path(os.environ.get("TMPDIR", "/tmp")) / "qfactor-fake"
    if env is None:
        env = {}
    work = Path(env.get("WORKDIR", "/tmp"))
    if "WORKDIR" in env:
        work = Path(env.pop("WORKDIR"))
    work.mkdir(parents=True, exist_ok=True)
    fake = work / "qfactor"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$0 $*\" >> \"{work / 'calls.log'}\"\n"
        f"exit {int(fake_exit)}\n",
        encoding="utf-8",
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    merged = os.environ.copy()
    merged.update(
        {
            "SKIP_PULL": "1",
            "DRY_RUN": env.pop("DRY_RUN", "0"),
            "QFACTOR_BIN": str(fake),
            "OPENAI_API_KEY": env.pop("OPENAI_API_KEY", "test-key"),
            "PYTHON_BIN": "python3",
        }
    )
    merged.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        env=merged,
        capture_output=True,
        text=True,
        check=False,
    )


def test_start_script_is_executable():
    assert SCRIPT.exists()
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


def test_help_lists_factory_and_prepare():
    out = subprocess.run(
        ["bash", str(SCRIPT), "help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "prepare-data" in out.stdout
    assert "factory" in out.stdout


def test_unknown_command_exits_2():
    out = subprocess.run(
        ["bash", str(SCRIPT), "not-a-command"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 2


def test_dry_run_factory_does_not_call_cli(tmp_path: Path):
    out = _run(
        ["factory"],
        env={"WORKDIR": str(tmp_path), "DRY_RUN": "1"},
    )
    assert out.returncode == 0, out.stderr + out.stdout
    assert "dry_run prepare-data" in out.stdout
    assert "dry_run" in out.stdout and "start" in out.stdout
    assert not (tmp_path / "calls.log").exists()


def test_prepare_invokes_prepare_data(tmp_path: Path):
    out = _run(["prepare"], env={"WORKDIR": str(tmp_path)})
    assert out.returncode == 0, out.stderr + out.stdout
    calls = (tmp_path / "calls.log").read_text(encoding="utf-8")
    assert "prepare-data" in calls
    assert "data-contract-readiness" in calls


def test_prepare_blocks_when_mining_not_allowed(tmp_path: Path):
    out = _run(["prepare"], env={"WORKDIR": str(tmp_path)}, fake_exit=2)
    assert out.returncode == 1
    assert "blocked mining" in out.stdout


def test_factory_requires_openai_key(tmp_path: Path):
    out = _run(
        ["factory"],
        env={"WORKDIR": str(tmp_path), "OPENAI_API_KEY": "", "DRY_RUN": "1"},
    )
    assert out.returncode == 1
    assert "OPENAI_API_KEY" in out.stdout
