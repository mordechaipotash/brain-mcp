"""Dashboard tool status API routes.

Provides:
- GET  /api/tools              — List all tools with status/category/requirements
- GET  /api/tools/{name}/test  — Probe one tool, return latency + result
- POST /api/tools/test-all     — Test all tools, returns task ID for SSE
- POST /api/tools/{name}/run   — Run tool with custom params
- GET  /api/tools/cards        — HTML partial for tool cards
"""

import asyncio
import threading
import time
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter(tags=["tools"])


# ═══════════════════════════════════════════════════════════════════
# TOOL REGISTRY — all 25 Brain MCP tools
# ═══════════════════════════════════════════════════════════════════

TOOLS = [
    # Cognitive Prosthetic (8)
    {
        "name": "tunnel_state",
        "description": "Reconstruct save-state for a domain",
        "category": "Cognitive Prosthetic",
        "requires": ["summaries"],
        "params": {"domain": "str", "limit": "int=10"},
        "probe": {"domain": "ai-dev"},
    },
    {
        "name": "dormant_contexts",
        "description": "Find abandoned tunnels / dormant domains",
        "category": "Cognitive Prosthetic",
        "requires": ["summaries"],
        "params": {"min_importance": "float=0", "limit": "int=5"},
        "probe": {},
    },
    {
        "name": "context_recovery",
        "description": "Full re-entry brief for picking up a domain",
        "category": "Cognitive Prosthetic",
        "requires": ["summaries"],
        "params": {"domain": "str", "summary_count": "int=5"},
        "probe": {"domain": "ai-dev"},
    },
    {
        "name": "tunnel_history",
        "description": "Engagement meta-view over time for a domain",
        "category": "Cognitive Prosthetic",
        "requires": ["summaries"],
        "params": {"domain": "str"},
        "probe": {"domain": "ai-dev"},
    },
    {
        "name": "switching_cost",
        "description": "Quantified penalty for switching between domains",
        "category": "Cognitive Prosthetic",
        "requires": ["summaries"],
        "params": {"current_domain": "str", "target_domain": "str"},
        "probe": {"current_domain": "ai-dev", "target_domain": "frontend-dev"},
    },
    {
        "name": "cognitive_patterns",
        "description": "When do you think best? Temporal analysis",
        "category": "Cognitive Prosthetic",
        "requires": ["conversations"],
        "params": {"domain": "str=None"},
        "probe": {},
    },
    {
        "name": "open_threads",
        "description": "Global unfinished business across all domains",
        "category": "Cognitive Prosthetic",
        "requires": ["summaries"],
        "params": {"limit_per_domain": "int=3", "max_domains": "int=10"},
        "probe": {},
    },
    {
        "name": "trust_dashboard",
        "description": "System-wide proof the safety net works",
        "category": "Cognitive Prosthetic",
        "requires": ["conversations", "summaries"],
        "params": {},
        "probe": {},
    },

    # Search (6)
    {
        "name": "search_conversations",
        "description": "Keyword search across all conversations",
        "category": "Search",
        "requires": ["conversations"],
        "params": {"term": "str", "role": "str=None", "limit": "int=20"},
        "probe": {"term": "hello"},
    },
    {
        "name": "semantic_search",
        "description": "Vector search via LanceDB embeddings",
        "category": "Search",
        "requires": ["embeddings"],
        "params": {"query": "str"},
        "probe": {"query": "how to get started"},
    },
    {
        "name": "unified_search",
        "description": "Search across conversations, GitHub, and markdown",
        "category": "Search",
        "requires": ["conversations"],
        "params": {"query": "str"},
        "probe": {"query": "test"},
    },
    {
        "name": "search_summaries",
        "description": "Search structured summaries by topic",
        "category": "Search",
        "requires": ["summaries"],
        "params": {"query": "str", "extract": "str=None"},
        "probe": {"query": "productivity"},
    },
    {
        "name": "search_docs",
        "description": "Search markdown corpus",
        "category": "Search",
        "requires": ["conversations"],
        "params": {"query": "str", "filter": "str=None"},
        "probe": {"query": "setup"},
    },
    {
        "name": "unfinished_threads",
        "description": "Find threads with open questions",
        "category": "Search",
        "requires": ["summaries"],
        "params": {"domain": "str=None"},
        "probe": {},
    },

    # Synthesis (4)
    {
        "name": "what_do_i_think",
        "description": "Synthesize views on a topic",
        "category": "Synthesis",
        "requires": ["conversations", "summaries"],
        "params": {"topic": "str", "mode": "str=synthesize"},
        "probe": {"topic": "AI"},
    },
    {
        "name": "alignment_check",
        "description": "Check decision alignment with principles",
        "category": "Synthesis",
        "requires": ["principles"],
        "params": {"decision": "str"},
        "probe": {"decision": "test decision"},
    },
    {
        "name": "thinking_trajectory",
        "description": "Track how thinking evolved on a topic",
        "category": "Synthesis",
        "requires": ["conversations"],
        "params": {"topic": "str", "view": "str=full"},
        "probe": {"topic": "AI"},
    },
    {
        "name": "what_was_i_thinking",
        "description": "Month-level snapshot of activity",
        "category": "Synthesis",
        "requires": ["conversations"],
        "params": {"month": "str"},
        "probe": {"month": "2025-01"},
    },

    # Conversation (3)
    {
        "name": "get_conversation",
        "description": "Retrieve full conversation by ID",
        "category": "Conversation",
        "requires": ["conversations"],
        "params": {"id": "str"},
        "probe": None,  # Can't probe without a real ID
    },
    {
        "name": "conversations_by_date",
        "description": "What happened on a specific date",
        "category": "Conversation",
        "requires": ["conversations"],
        "params": {"date": "str"},
        "probe": {"date": "2025-01-15"},
    },
    {
        "name": "brain_stats",
        "description": "Brain statistics (7 views)",
        "category": "Conversation",
        "requires": ["conversations"],
        "params": {"view": "str=overview"},
        "probe": {"view": "overview"},
    },

    # GitHub (1)
    {
        "name": "github_search",
        "description": "Search GitHub repos, commits, and code",
        "category": "GitHub",
        "requires": ["github"],
        "params": {"project": "str=None", "query": "str=None", "mode": "str=None"},
        "probe": {},
    },

    # Analytics (1)
    {
        "name": "query_analytics",
        "description": "Timeline, stacks, problems, spend analysis",
        "category": "Analytics",
        "requires": ["conversations"],
        "params": {"view": "str", "date": "str=None"},
        "probe": {"view": "summary"},
    },

    # Meta (2)
    {
        "name": "list_principles",
        "description": "List your configured principles",
        "category": "Meta",
        "requires": ["principles"],
        "params": {},
        "probe": {},
    },
    {
        "name": "get_principle",
        "description": "Get detailed info about a specific principle",
        "category": "Meta",
        "requires": ["principles"],
        "params": {"name": "str"},
        "probe": None,  # Needs a real principle name
    },
]


