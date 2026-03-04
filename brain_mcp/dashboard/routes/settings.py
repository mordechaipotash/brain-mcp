"""Dashboard settings API routes.

Provides:
- GET    /api/settings              — Current config as JSON
- PUT    /api/settings              — Partial update (merge with existing)
- GET    /api/settings/disk         — Disk usage breakdown
- GET    /api/settings/cron         — Cron job status
- POST   /api/settings/cron/install — Install cron entries
- DELETE /api/settings/cron         — Remove cron entries
- POST   /api/settings/validate-key — Test an API key
- GET    /api/settings/cards        — HTML partial for settings sections
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["settings"])

CONFIG_PATH = Path.home() / ".config" / "brain-mcp" / "config.toml"
CONFIG_PATH_YAML = Path.home() / ".config" / "brain-mcp" / "brain.yaml"


def _find_active_config_path() -> Path:
    """Find the active config file path."""
    from brain_mcp.config import _find_config_path
    path = _find_config_path()
    if path and path.exists():
        return path
    # Default to TOML location
    return CONFIG_PATH


def _get_config_dict() -> dict:
    """Load current config as raw dict."""
    path = _find_active_config_path()
    if not path.exists():
        return {}
    try:
        from brain_mcp.config import _load_raw
        return _load_raw(path)
    except Exception:
        return {}


def _save_config_toml(data: dict):
    """Write config dict to TOML file."""
    try:
        import tomli_w
    except ImportError:
        raise ImportError("tomli_w required for writing TOML. Install: pip install tomli-w")

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Backup existing
    if CONFIG_PATH.exists():
        backup = CONFIG_PATH.with_suffix(".toml.bak")
        shutil.copy2(CONFIG_PATH, backup)

    with open(CONFIG_PATH, "wb") as f:
        tomli_w.dump(data, f)


def _deep_merge(base: dict, update: dict) -> dict:
    """Deep merge update into base dict."""
    result = base.copy()
    for key, value in update.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _dir_size(path: Path) -> int:
    """Get total size of a directory or file in bytes."""
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _format_size(bytes_val: int) -> str:
    """Format bytes as human-readable string."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / 1024 / 1024:.1f} MB"
    return f"{bytes_val / 1024 / 1024 / 1024:.1f} GB"


# ═══════════════════════════════════════════════════════════════════
# GET / PUT SETTINGS
# ═══════════════════════════════════════════════════════════════════

@router.get("")
async def get_settings():
    """Return current config as JSON."""
    raw = _get_config_dict()

    # Augment with resolved paths and status
    from brain_mcp.config import get_config
    try:
        cfg = get_config()
        raw["_resolved"] = {
            "data_dir": str(cfg.data_dir),
            "vectors_dir": str(cfg.vectors_dir),
            "parquet_exists": cfg.parquet_path.exists(),
            "embeddings_exist": cfg.lance_path.exists(),
            "summaries_exist": cfg.summaries_parquet.exists(),
            "config_path": str(_find_active_config_path()),
        }
    except Exception:
        pass

    return raw


@router.put("")
async def update_settings(request: Request):
    """Partial update — merge incoming JSON with existing config."""
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)

    current = _get_config_dict()
    merged = _deep_merge(current, update)

    try:
        _save_config_toml(merged)
    except ImportError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    except Exception as e:
        return JSONResponse({"error": f"Failed to save: {e}"}, status_code=500)

    # Reload config singleton
    from brain_mcp.config import load_config, set_config
    try:
        new_cfg = load_config(str(CONFIG_PATH))
        set_config(new_cfg)
    except Exception:
        pass

    return {"status": "ok", "config": merged}


# ═══════════════════════════════════════════════════════════════════
# DISK USAGE
# ═══════════════════════════════════════════════════════════════════

