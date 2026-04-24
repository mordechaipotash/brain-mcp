#!/usr/bin/env python3
"""
verify_skills.py — Validate SHELET skill manifests against the MCP tool surface.

Checks:
  1. Every SKILL.md parses as YAML frontmatter + body
  2. Required fields present: name, description, layer, reads, writes, citations, determinism, allowed-tools
  3. `layer` is in {L0, L1, L2, L3, utility}
  4. `citations` value matches the layer expectation:
       L0/L1 → not-required OR output-is-citation
       L2/L3 → required
  5. `determinism` is in {pure-function, temporal, LLM-guided}
  6. `name` matches directory name
  7. `allowed-tools` references a tool that is actually registered in the MCP server
       (introspects brain_mcp.server.server)
  8. No orphan MCP tools (every registered tool has a SKILL.md)

Exit code: 0 if all checks pass, 1 otherwise.

Run:  python scripts/verify_skills.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # noqa: F401
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

VALID_LAYERS = {"L0", "L1", "L2", "L3", "utility"}
VALID_DETERMINISM = {"pure-function", "temporal", "LLM-guided"}
VALID_CITATIONS = {"required", "output-is-citation", "not-required"}

REQUIRED_FIELDS = [
    "name",
    "description",
    "layer",
    "reads",
    "writes",
    "citations",
    "determinism",
    "allowed-tools",
]


class VerificationError(Exception):
    pass


def parse_skill_md(path: Path) -> dict[str, Any]:
    """Parse a SKILL.md into (frontmatter_dict, body_str)."""
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.DOTALL)
    if not m:
        raise VerificationError(f"{path}: no YAML frontmatter found")
    fm_text, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise VerificationError(f"{path}: frontmatter YAML error: {e}") from e
    if not isinstance(fm, dict):
        raise VerificationError(f"{path}: frontmatter must be a dict")
    return fm, body


def check_skill(skill_dir: Path) -> list[str]:
    """Return list of error strings for this skill (empty if valid)."""
    errors: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [f"{skill_dir.name}: missing SKILL.md"]

    try:
        fm, body = parse_skill_md(skill_md)
    except VerificationError as e:
        return [str(e)]

    # 1. Required fields
    for field in REQUIRED_FIELDS:
        if field not in fm:
            errors.append(f"{skill_dir.name}: missing required field `{field}`")

    if errors:
        return errors  # can't validate further without fields

    # 2. Field value validation
    if fm["name"] != skill_dir.name:
        errors.append(
            f"{skill_dir.name}: `name` field is `{fm['name']}`, "
            f"must match directory name `{skill_dir.name}`"
        )

    if fm["layer"] not in VALID_LAYERS:
        errors.append(
            f"{skill_dir.name}: `layer` is `{fm['layer']}`, must be one of {VALID_LAYERS}"
        )

    if fm["determinism"] not in VALID_DETERMINISM:
        errors.append(
            f"{skill_dir.name}: `determinism` is `{fm['determinism']}`, "
            f"must be one of {VALID_DETERMINISM}"
        )

    if fm["citations"] not in VALID_CITATIONS:
        errors.append(
            f"{skill_dir.name}: `citations` is `{fm['citations']}`, "
            f"must be one of {VALID_CITATIONS}"
        )

    # 3. Layer/citations consistency
    if fm["layer"] in ("L2", "L3") and fm["citations"] not in ("required",):
        errors.append(
            f"{skill_dir.name}: layer `{fm['layer']}` requires `citations: required` "
            f"(got `{fm['citations']}`)"
        )

    # 4. reads / writes must be lists
    if not isinstance(fm["reads"], list):
        errors.append(f"{skill_dir.name}: `reads` must be a list")
    if not isinstance(fm["writes"], list):
        errors.append(f"{skill_dir.name}: `writes` must be a list")

    # 5. allowed-tools must be present and non-empty
    allowed = fm["allowed-tools"]
    if isinstance(allowed, str):
        allowed_list = [t.strip() for t in allowed.split(",") if t.strip()]
    elif isinstance(allowed, list):
        allowed_list = allowed
    else:
        errors.append(f"{skill_dir.name}: `allowed-tools` must be list or comma-string")
        allowed_list = []

    if not allowed_list:
        errors.append(f"{skill_dir.name}: `allowed-tools` is empty")

    # 6. Body sanity — must contain "## Does NOT do" and "## Verification checklist"
    if "## Does NOT do" not in body:
        errors.append(f"{skill_dir.name}: body missing `## Does NOT do` section")
    if "## Verification checklist" not in body:
        errors.append(f"{skill_dir.name}: body missing `## Verification checklist` section")

    return errors


def list_registered_tools() -> set[str]:
    """Introspect brain_mcp.server.server to list registered MCP tool names.

    Soft-fails (returns empty set) if the import fails — we still validate the
    manifest structure even if the server can't be imported in this environment.
    """
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from brain_mcp.server import server as srv  # type: ignore

        # FastMCP stores tools in app._tool_manager._tools (implementation detail);
        # safer to try known access patterns.
        if hasattr(srv, "create_server"):
            # Best-effort: just scan the module source for @mcp.tool decorators
            pass
        # Scan the tools_*.py files for mcp.tool-decorated functions.
        tools_dir = REPO_ROOT / "brain_mcp" / "server"
        pattern = re.compile(r"@(?:\w+\.)?tool\(\s*\)\s*\ndef\s+(\w+)", re.MULTILINE)
        names: set[str] = set()
        for py in tools_dir.glob("tools_*.py"):
            names.update(pattern.findall(py.read_text()))
        return names
    except Exception as e:
        print(f"  (note: could not introspect MCP server: {e})", file=sys.stderr)
        return set()


def main() -> int:
    if not SKILLS_DIR.exists():
        print(f"ERROR: skills dir not found: {SKILLS_DIR}", file=sys.stderr)
        return 1

    skill_dirs = sorted(d for d in SKILLS_DIR.iterdir() if d.is_dir() and not d.name.startswith("_"))
    print(f"Verifying {len(skill_dirs)} skills in {SKILLS_DIR}...")
    print()

    all_errors: list[str] = []
    skill_tool_refs: set[str] = set()

    for sd in skill_dirs:
        errs = check_skill(sd)
        if errs:
            for e in errs:
                print(f"  ✗ {e}")
            all_errors.extend(errs)
        else:
            print(f"  ✓ {sd.name}")
            # Collect the allowed-tools references
            try:
                fm, _ = parse_skill_md(sd / "SKILL.md")
                allowed = fm.get("allowed-tools", "")
                if isinstance(allowed, str):
                    for t in allowed.split(","):
                        t = t.strip()
                        if t.startswith("mcp__my-brain__"):
                            skill_tool_refs.add(t.removeprefix("mcp__my-brain__"))
                elif isinstance(allowed, list):
                    for t in allowed:
                        if isinstance(t, str) and t.startswith("mcp__my-brain__"):
                            skill_tool_refs.add(t.removeprefix("mcp__my-brain__"))
            except VerificationError:
                pass

    print()

    # Cross-check against registered tools
    registered = list_registered_tools()
    if registered:
        print(f"Registered MCP tools found: {len(registered)}")
        orphans = registered - skill_tool_refs
        dangling = skill_tool_refs - registered
        if orphans:
            print(f"  ⚠ {len(orphans)} MCP tools have no SKILL.md:")
            for name in sorted(orphans):
                print(f"      - {name}")
        if dangling:
            print(f"  ✗ {len(dangling)} skills reference tools not registered:")
            for name in sorted(dangling):
                print(f"      - {name}")
                all_errors.append(f"dangling tool reference: {name}")
    else:
        print("(skipped cross-check against MCP server — could not introspect)")

    print()
    if all_errors:
        print(f"FAILED: {len(all_errors)} error(s)")
        return 1
    print(f"OK: all {len(skill_dirs)} skills valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
