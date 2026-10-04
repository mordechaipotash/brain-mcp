"""brain-mcp v2 CLI — record · install · uninstall · health · backup · redact ·
migrate-v1 · doctor · serve.

Every verb prints what it measured. Windows is out of scope for v2.0 (stated,
not implied). macOS scheduling = LaunchAgent; Linux = systemd user timer.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

from .paths import brain_home, ensure_layout

LANES = ["cc_transcript", "codex_rollout", "pi_session"]
PLIST_LABEL = "com.brainmcp.record"


def _init() -> None:
    from .floor_db import init_db

    ensure_layout(LANES)
    init_db()


# ── record: one tick (scan + drain + derived refresh) ─────────────────────────

def cmd_record(args) -> int:
    _init()
    from .drain import drain_tick
    from .scanner import scan_tick
    from . import derived

    d = drain_tick()
    s = scan_tick(only_lane=args.lane)
    r = derived.refresh(rebuild_fts=args.fts if args.fts is not None else None)
    print(json.dumps({"drain": d, "scan": s, "derived": r}, indent=2, default=str))
    return 0


# ── install / uninstall ───────────────────────────────────────────────────────

_CC_SETTINGS = Path("~/.claude/settings.json").expanduser()


def _cc_hooks_present(settings: dict) -> str | None:
    """Detect ANY existing CC capture hooks so we never double-spool.

    Looks in TWO places, because hooks arrive from two: settings.json, and
    installed plugins (whose hooks/hooks.json never appears in settings).
    Checking only settings.json missed a live capture pipeline on the author's
    own machine — measured 2026-08-20, before the first real install.
    """
    blob = json.dumps(settings.get("hooks", {}))
    if "brain-stream-on-turn" in blob:
        return "brain (already installed via settings.json)"
    if "cc-stream-on-turn" in blob:
        return "another capture pipeline (cc-stream hooks in settings.json)"

    # plugin-provided hooks
    plugin_root = Path("~/.claude/plugins").expanduser()
    if plugin_root.exists():
        for hooks_json in plugin_root.glob("**/hooks/hooks.json"):
            try:
                text = hooks_json.read_text()
            except Exception:
                continue
            if "brain-stream-on-turn" in text:
                return f"brain (already installed as a plugin: {hooks_json.parent.parent.name})"
            if "cc-stream-on-turn" in text or "stream-on-sessionend" in text:
                return f"another capture pipeline (plugin: {hooks_json.parent.parent.name})"
    return None


def cmd_install(args) -> int:
    _init()
    agent = args.agent
    scripts = Path(__file__).resolve().parent.parent.parent / "plugin" / "scripts"

    if agent == "cc":
        settings = json.loads(_CC_SETTINGS.read_text()) if _CC_SETTINGS.exists() else {}
        found = _cc_hooks_present(settings)
        if found:
            print(f"cc hooks: DEFERRING — detected {found}; not installing a second spool.")
            print("the scanner still covers ~/.claude/projects as backstop.")
        else:
            hooks = settings.setdefault("hooks", {})
            for event, script in (("Stop", "brain-stream-on-turn.sh"),
                                  ("SessionEnd", "brain-stream-on-sessionend.sh")):
                entry = {"hooks": [{"type": "command", "command": str(scripts / script)}]}
                hooks.setdefault(event, []).append(entry)
            _CC_SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            _CC_SETTINGS.write_text(json.dumps(settings, indent=2))
            print(f"cc hooks installed into {_CC_SETTINGS} (Stop + SessionEnd)")
    elif agent in ("codex", "pi"):
        print(f"{agent}: scanner-only lane (no reliable hook contract) — covered by the scheduler.")
    else:
        print(f"unknown agent {agent!r}; known: cc, codex, pi", file=sys.stderr)
        return 2

    # the scheduler runs scan+drain for ALL lanes
    if platform.system() == "Darwin":
        plist = Path(f"~/Library/LaunchAgents/{PLIST_LABEL}.plist").expanduser()
        exe = shutil_which_self()
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>{exe}</string><string>record</string>
  </array>
  <key>StartInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>{brain_home()}/health/record.out.log</string>
  <key>StandardErrorPath</key><string>{brain_home()}/health/record.err.log</string>
</dict></plist>
""")
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        r = subprocess.run(["launchctl", "load", str(plist)], capture_output=True, text=True)
        print(f"scheduler: {plist} loaded (60s)" if r.returncode == 0
              else f"scheduler: wrote {plist}; launchctl load failed: {r.stderr.strip()}")
    elif platform.system() == "Linux":
        print("Linux: create a systemd user timer running 'brain-mcp record' every 60s "
              "(unit template in the README). Not auto-installed yet.")
    else:
        print("Windows: out of scope for v2.0.")

    # install is not done until a capture is confirmed
    print("verifying: running one tick now...")
    from .scanner import scan_tick
    census = scan_tick()
    print(json.dumps(census, default=str))
    return 0