def _check_data_available() -> dict[str, bool]:
    """Check which data layers are available."""
    available = {
        "conversations": False,
        "embeddings": False,
        "summaries": False,
        "github": False,
        "principles": False,
    }

    try:
        from brain_mcp.config import get_config
        cfg = get_config()

        available["conversations"] = cfg.parquet_path.exists()
        available["embeddings"] = cfg.lance_path.exists()
        available["summaries"] = cfg.summaries_parquet.exists()
        available["github"] = cfg.github_repos_parquet.exists()
        available["principles"] = (
            cfg.principles_path is not None and cfg.principles_path.exists()
        )
    except Exception:
        pass

    return available


def _tool_status(tool: dict, available: dict[str, bool]) -> str:
    """Determine tool status: ok, degraded, unavailable."""
    requires = tool.get("requires", [])
    if not requires:
        return "ok"

    missing = [r for r in requires if not available.get(r, False)]
    if not missing:
        return "ok"
    if len(missing) == len(requires):
        return "unavailable"
    return "degraded"


def _status_icon(status: str) -> str:
    """Return status icon."""
    return {"ok": "✅", "degraded": "⚠️", "unavailable": "❌"}.get(status, "❓")


# ═══════════════════════════════════════════════════════════════════
# LIST TOOLS
# ═══════════════════════════════════════════════════════════════════