@router.get("/disk")
async def disk_usage():
    """Disk usage breakdown."""
    from brain_mcp.config import get_config
    try:
        cfg = get_config()
        parquet_size = _dir_size(cfg.parquet_path)
        vectors_size = _dir_size(cfg.lance_path)
        summaries_size = _dir_size(cfg.summaries_parquet)
        summaries_lance_size = _dir_size(cfg.summaries_lance)
        github_size = _dir_size(cfg.github_repos_parquet) + _dir_size(cfg.github_commits_parquet)
        total = parquet_size + vectors_size + summaries_size + summaries_lance_size + github_size

        return {
            "data_dir": str(cfg.data_dir),
            "parquet": {"bytes": parquet_size, "display": _format_size(parquet_size)},
            "vectors": {"bytes": vectors_size, "display": _format_size(vectors_size)},
            "summaries": {"bytes": summaries_size + summaries_lance_size,
                          "display": _format_size(summaries_size + summaries_lance_size)},
            "github": {"bytes": github_size, "display": _format_size(github_size)},
            "total": {"bytes": total, "display": _format_size(total)},
        }
    except Exception as e:
        return {"error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# EMBEDDING / SUMMARY STATUS
# ═══════════════════════════════════════════════════════════════════

@router.get("/embedding-status")
async def embedding_status():
    """Return embedding progress."""
    from brain_mcp.config import get_config
    try:
        cfg = get_config()
        total_messages = 0
        embedded = 0

        if cfg.parquet_path.exists():
            from brain_mcp.server.db import get_conversations
            con = get_conversations()
            total_messages = con.execute(
                "SELECT COUNT(*) FROM conversations WHERE role = 'user'"
            ).fetchone()[0]

        if cfg.lance_path.exists():
            from brain_mcp.server.db import lance_count
            embedded = lance_count("message")

        pct = round(embedded / total_messages * 100, 1) if total_messages else 0
        return {
            "total": total_messages,
            "embedded": embedded,
            "remaining": max(0, total_messages - embedded),
            "percent": pct,
        }
    except Exception as e:
        return {"total": 0, "embedded": 0, "remaining": 0, "percent": 0, "error": str(e)}


@router.get("/summary-status")
async def summary_status():
    """Return summary generation progress."""
    from brain_mcp.config import get_config
    try:
        cfg = get_config()
        total_conversations = 0
        summarized = 0

        if cfg.parquet_path.exists():
            from brain_mcp.server.db import get_conversations
            con = get_conversations()
            total_conversations = con.execute(
                "SELECT COUNT(DISTINCT conversation_id) FROM conversations"
            ).fetchone()[0]

        if cfg.summaries_parquet.exists():
            from brain_mcp.server.db import get_summaries_db
            sdb = get_summaries_db()
            if sdb:
                summarized = sdb.execute(
                    "SELECT COUNT(*) FROM summaries"
                ).fetchone()[0]

        pct = round(summarized / total_conversations * 100, 1) if total_conversations else 0
        return {
            "total": total_conversations,
            "summarized": summarized,
            "remaining": max(0, total_conversations - summarized),
            "percent": pct,
        }
    except Exception as e:
        return {"total": 0, "summarized": 0, "remaining": 0, "percent": 0, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# CRON MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

BRAIN_CRON_MARKER = "# brain-mcp"


@router.get("/cron")
async def get_cron():
    """Check cron job status."""
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return {"installed": False, "entries": [], "raw": ""}

        lines = result.stdout.strip().split("\n")
        brain_entries = [l for l in lines if BRAIN_CRON_MARKER in l]

        return {
            "installed": len(brain_entries) > 0,
            "entries": brain_entries,
            "count": len(brain_entries),
            "raw": result.stdout,
        }
    except Exception as e:
        return {"installed": False, "entries": [], "error": str(e)}


@router.post("/cron/install")
async def install_cron():
    """Install brain-mcp cron entries."""
    try:
        # Get current crontab
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        )
        existing = result.stdout if result.returncode == 0 else ""

        # Remove any existing brain-mcp entries
        lines = [l for l in existing.strip().split("\n") if BRAIN_CRON_MARKER not in l]
        lines = [l for l in lines if l.strip()]  # Remove empty lines

        # Find the python and project paths
        venv_python = Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python"
        project_dir = Path(__file__).resolve().parents[3]

        python_path = str(venv_python) if venv_python.exists() else "python3"

        # Add brain-mcp cron entries
        entries = [
            f"5 * * * * cd {project_dir} && {python_path} -m brain_mcp.ingest 2>/dev/null {BRAIN_CRON_MARKER} quick-sync",
            f"0 3 * * * cd {project_dir} && {python_path} -m brain_mcp.ingest --embed 2>/dev/null {BRAIN_CRON_MARKER} full-sync",
        ]

        lines.extend(entries)
        new_crontab = "\n".join(lines) + "\n"

        # Write new crontab
        proc = subprocess.run(
            ["crontab", "-"],
            input=new_crontab, text=True, capture_output=True, timeout=5
        )
        if proc.returncode != 0:
            return JSONResponse(
                {"error": f"crontab install failed: {proc.stderr}"},
                status_code=500,
            )

        return {"status": "ok", "entries": entries}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.delete("/cron")
async def remove_cron():
    """Remove brain-mcp cron entries."""
    try:
        result = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return {"status": "ok", "message": "No crontab found"}

        lines = [l for l in result.stdout.strip().split("\n") if BRAIN_CRON_MARKER not in l]
        lines = [l for l in lines if l.strip()]

        new_crontab = "\n".join(lines) + "\n" if lines else ""

        proc = subprocess.run(
            ["crontab", "-"],
            input=new_crontab, text=True, capture_output=True, timeout=5
        )
        if proc.returncode != 0:
            return JSONResponse(
                {"error": f"crontab update failed: {proc.stderr}"},
                status_code=500,
            )

        return {"status": "ok", "removed": True}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ═══════════════════════════════════════════════════════════════════
# VALIDATE API KEY
# ═══════════════════════════════════════════════════════════════════

@router.post("/validate-key")
async def validate_key(request: Request):
    """Test an API key (currently OpenRouter)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    key = body.get("key", "")
    if not key:
        return {"valid": False, "error": "No key provided"}

    # Quick validation via OpenRouter /auth/key endpoint
    try:
        import httpx
        resp = httpx.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return {
                "valid": True,
                "label": data.get("label", ""),
                "credit_remaining": data.get("limit_remaining"),
            }
        return {"valid": False, "error": f"HTTP {resp.status_code}"}
    except ImportError:
        # httpx not available, do basic format check
        if key.startswith("sk-or-"):
            return {"valid": True, "note": "Format looks correct (couldn't verify online)"}
        return {"valid": False, "error": "Key format doesn't look like OpenRouter"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# MCP CONFIG
# ═══════════════════════════════════════════════════════════════════

@router.get("/mcp-config", response_class=HTMLResponse)
async def mcp_config_snippet(request: Request):
    """Return MCP config snippet as HTML."""
    from brain_mcp.dashboard.routes.onboarding import _get_mcp_config_json
    config = _get_mcp_config_json()
    config_json = json.dumps(config, indent=2)
    templates = request.app.state.templates
    return templates.TemplateResponse("partials/mcp_config_snippet.html", {
        "request": request,
        "config_json": config_json,
    })


# ═══════════════════════════════════════════════════════════════════
# SETTINGS CARDS (HTML partial)
# ═══════════════════════════════════════════════════════════════════

@router.get("/cards", response_class=HTMLResponse)
async def settings_cards(request: Request):
    """Settings sections as HTML partial for htmx."""
    templates = request.app.state.templates

    # Gather all status info
    from brain_mcp.config import get_config
    try:
        cfg = get_config()
    except Exception:
        cfg = None

    config_dict = _get_config_dict()
    config_path = str(_find_active_config_path())

    # Disk usage
    disk = {}
    if cfg:
        disk = {
            "data_dir": str(cfg.data_dir),
            "parquet": _format_size(_dir_size(cfg.parquet_path)),
            "vectors": _format_size(_dir_size(cfg.lance_path)),
            "summaries": _format_size(
                _dir_size(cfg.summaries_parquet) + _dir_size(cfg.summaries_lance)
            ),
            "total": _format_size(
                _dir_size(cfg.parquet_path) +
                _dir_size(cfg.lance_path) +
                _dir_size(cfg.summaries_parquet) +
                _dir_size(cfg.summaries_lance)
            ),
        }

    # Embedding config
    embedding = {
        "model": cfg.embedding.model if cfg else "unknown",
        "dim": cfg.embedding.dim if cfg else 0,
    }

    # Summary config
    summarizer = {
        "enabled": cfg.summarizer.enabled if cfg else False,
        "provider": cfg.summarizer.provider if cfg else "none",
        "model": cfg.summarizer.model if cfg else "",
    }

    return templates.TemplateResponse("partials/settings_cards.html", {
        "request": request,
        "config_path": config_path,
        "config_dict": config_dict,
        "disk": disk,
        "embedding": embedding,
        "summarizer": summarizer,
    })
