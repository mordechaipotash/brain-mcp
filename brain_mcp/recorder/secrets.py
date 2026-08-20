"""Credential scanning over the floor — because a bytes-forever floor replicates
whatever it captured into every backup, forever.

Design rules, each bought by a real failure:
  - NEVER print a secret value. Findings carry a sha256 prefix, a shape name,
    and a location. The scan output is safe to paste anywhere.
  - Known-example values (AWS's canonical docs key, obvious fakes) are excluded
    by value, not by guesswork — a scanner that cries wolf gets ignored.
  - Report, never auto-redact. Redaction rewrites the floor; that stays an
    explicit human verb (`brain-mcp redact`).
  - The exit code is the product: `--exit-nonzero-on-findings` makes this
    cron-able, so "no secrets in the floor" becomes a claim that runs.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path

from .paths import brain_home, lake_dir
from .floor_db import read_conn

# shape -> (compiled pattern, human name)
PATTERNS: dict[str, re.Pattern[str]] = {
    "github_pat": re.compile(r"ghp_[0-9A-Za-z]{36}"),
    "github_oauth": re.compile(r"gho_[0-9A-Za-z]{36}"),
    "supabase_pat": re.compile(r"sbp_[0-9a-f]{40}"),
    "anthropic_key": re.compile(r"sk-ant-api03-[0-9A-Za-z_\-]{40,}"),
    "openai_key": re.compile(r"sk-proj-[0-9A-Za-z_\-]{40,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "slack_token": re.compile(r"xox[bpsa]-[0-9A-Za-z\-]{20,}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "jwt": re.compile(r"eyJhbGciOi[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),
}

# Values that LOOK like credentials and provably are not. Excluded by value.
KNOWN_EXAMPLES = {
    "AKIAIOSFODNN7EXAMPLE",          # AWS's own documentation key
    "AKIAAAAAAAAAAAAAAAAA",
}
FAKE_MARKERS = ("example", "fake", "dummy", "placeholder", "xxxxxxx", "your-key", "redacted")


def _is_example(value: str, context: str = "") -> bool:
    """A match is an example if the VALUE is a known one, or if the value itself
    or the text immediately around it is marked fake.

    The context window matters: a placeholder is usually annotated NEXT to the
    token ("sbp_aaa… <- fake"), not inside it. Checking only the matched
    substring made this function unable to recognize the very placeholders it
    exists to skip — found by a falsification test, 2026-08-20.
    """
    if value in KNOWN_EXAMPLES:
        return True
    if any(m in value.lower() for m in FAKE_MARKERS):
        return True
    if context:
        # Only the SAME LINE counts as annotation. A wider window silently
        # suppressed a real planted secret because an unrelated line nearby
        # said "fake" — measured 2026-08-20. Proximity is not evidence;
        # sharing a line is.
        i = context.find(value)
        if i != -1:
            start = context.rfind("\n", 0, i) + 1
            end = context.find("\n", i)
            line = context[start : end if end != -1 else len(context)].lower()
            return any(m in line for m in FAKE_MARKERS)
    return False


def digest(value: str) -> str:
    """Stable, non-reversible handle for a finding. Safe to print and to diff
    across runs — the same secret always yields the same id."""
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def scan(lanes: list[str] | None = None, include_origin: bool = False) -> dict:
    """Scan the floor (and optionally the agents' own stores) for credentials.

    Returns a report of DISTINCT findings: {shape, id, occurrences, files, first_line}.
    No values, ever.
    """
    home = brain_home()
    targets: list[Path] = []
    with read_conn() as c:
        known = [r[0] for r in c.execute("SELECT lane FROM floor.lanes").fetchall()]
    for lane in lanes or known:
        d = lake_dir(lane)
        if d.exists():
            targets += sorted(d.glob("*.jsonl"))

    findings: dict[tuple[str, str], dict] = {}
    scanned_bytes = 0

    for f in targets:
        try:
            raw = f.read_text(errors="replace")
        except Exception:
            continue
        scanned_bytes += len(raw)
        for shape, pat in PATTERNS.items():
            for m in set(pat.findall(raw)):
                if _is_example(m, raw):
                    continue
                key = (shape, digest(m))
                entry = findings.setdefault(
                    key,
                    {"shape": shape, "id": digest(m), "occurrences": 0, "files": [], "first_line": None},
                )
                entry["occurrences"] += raw.count(m)
                rel = str(f.relative_to(home))
                if rel not in entry["files"]:
                    entry["files"].append(rel)
                if entry["first_line"] is None:
                    entry["first_line"] = raw[: raw.index(m)].count("\n") + 1

    by_shape: dict[str, int] = defaultdict(int)
    for (shape, _), _e in findings.items():
        by_shape[shape] += 1

    return {
        "scanned_files": len(targets),
        "scanned_bytes": scanned_bytes,
        "distinct_findings": len(findings),
        "by_shape": dict(by_shape),
        "findings": sorted(findings.values(), key=lambda e: -e["occurrences"]),
        "note": (
            "Values are never printed — each finding carries a sha256 prefix so you can "
            "match it across runs and confirm a rotation actually removed it. "
            "To remove one from the floor: brain-mcp redact <file> --lines A B --reason '...'. "
            "Redaction does not rotate the secret — rotate it at the source first."
        ),
    }