@router.get("", response_class=JSONResponse)
async def list_tools():
    """List all tools with status and requirements."""
    available = _check_data_available()
    results = []

    for tool in TOOLS:
        status = _tool_status(tool, available)
        missing = [r for r in tool.get("requires", []) if not available.get(r, False)]
        results.append({
            "name": tool["name"],
            "description": tool["description"],
            "category": tool["category"],
            "status": status,
            "status_icon": _status_icon(status),
            "requires": tool.get("requires", []),
            "missing": missing,
            "params": tool.get("params", {}),
            "testable": tool.get("probe") is not None,
        })

    return results


# ═══════════════════════════════════════════════════════════════════
# TOOL CARDS (HTML partial)
# ═══════════════════════════════════════════════════════════════════

@router.get("/cards", response_class=HTMLResponse)
async def tool_cards(request: Request):
    """Tool cards grouped by category — HTML partial for htmx."""
    templates = request.app.state.templates
    available = _check_data_available()

    # Group tools by category
    categories = {}
    total_ok = 0
    total_degraded = 0
    total_unavailable = 0

    for tool in TOOLS:
        status = _tool_status(tool, available)
        missing = [r for r in tool.get("requires", []) if not available.get(r, False)]

        tool_data = {
            **tool,
            "status": status,
            "status_icon": _status_icon(status),
            "missing": missing,
            "testable": tool.get("probe") is not None,
        }

        cat = tool["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tool_data)

        if status == "ok":
            total_ok += 1
        elif status == "degraded":
            total_degraded += 1
        else:
            total_unavailable += 1

    return templates.TemplateResponse("partials/tool_cards.html", {
        "request": request,
        "categories": categories,
        "available": available,
        "total": len(TOOLS),
        "total_ok": total_ok,
        "total_degraded": total_degraded,
        "total_unavailable": total_unavailable,
    })


# ═══════════════════════════════════════════════════════════════════
# TEST ONE TOOL
# ═══════════════════════════════════════════════════════════════════

@router.get("/{name}/test", response_class=HTMLResponse)
async def test_tool(request: Request, name: str):
    """Probe one tool and return result as HTML."""
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if not tool:
        return HTMLResponse(f'<small style="color:#f85149;">❌ Unknown tool: {name}</small>')

    if tool.get("probe") is None:
        return HTMLResponse(f'<small style="color:#d29922;">⚠️ No probe configured for {name}</small>')

    available = _check_data_available()
    status = _tool_status(tool, available)
    if status == "unavailable":
        missing = [r for r in tool.get("requires", []) if not available.get(r, False)]
        return HTMLResponse(
            f'<small style="color:#f85149;">❌ Missing: {", ".join(missing)}</small>'
        )

    # Try to call the tool via the MCP server's registered functions
    start = time.time()
    try:
        result = _call_tool(name, tool["probe"])
        latency_ms = int((time.time() - start) * 1000)

        # Truncate result for display
        result_str = str(result)
        if len(result_str) > 300:
            result_str = result_str[:300] + "..."

        return HTMLResponse(
            f'<small style="color:#3fb950;">✅ {latency_ms}ms</small>'
            f'<details><summary><small>Result</small></summary>'
            f'<pre><code>{_escape_html(result_str)}</code></pre></details>'
        )
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        return HTMLResponse(
            f'<small style="color:#f85149;">❌ {latency_ms}ms — {_escape_html(str(e)[:200])}</small>'
        )


# ═══════════════════════════════════════════════════════════════════
# TEST ALL TOOLS
# ═══════════════════════════════════════════════════════════════════

@router.post("/test-all", response_class=HTMLResponse)
async def test_all_tools(request: Request):
    """Test all tools in background. Returns task ID for polling."""
    from brain_mcp.dashboard.tasks import task_manager, TaskStatus

    task = task_manager.create("test-all-tools")
    loop = asyncio.get_event_loop()
    task_manager.set_loop(loop)

    def run_tests():
        available = _check_data_available()
        testable = [t for t in TOOLS if t.get("probe") is not None]
        total = len(testable)
        results = []

        task_manager.update_sync(task.id,
                                 status=TaskStatus.RUNNING,
                                 started=datetime.now(),
                                 message=f"Testing {total} tools...")

        for i, tool in enumerate(testable):
            name = tool["name"]
            status = _tool_status(tool, available)

            if status == "unavailable":
                results.append({"name": name, "status": "skip", "reason": "missing data"})
                continue

            start = time.time()
            try:
                _call_tool(name, tool["probe"])
                latency = int((time.time() - start) * 1000)
                results.append({"name": name, "status": "ok", "latency_ms": latency})
            except Exception as e:
                latency = int((time.time() - start) * 1000)
                results.append({"name": name, "status": "fail", "error": str(e)[:100], "latency_ms": latency})

            progress = (i + 1) / total
            ok_count = sum(1 for r in results if r["status"] == "ok")
            fail_count = sum(1 for r in results if r["status"] == "fail")
            task_manager.update_sync(task.id,
                                     progress=progress,
                                     message=f"Tested {i+1}/{total} — ✅ {ok_count} ❌ {fail_count}")

        ok_count = sum(1 for r in results if r["status"] == "ok")
        fail_count = sum(1 for r in results if r["status"] == "fail")
        skip_count = sum(1 for r in results if r["status"] == "skip")
        task_manager.update_sync(task.id,
                                 status=TaskStatus.DONE,
                                 progress=1.0,
                                 finished=datetime.now(),
                                 message=f"Done: ✅ {ok_count} ❌ {fail_count} ⏭️ {skip_count}")

    thread = threading.Thread(target=run_tests, daemon=True)
    thread.start()

    return HTMLResponse(
        f'<div id="test-all-progress" '
        f'hx-get="/api/tasks/{task.id}" '
        f'hx-trigger="every 1s" '
        f'hx-target="#test-all-progress" '
        f'hx-swap="innerHTML">'
        f'<p aria-busy="true">Testing tools...</p>'
        f'</div>'
    )


# ═══════════════════════════════════════════════════════════════════
# RUN TOOL (interactive)
# ═══════════════════════════════════════════════════════════════════

@router.post("/{name}/run", response_class=HTMLResponse)
async def run_tool(request: Request, name: str):
    """Run a tool with custom parameters. Expects JSON body."""
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if not tool:
        return HTMLResponse(f'<small style="color:#f85149;">❌ Unknown tool: {name}</small>')

    try:
        body = await request.json()
    except Exception:
        body = {}

    # Filter out empty params
    params = {k: v for k, v in body.items() if v not in (None, "", [])}

    start = time.time()
    try:
        result = _call_tool(name, params)
        latency_ms = int((time.time() - start) * 1000)

        result_str = str(result)
        if len(result_str) > 2000:
            result_str = result_str[:2000] + "\n... (truncated)"

        return HTMLResponse(
            f'<small style="color:#3fb950;">✅ {latency_ms}ms</small>'
            f'<pre><code>{_escape_html(result_str)}</code></pre>'
        )
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        return HTMLResponse(
            f'<small style="color:#f85149;">❌ {latency_ms}ms</small>'
            f'<pre><code>{_escape_html(str(e))}</code></pre>'
        )


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _call_tool(name: str, params: dict) -> str:
    """Call a Brain MCP tool by name with given parameters.

    Creates a temporary MCP server and invokes the tool function directly.
    """
    from brain_mcp.server.server import create_server

    mcp = create_server()

    # Get the tool function from FastMCP's internal registry
    if hasattr(mcp, "_tool_manager"):
        tool_mgr = mcp._tool_manager
        for tool in tool_mgr.list_tools():
            if tool.name == name:
                # FastMCP tools have a .fn attribute
                fn = tool.fn if hasattr(tool, "fn") else None
                if fn:
                    return fn(**params)
    elif hasattr(mcp, "list_tools"):
        for tool in mcp.list_tools():
            if tool.name == name:
                fn = tool.fn if hasattr(tool, "fn") else None
                if fn:
                    return fn(**params)

    raise ValueError(f"Tool '{name}' not found in MCP server registry")


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
