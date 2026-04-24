"""Dashboard onboarding wizard API routes.

Provides:
- GET  /api/onboarding/status          — Check if onboarding is complete
- POST /api/onboarding/complete        — Mark onboarding done
- GET  /api/onboarding/discover        — Auto-detect conversation sources on disk (JSON)
- GET  /api/onboarding/mcp-config      — Generate MCP config JSON
- POST /api/onboarding/auto-configure  — Write config to Claude/Cursor config files
- POST /api/onboarding/configure-embedding — Save embedding provider choice
- POST /api/onboarding/configure-summaries — Save summary provider choice
"""

import json
import os
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["onboarding"])

# Onboarding state file (separate from main config to keep things simple)
ONBOARDING_STATE_PATH = Path.home() / ".brain-mcp" / "onboarding.json"


def _load_onboarding_state() -> dict:
    """Load onboarding state from disk."""
    try:
        if ONBOARDING_STATE_PATH.exists():
            return json.loads(ONBOARDING_STATE_PATH.read_text())
    except Exception:
        pass
    return {"complete": False, "current_step": 1}


def _save_onboarding_state(state: dict):
    """Persist onboarding state."""
    ONBOARDING_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ONBOARDING_STATE_PATH.write_text(json.dumps(state, indent=2))


def _get_mcp_config_json() -> dict:
    """Generate the MCP config JSON snippet for connecting clients."""
    # Find the python interpreter in the project venv or system
    venv_python = Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python"
    if venv_python.exists():
        python_path = str(venv_python)
    else:
        python_path = sys.executable

    server_module = "brain_mcp.server"

    # Check for config file
    config_flag = []
    for candidate in [
        Path.home() / ".config" / "brain-mcp" / "config.toml",
        Path.home() / ".config" / "brain-mcp" / "brain.yaml",
    ]:
        if candidate.exists():
            config_flag = ["--config", str(candidate)]
            break

    return {
        "mcpServers": {
            "brain-mcp": {
                "command": python_path,
                "args": ["-m", server_module] + config_flag,
            }
        }
    }


# ═══════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════

@router.get("/status")
async def onboarding_status():
    """Check onboarding completion status."""
    state = _load_onboarding_state()

    # Also check if data exists — if parquet exists, onboarding may already be done
    from brain_mcp.config import get_config
    try:
        cfg = get_config()
        has_data = cfg.parquet_path.exists()
    except Exception:
        has_data = False

    return {
        "complete": state.get("complete", False) or has_data,
        "current_step": state.get("current_step", 1),
        "has_data": has_data,
    }


# ═══════════════════════════════════════════════════════════════════
# COMPLETE
# ═══════════════════════════════════════════════════════════════════

@router.post("/complete")
async def onboarding_complete():
    """Mark onboarding as complete."""
    state = _load_onboarding_state()
    state["complete"] = True
    state["current_step"] = 5
    _save_onboarding_state(state)
    return {"status": "ok", "complete": True}


# ═══════════════════════════════════════════════════════════════════
# STEP PROGRESS
# ═══════════════════════════════════════════════════════════════════

@router.post("/step/{step}")
async def set_step(step: int):
    """Update the current step."""
    state = _load_onboarding_state()
    state["current_step"] = min(max(step, 1), 5)
    _save_onboarding_state(state)
    return {"status": "ok", "current_step": state["current_step"]}


# ═══════════════════════════════════════════════════════════════════
# SOURCE DISCOVERY
# ═══════════════════════════════════════════════════════════════════

# Known ingesters with module-level discover() functions.
# Maps source_type → (module_path, display_name).
_DISCOVER_MODULES = {
    "chatgpt-export": ("brain_mcp.ingest.chatgpt_export", "ChatGPT Export"),
    "cursor": ("brain_mcp.ingest.cursor", "Cursor"),
    "gemini-cli": ("brain_mcp.ingest.gemini_cli", "Gemini CLI"),
}

# Well-known paths for ingesters that don't have discover() yet.
_WELL_KNOWN_PATHS = [
    ("claude-code", "Claude Code", Path.home() / ".claude" / "projects", "*.jsonl"),
    ("clawdbot", "Clawdbot", Path.home() / ".clawdbot" / "agents", "*.jsonl"),
]

# Add Claude Desktop with platform-aware path
try:
    from brain_mcp.platform import claude_desktop_conversations
    _WELL_KNOWN_PATHS.append(
        ("claude-desktop", "Claude Desktop", claude_desktop_conversations(), "*.jsonl")
    )
except Exception:
    _WELL_KNOWN_PATHS.append(
        ("claude-desktop", "Claude Desktop", Path.home() / "Library" / "Application Support" / "Claude" / "chat_conversations", "*.jsonl")
    )


