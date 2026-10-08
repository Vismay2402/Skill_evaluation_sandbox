"""Finding skills in the repo and snapshotting a previous version from git."""
from __future__ import annotations

import io
import subprocess
import tarfile
from pathlib import Path

from .config import REPO_ROOT


def all_skills(skills_dir: Path) -> list[str]:
    return sorted(p.name for p in skills_dir.iterdir() if (p / "SKILL.md").exists())


def changed_skills(skills_dir: Path, base_ref: str) -> list[str]:
    rel = skills_dir.resolve().relative_to(REPO_ROOT)
    out = subprocess.run(["git", "diff", "--name-only", f"{base_ref}...HEAD", "--", str(rel)],
                         cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    names = {Path(line).relative_to(rel).parts[0] for line in out.splitlines() if line.strip()}
    return sorted(n for n in names if (skills_dir / n / "SKILL.md").exists())


def snapshot_from_ref(skills_dir: Path, skill: str, ref: str, dest: Path) -> Path | None:
    """Extract skills/<skill> as it exists at `ref` into dest/<skill>. None if absent there."""
    rel = (skills_dir.resolve().relative_to(REPO_ROOT) / skill).as_posix()
    proc = subprocess.run(["git", "archive", "--format=tar", ref, rel], cwd=REPO_ROOT,
                          capture_output=True)
    if proc.returncode != 0:
        return None
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")
    path = dest / rel
    return path if (path / "SKILL.md").exists() else None


def tree_hash(skill_path: Path) -> str:
    """Content hash of the skill folder (excluding evals) - identifies the exact version evaluated."""
    import hashlib
    h = hashlib.sha256()
    for p in sorted(skill_path.rglob("*")):
        if p.is_file() and "evals" not in p.relative_to(skill_path).parts and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(skill_path)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]
