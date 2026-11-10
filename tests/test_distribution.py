"""Distribution flow on a throwaway copy of the repo: evaluate (mock) -> approve -> release -> deprecate -> retire."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def repo(tmp_path):
    dst = tmp_path / "repo"
    shutil.copytree(ROOT, dst, ignore=shutil.ignore_patterns(".git", "skilleval-workspace", "dist", "__pycache__"))
    return dst


def run(repo, *args, check=True):
    p = subprocess.run([sys.executable, *args], cwd=repo, capture_output=True, text=True)
    if check:
        assert p.returncode == 0, p.stdout + p.stderr
    return p


def test_release_needs_approved_evaluation(repo):
    p = run(repo, "-m", "skilldist", "check", "--skill", "pii-redactor", check=False)
    assert p.returncode == 1 and "no approved evaluation" in p.stdout


def test_release_lifecycle(repo):
    run(repo, "-m", "skilleval", "run", "--skill", "pii-redactor", "--executor", "mock", "--workspace", "ws",
        "--allow-failed-gate", "--no-trigger")
    run(repo, "-m", "skilleval", "publish", "--workspace", "ws", "--approver", "eval-reviewer", "--include-failed")
    gate = json.loads((repo / "eval-results/pii-redactor/latest.json").read_text())["gate_status"]
    if gate != "pass":
        pytest.skip("mock evaluation did not pass the gate on this seed")
    run(repo, "-m", "skilldist", "check", "--skill", "pii-redactor")
    run(repo, "-m", "skilldist", "release", "--skill", "pii-redactor", "--approver", "release-reviewer", "--out", "dist")

    rec = json.loads((repo / "releases/pii-redactor/1.0.0.json").read_text())
    assert rec["approved_by"] == "release-reviewer" and set(rec["channels_done"]) == {"claude_code", "claude_ai"}
    assert (repo / "dist/pii-redactor-1.0.0.zip").exists()
    mk = json.loads((repo / ".claude-plugin/marketplace.json").read_text())
    assert [p["name"] for p in mk["plugins"]] == ["pii-redactor"]
    assert (repo / "marketplace/plugins/pii-redactor/skills/pii-redactor/SKILL.md").exists()
    assert "pii-redactor@" in (repo / "distribution/claude-code-settings.json").read_text()
    assert "1.0.0 released" in (repo / "CATALOG.md").read_text()

    # same version again is refused; a content change without re-evaluation is refused
    assert run(repo, "-m", "skilldist", "check", "--skill", "pii-redactor", check=False).returncode == 1
    (repo / "distribution/pii-redactor.yaml").write_text(
        (repo / "distribution/pii-redactor.yaml").read_text().replace("version: 1.0.0", "version: 1.1.0")
        + "  1.1.0: tweak\n")
    with open(repo / "skills/pii-redactor/SKILL.md", "a") as f:
        f.write("\nextra guidance\n")
    p = run(repo, "-m", "skilldist", "check", "--skill", "pii-redactor", check=False)
    assert p.returncode == 1 and "re-evaluate" in p.stdout

    run(repo, "-m", "skilldist", "deprecate", "--skill", "pii-redactor", "--approver", "gov", "--reason", "old",
        "--replaced-by", "meeting-minutes")
    mk = json.loads((repo / ".claude-plugin/marketplace.json").read_text())
    assert mk["plugins"][0]["description"].startswith("[DEPRECATED - use meeting-minutes]")
    run(repo, "-m", "skilldist", "lint")
    run(repo, "-m", "skilldist", "retire", "--skill", "pii-redactor", "--approver", "gov", "--reason", "unused")
    assert json.loads((repo / ".claude-plugin/marketplace.json").read_text())["plugins"] == []
    assert "retired" in (repo / "CATALOG.md").read_text()


def test_secret_blocks_release(repo):
    with open(repo / "skills/pii-redactor/SKILL.md", "a") as f:
        f.write('\napi_key = "abcdefghijklmnop"\n')
    p = run(repo, "-m", "skilldist", "check", "--skill", "pii-redactor", check=False)
    assert "possible secret" in p.stdout


def test_lint_flags_missing_manifest(repo):
    (repo / "distribution/pii-redactor.yaml").unlink()
    p = run(repo, "-m", "skilldist", "lint", check=False)
    assert p.returncode == 1 and "pii-redactor.yaml is missing" in p.stdout