def _discover_sources() -> list[dict]:
    """Run source discovery across all known ingesters.

    Uses the ingester registry (discover_all) if populated, otherwise
    falls back to calling each ingester's module-level discover() plus
    well-known path checks for ingesters that lack discover().

    Returns a list of:
        {source_type, display_name, paths: [{path, count_hint}]}
    """
    results: list[dict] = []

    # ── Try the registry first ──────────────────────────────────
    try:
        from brain_mcp.ingest.registry import discover_all

        registry_results = discover_all()
        if registry_results:
            for source_type, items in registry_results.items():
                ingester = None
                try:
                    from brain_mcp.ingest.registry import get_ingester
                    ingester = get_ingester(source_type)
                except Exception:
                    pass

                display_name = (
                    ingester.display_name if ingester else source_type
                )
                paths = []
                for item in items:
                    paths.append(
                        {
                            "path": item.get("path", ""),
                            "count_hint": item.get("size", 0),
                        }
                    )
                results.append(
                    {
                        "source_type": source_type,
                        "display_name": display_name,
                        "paths": paths,
                    }
                )
            # If registry gave us results, return them
            if results:
                return results
    except Exception:
        pass

    # ── Fallback: call module-level discover() functions ────────
    seen_types: set[str] = set()

    for source_type, (module_path, display_name) in _DISCOVER_MODULES.items():
        try:
            import importlib

            mod = importlib.import_module(module_path)
            found = mod.discover()
            if found:
                paths = []
                for item in found:
                    paths.append(
                        {
                            "path": item.get("path", ""),
                            "count_hint": item.get("size", 0),
                        }
                    )
                results.append(
                    {
                        "source_type": source_type,
                        "display_name": display_name,
                        "paths": paths,
                    }
                )
                seen_types.add(source_type)
        except Exception:
            pass

    # ── Fallback: well-known paths for ingesters without discover() ──
    for source_type, display_name, base_path, glob_pattern in _WELL_KNOWN_PATHS:
        if source_type in seen_types:
            continue
        try:
            if base_path.exists():
                files = list(base_path.rglob(glob_pattern))
                if files:
                    results.append(
                        {
                            "source_type": source_type,
                            "display_name": display_name,
                            "paths": [
                                {
                                    "path": str(base_path),
                                    "count_hint": len(files),
                                }
                            ],
                        }
                    )
        except Exception:
            pass

    return results


@router.get("/discover")
async def discover():
    """Auto-detect conversation sources on disk.

    Returns JSON list of discovered sources:
        [{source_type, display_name, paths: [{path, count_hint}]}]
    """
    return _discover_sources()


# ═══════════════════════════════════════════════════════════════════
# MCP CONFIG
# ═══════════════════════════════════════════════════════════════════

@router.get("/mcp-config")
async def mcp_config():
    """Return the MCP config JSON for connecting clients."""
    return _get_mcp_config_json()


@router.get("/mcp-config/snippet", response_class=HTMLResponse)
async def mcp_config_snippet(request: Request):
    """Return formatted MCP config snippet as HTML partial."""
    config = _get_mcp_config_json()
    config_json = json.dumps(config, indent=2)
    templates = request.app.state.templates
    return templates.TemplateResponse("partials/mcp_config_snippet.html", {
        "request": request,
        "config_json": config_json,
    })


# ═══════════════════════════════════════════════════════════════════
# AUTO-CONFIGURE
# ═══════════════════════════════════════════════════════════════════

@router.post("/auto-configure")
async def auto_configure(request: Request):
    """Write MCP config to Claude/Cursor config files."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    target = body.get("target", "claude-desktop")
    config = _get_mcp_config_json()

    # Target config file paths
    try:
        from brain_mcp.platform import claude_desktop_config
        _desktop_cfg = claude_desktop_config()
    except Exception:
        _desktop_cfg = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    targets = {
        "claude-desktop": _desktop_cfg,
        "claude-code": Path.home() / ".claude" / "mcp.json",
        "cursor": Path.home() / ".cursor" / "mcp.json",
    }

    config_path = targets.get(target)
    if not config_path:
        return JSONResponse(
            {"error": f"Unknown target: {target}. Use: {', '.join(targets.keys())}"},
            status_code=400,
        )

    try:
        # Read existing config
        existing = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text())
            except Exception:
                # Backup corrupted file
                backup = config_path.with_suffix(".json.bak")
                shutil.copy2(config_path, backup)

        # Merge — add our server without overwriting others
        if "mcpServers" not in existing:
            existing["mcpServers"] = {}
        existing["mcpServers"]["brain-mcp"] = config["mcpServers"]["brain-mcp"]

        # Write
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(existing, indent=2))

        return {"status": "ok", "target": target, "path": str(config_path)}

    except Exception as e:
        return JSONResponse(
            {"error": f"Failed to write config: {e}"},
            status_code=500,
        )


# ═══════════════════════════════════════════════════════════════════
# EMBEDDING CONFIG (for step 3)
# ═══════════════════════════════════════════════════════════════════

@router.post("/configure-embedding")
async def configure_embedding(request: Request):
    """Save embedding provider choice."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    provider = body.get("provider", "skip")  # "local", "openrouter", "skip"
    api_key = body.get("api_key", "")

    state = _load_onboarding_state()
    state["embedding_provider"] = provider
    if provider == "openrouter" and api_key:
        state["openrouter_key"] = api_key
    _save_onboarding_state(state)

    return {"status": "ok", "provider": provider}


# ═══════════════════════════════════════════════════════════════════
# SUMMARY CONFIG (for step 4)
# ═══════════════════════════════════════════════════════════════════

@router.post("/configure-summaries")
async def configure_summaries(request: Request):
    """Save summary provider choice."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    provider = body.get("provider", "skip")  # "gemini", "openrouter", "skip"

    state = _load_onboarding_state()
    state["summary_provider"] = provider
    _save_onboarding_state(state)

    return {"status": "ok", "provider": provider}