def shutil_which_self() -> str:
    import shutil

    return shutil.which("brain-mcp") or sys.executable + " -m brain_mcp.recorder.cli"


def cmd_uninstall(args) -> int:
    removed = []
    if _CC_SETTINGS.exists():
        settings = json.loads(_CC_SETTINGS.read_text())
        hooks = settings.get("hooks", {})
        for event in list(hooks):
            before = len(hooks[event])
            hooks[event] = [h for h in hooks[event]
                            if "brain-stream-on-" not in json.dumps(h)]
            if len(hooks[event]) != before:
                removed.append(f"{event} hook")
            if not hooks[event]:
                del hooks[event]
        _CC_SETTINGS.write_text(json.dumps(settings, indent=2))
    if platform.system() == "Darwin":
        plist = Path(f"~/Library/LaunchAgents/{PLIST_LABEL}.plist").expanduser()
        if plist.exists():
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            plist.unlink()
            removed.append("LaunchAgent")
    print(f"removed: {removed or 'nothing found'}")
    print(f"KEPT deliberately: the floor at {brain_home()} — your bytes are yours. "
          f"Delete it yourself if that is what you want.")
    return 0


# ── health / backup / redact / migrate / doctor / serve ───────────────────────

def cmd_health(args) -> int:
    _init()
    from . import api

    h = api.health()
    print(json.dumps(h, indent=2, default=str))
    if args.exit_nonzero_on_stale and (h["summary"].get("stale") or h["summary"].get("unknown")):
        return 1
    return 0


def cmd_backup(args) -> int:
    _init()
    from . import api

    print(json.dumps(api.backup(args.dest), indent=2, default=str))
    return 0


def cmd_redact(args) -> int:
    """Tombstone + rewrite: span bytes replaced by a marker line-for-line, old sha
    recorded in the manifest. Supersede-and-keep is for evidence; a live secret
    is not evidence."""
    _init()
    import hashlib
    from . import manifest

    p = brain_home() / args.file
    if not p.exists() or not args.file.startswith("lake/"):
        print(f"no such floor file: {args.file}", file=sys.stderr)
        return 2
    a, b = args.lines
    lines = p.read_bytes().splitlines(keepends=True)
    span = b"".join(lines[a - 1:b])
    old_sha = hashlib.sha256(span).hexdigest()
    marker = b'{"redacted":true,"reason":%s}\n' % json.dumps(args.reason).encode()
    new = lines[:a - 1] + [marker] * (b - a + 1) + lines[b:]
    p.write_bytes(b"".join(new))
    manifest.append({"redacted": args.file, "lines": [a, b], "old_span_sha256": old_sha,
                     "reason": args.reason, "redacted_at": manifest.now_iso()})
    print(f"redacted {args.file}:{a}-{b} (old span sha {old_sha[:16]}… recorded in manifest). "
          f"Rebuild derived: brain-mcp record. NOTE: backups made before now still hold the "
          f"original — re-run backup and rotate the secret itself.")
    return 0


