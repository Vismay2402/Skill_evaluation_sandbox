"""Claude API Skills channel: upload a released skill (POST /v1/skills, or a new version of an existing one).

Docs: https://platform.claude.com/docs/en/api/beta/skills/create and .../skills/versions/create
The skill id is kept in releases/<skill>/<version>.json and the registry, so later releases add versions.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
import zipfile
from pathlib import Path

API = os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com").rstrip("/")
HEADERS = {"anthropic-version": "2023-06-01", "anthropic-beta": "skills-2025-10-02"}


def _multipart(zip_path: Path, display_name: str | None) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="files[]"; filename="{name}"\r\n'
                          "Content-Type: application/octet-stream\r\n\r\n").encode() + z.read(name) + b"\r\n")
    if display_name:
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="display_name"\r\n\r\n'
                      f"{display_name}\r\n").encode())
    return b"".join(parts) + f"--{boundary}--\r\n".encode(), boundary


def upload(zip_path: Path, skill_id: str | None, display_name: str | None = None) -> dict:
    key = os.environ.get("SKILLS_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("SKILLS_API_KEY (or ANTHROPIC_API_KEY) is not set")
    body, boundary = _multipart(zip_path, None if skill_id else display_name)
    url = f"{API}/v1/skills/{skill_id}/versions" if skill_id else f"{API}/v1/skills"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        **HEADERS, "x-api-key": key, "content-type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    if skill_id:
        return {"skill_id": skill_id, "version": data.get("id") or data.get("version")}
    return {"skill_id": data["id"], "version": data.get("latest_version_id") or data.get("latest_version")}
