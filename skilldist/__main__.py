"""skilldist CLI.

  python -m skilldist check --skill NAME [--skill N2] | --pending      release readiness (exit 1 if blocked)
  python -m skilldist pending                                            skills whose manifest version is unreleased
  python -m skilldist package --skill NAME --out dist                    build <skill>-<version>.zip
  python -m skilldist release --skill NAME --approver USER [--api]       record the release, update marketplace + catalog
  python -m skilldist deprecate --skill NAME --approver USER [--replaced-by X] --reason "..."
  python -m skilldist retire --skill NAME --approver USER --reason "..."
  python -m skilldist reactivate --skill NAME --approver USER
  python -m skilldist confirm-claude-ai --skill NAME --by USER           an Owner uploaded it to Claude.ai
  python -m skilldist catalog | marketplace | lint
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import core


def _skills(args) -> list[str]:
    if getattr(args, "pending", False):
        return core.pending_releases()
    return args.skill or []


def main():
    p = argparse.ArgumentParser(prog="skilldist")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check"); c.add_argument("--skill", action="append"); c.add_argument("--pending", action="store_true")
    c.add_argument("--action", default="release"); c.add_argument("--json", action="store_true")
    c.add_argument("--summary", help="also write the markdown report here")
    c.add_argument("--ready-file", help="write the comma-separated skills that passed here")
    sub.add_parser("pending")
    pk = sub.add_parser("package"); pk.add_argument("--skill", action="append", required=True); pk.add_argument("--out", default="dist")
    r = sub.add_parser("release"); r.add_argument("--skill", action="append", required=True)
    r.add_argument("--approver", required=True); r.add_argument("--out", default="dist")
    r.add_argument("--run-url", default=""); r.add_argument("--note", default="")
    r.add_argument("--api", action="store_true", help="also upload to the Claude API Skills endpoint")
    for name in ("deprecate", "retire", "reactivate"):
        s = sub.add_parser(name); s.add_argument("--skill", required=True); s.add_argument("--approver", required=True)
        s.add_argument("--reason", default=""); s.add_argument("--replaced-by")
    cf = sub.add_parser("confirm-claude-ai"); cf.add_argument("--skill", required=True); cf.add_argument("--by", required=True)
    cf.add_argument("--version")
    for name in ("catalog", "marketplace", "lint"):
        sub.add_parser(name)
    args = p.parse_args()

    if args.cmd == "check":
        res = [core.check(s, args.action) for s in _skills(args)]
        md = core.render_check(res) if res else "Nothing to release."
        if args.summary:
            Path(args.summary).write_text(md)
        if args.ready_file:
            Path(args.ready_file).write_text(",".join(r["skill"] for r in res if r["ok"]))
        print(json.dumps(res, indent=2, default=str) if args.json else md)
        sys.exit(0 if all(r["ok"] for r in res) else 1)
    if args.cmd == "pending":
        print(",".join(core.pending_releases()))
        return
    if args.cmd == "package":
        for s in args.skill:
            zp, sha = core.package(s, Path(args.out))
            print(f"{zp}  sha256 {sha}")
        return
    if args.cmd == "release":
        for s in args.skill:
            api_result = None
            m = core.manifest(s)
            if args.api and "api" in m["channels"] and core.dist_config()["channels"].get("api"):
                from .api import upload
                zp, _ = core.package(s, Path(args.out))
                prev = core.latest_release(s) or {}
                api_result = upload(zp, ((prev.get("channels_done") or {}).get("api") or {}).get("skill_id"), s)
            rec = core.release(s, args.approver, Path(args.out), args.run_url, args.note, api_result)
            print(f"released {s} {rec['version']} -> {', '.join(rec['channels_done'])} ({rec['package']}, sha256 {rec['sha256'][:12]})")
        return
    if args.cmd in ("deprecate", "retire", "reactivate"):
        status = {"deprecate": "deprecated", "retire": "retired", "reactivate": "active"}[args.cmd]
        out = core.change_status(args.skill, status, args.approver, args.reason, args.replaced_by)
        print(json.dumps(out, indent=2))
        return
    if args.cmd == "confirm-claude-ai":
        rel = core.latest_release(args.skill) if not args.version else \
            json.loads((core.RELEASES_DIR / args.skill / f"{args.version}.json").read_text())
        if not rel:
            sys.exit(f"{args.skill} has no release")
        rel["channels_done"]["claude_ai"] = f"provisioned {datetime.now(timezone.utc).date()} by {args.by}"
        (core.RELEASES_DIR / args.skill / f"{rel['version']}.json").write_text(json.dumps(rel, indent=2) + "\n")
        core._update_registry(args.skill, rel)
        core.write_catalog()
        print(f"{args.skill} {rel['version']}: Claude.ai provisioning recorded")
        return
    if args.cmd == "catalog":
        print(core.write_catalog())
    elif args.cmd == "marketplace":
        core.rebuild_marketplace(); print(core.MARKET_FILE, core.write_settings_snippet())
    elif args.cmd == "lint":
        probs = core.lint()
        print("\n".join(probs) if probs else "distribution manifests OK")
        sys.exit(1 if probs else 0)


if __name__ == "__main__":
    main()