def cmd_migrate_v1(args) -> int:
    _init()
    from .floor_db import write_conn
    from . import derived

    with write_conn() as c:
        c.execute(derived.DIALECT_SQL)
        n = c.execute(
            "INSERT INTO derived.messages "
            "SELECT 'v1:'||source||':'||message_id, source, conversation_id, role, model, "
            "content, CASE WHEN timestamp_is_fallback = 0 THEN msg_timestamp END, "
            "NULL, NULL, 0, 0 "
            "FROM read_parquet(?) v "
            "ANTI JOIN derived.messages m ON m.msg_id = 'v1:'||v.source||':'||v.message_id "
            "WHERE v.content IS NOT NULL AND length(v.content) > 0",
            [args.parquet],
        ).fetchone()
        total = c.execute("SELECT count(*) FROM derived.messages WHERE msg_id LIKE 'v1:%'").fetchone()[0]
    print(f"migrated v1 rows now present: {total} (marked v1: — cited as 'derived, raw bytes "
          f"not held'; floor-backed rows win wherever the source still exists)")
    return 0


def cmd_scan_secrets(args) -> int:
    """Report credentials sitting in the floor. Values are never printed."""
    _init()
    from . import secrets as sec

    rep = sec.scan(lanes=[args.lane] if args.lane else None)
    print(json.dumps(rep, indent=2, default=str))
    if args.exit_nonzero_on_findings and rep["distinct_findings"]:
        return 1
    return 0


def cmd_doctor(args) -> int:
    _init()
    from . import api

    print("brain-mcp v2 doctor")
    print(json.dumps(api.capture_status(), indent=2, default=str))
    print(json.dumps(api.health()["summary"], indent=2))
    from . import secrets as sec
    rep = sec.scan()
    n = rep["distinct_findings"]
    print(f"secrets in floor: {n} distinct" + (f" {rep['by_shape']} — run 'brain-mcp scan-secrets'" if n else " ✓"))
    return 0


def cmd_serve(args) -> int:  # pragma: no cover
    from .server import main as serve_main

    serve_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="brain-mcp", description="the recorder for your AI conversations")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="one capture tick: drain spool, scan lanes, refresh index")
    p.add_argument("--lane", default=None)
    p.add_argument("--fts", action="store_true", default=None, help="force FTS rebuild")
    p.set_defaults(fn=cmd_record)

    p = sub.add_parser("install", help="install capture for an agent + the 60s scheduler")
    p.add_argument("agent", choices=["cc", "codex", "pi"])
    p.set_defaults(fn=cmd_install)

    p = sub.add_parser("uninstall", help="remove hooks + scheduler; the floor is kept")
    p.set_defaults(fn=cmd_uninstall)

    p = sub.add_parser("health", help="per-lane freshness (unknown ≠ healthy)")
    p.add_argument("--exit-nonzero-on-stale", action="store_true")
    p.set_defaults(fn=cmd_health)

    p = sub.add_parser("backup", help="sync lake+manifest to dest, verify by re-hash")
    p.add_argument("dest")
    p.set_defaults(fn=cmd_backup)

    p = sub.add_parser("redact", help="replace a span with a tombstone; audit in manifest")
    p.add_argument("file")
    p.add_argument("--lines", nargs=2, type=int, required=True, metavar=("FROM", "TO"))
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_redact)

    p = sub.add_parser("migrate-v1", help="import v1 all_conversations.parquet (marked v1_derived)")
    p.add_argument("parquet")
    p.set_defaults(fn=cmd_migrate_v1)

    p = sub.add_parser("scan-secrets", help="find credentials in the floor (values never printed)")
    p.add_argument("--lane", default=None)
    p.add_argument("--exit-nonzero-on-findings", action="store_true",
                   help="cron-able: makes 'no secrets in the floor' a claim that runs")
    p.set_defaults(fn=cmd_scan_secrets)

    p = sub.add_parser("doctor", help="capture status + health summary")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("serve", help="run the MCP server (stdio)")
    p.set_defaults(fn=cmd_serve)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
