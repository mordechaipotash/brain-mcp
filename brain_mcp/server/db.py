"""
brain-mcp — Database connection management.

Lazy-loaded, cached connections to DuckDB (parquet queries),
LanceDB (vector search), and the embedding model.

All connections read paths from config — no hardcoded paths.

Lazy sync: on each tool call, checks if source files changed since last
ingest. If so, re-ingests before serving the query. Zero background threads,
zero watchers — just mtime checks (~0.1ms overhead).
"""

import json
import sys
import time
from pathlib import Path
from typing import Optional

import duckdb
import lancedb

from brain_mcp.config import get_config, BrainConfig


# ═══════════════════════════════════════════════════════════════════════════════
# CACHED CONNECTIONS (module-level singletons)
# ═══════════════════════════════════════════════════════════════════════════════

_embedding_model = None
_conversations_db: Optional[duckdb.DuckDBPyConnection] = None
_lance_db = None
_summaries_lance = None
_summaries_db: Optional[duckdb.DuckDBPyConnection] = None
_github_db: Optional[duckdb.DuckDBPyConnection] = None
_markdown_db: Optional[duckdb.DuckDBPyConnection] = None
_principles_data = None

# Lazy sync state
_last_sync_check: float = 0.0          # time.monotonic() of last mtime scan
_SYNC_CHECK_INTERVAL: float = 60.0     # seconds between mtime scans (1 min)
_sync_lock = False                      # prevent re-entrant sync


# ═══════════════════════════════════════════════════════════════════════════════
# EMBEDDING MODEL
# ═══════════════════════════════════════════════════════════════════════════════

def get_embedding_model():
    """Get cached embedding provider (lazy-loaded on first call).
    Returns None if fastembed is not installed."""
    global _embedding_model
    if _embedding_model is None:
        try:
            from brain_mcp.embed.provider import get_provider
            _embedding_model = get_provider()
        except ImportError:
            return None
    return _embedding_model


def get_embedding(text: str) -> Optional[list[float]]:
    """Get embedding vector for text. Returns None if provider unavailable."""
    try:
        cfg = get_config()
        provider = get_embedding_model()
        if provider is None:
            return None
        return provider.embed_query(text[:cfg.embedding.max_chars])
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# LAZY SYNC — check source mtimes, re-ingest if stale
# ═══════════════════════════════════════════════════════════════════════════════

def _check_and_sync():
    """Check if source files changed since last ingest. If so, re-ingest.

    Called before every query via get_conversations(). Costs ~0.1ms when
    nothing changed (just stat() calls). Skips if checked within the last
    _SYNC_CHECK_INTERVAL seconds.
    """
    global _last_sync_check, _sync_lock, _conversations_db, _lance_db

    now = time.monotonic()
    if now - _last_sync_check < _SYNC_CHECK_INTERVAL:
        return  # checked recently
    if _sync_lock:
        return  # already syncing

    _last_sync_check = now

    cfg = get_config()
    if not cfg.parquet_path.exists():
        return  # no data yet — nothing to sync against

    parquet_mtime = cfg.parquet_path.stat().st_mtime

    # Scan source dirs for files newer than parquet
    new_count = 0
    for source in (cfg.sources or []):
        source_path = Path(source.path if hasattr(source, 'path') else source.get("path", ""))
        if not source_path.exists():
            continue
        for f in source_path.rglob("*.jsonl"):
            try:
                if f.stat().st_mtime > parquet_mtime:
                    new_count += 1
                    if new_count >= 2:
                        break  # don't need exact count, just > 0
            except OSError:
                continue
        if new_count >= 2:
            break

    if new_count == 0:
        return  # nothing new

    # New data found — re-ingest
    _sync_lock = True
    try:
        print(f"🔄 Lazy sync: {new_count}+ new file(s), re-ingesting...", file=sys.stderr, flush=True)
        from brain_mcp.ingest import run_all_ingesters
        run_all_ingesters(cfg)
        print(f"✅ Lazy sync complete.", file=sys.stderr, flush=True)

        # Invalidate cached connections so they re-read fresh data
        if _conversations_db is not None:
            try:
                _conversations_db.close()
            except Exception:
                pass
            _conversations_db = None
        _lance_db = None  # LanceDB will reconnect on next access
    except Exception as e:
        print(f"⚠️  Lazy sync failed: {e}", file=sys.stderr, flush=True)
    finally:
        _sync_lock = False


# ═══════════════════════════════════════════════════════════════════════════════
# CONVERSATIONS (DuckDB over parquet)
# ═══════════════════════════════════════════════════════════════════════════════

def get_conversations() -> duckdb.DuckDBPyConnection:
    """Get cached DuckDB connection with conversations view.

    Automatically checks for new source data and re-ingests if needed
    (lazy sync — no background threads, just mtime checks).
    """
    global _conversations_db

    # Lazy sync: check for new files before serving
    _check_and_sync()

    cfg = get_config()
    if _conversations_db is None:
        if not cfg.parquet_path.exists():
            raise FileNotFoundError(
                f"Conversations parquet not found at {cfg.parquet_path}. "
                "Run the ingest pipeline first."
            )
        _conversations_db = duckdb.connect()
        _conversations_db.execute(f"""
            CREATE VIEW IF NOT EXISTS conversations
            AS SELECT * FROM read_parquet('{cfg.parquet_path}')
        """)
    return _conversations_db


# ═══════════════════════════════════════════════════════════════════════════════
# LANCE DB (vector search)
# ═══════════════════════════════════════════════════════════════════════════════

def get_lance_db():
    """Get cached LanceDB connection for message vectors."""
    global _lance_db
    cfg = get_config()
    if _lance_db is None:
        if not cfg.lance_path.exists():
            return None
        _lance_db = lancedb.connect(str(cfg.lance_path))
    return _lance_db


def lance_search(
    embedding: list[float],
    table: str = "message",
    limit: int = 10,
    min_sim: float = 0.0,
) -> list[tuple]:
    """
    Search LanceDB with embedding vector.

    Returns list of (conversation_title, content, year, month, similarity) tuples.
    """
    db = get_lance_db()
    if not db:
        return []
    try:
        tbl = db.open_table(table)
        results = tbl.search(embedding).limit(limit).to_pandas()
        output = []
        for _, row in results.iterrows():
            sim = 1 / (1 + row.get("_distance", 0))
            if sim >= min_sim:
                output.append((
                    row.get("conversation_title", "Untitled"),
                    row.get("content", ""),
                    row.get("year", 0),
                    row.get("month", 0),
                    sim,
                ))
        return output
    except Exception:
        return []


def lance_count(table: str = "message") -> int:
    """Get row count from a LanceDB table."""
    db = get_lance_db()
    if not db:
        return 0
    try:
        return db.open_table(table).count_rows()
    except Exception:
        return 0


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARIES (v6 structured summaries)
# ═══════════════════════════════════════════════════════════════════════════════

SUMMARIES_TABLE = "summary"


def has_summaries() -> bool:
    """Check if structured summaries exist (parquet + lance)."""
    cfg = get_config()
    return cfg.summaries_parquet.exists() and cfg.summaries_lance.exists()


def get_summaries_lance():
    """LanceDB connection for v6 summary vectors.

    Returns None if summaries haven't been generated yet.
    This is expected — prosthetic tools gracefully degrade without summaries.
    """
    global _summaries_lance
    cfg = get_config()
    if _summaries_lance is None:
        if not cfg.summaries_lance.exists():
            return None
        try:
            _summaries_lance = lancedb.connect(str(cfg.summaries_lance))
        except Exception:
            return None
    return _summaries_lance


def get_summaries_db() -> Optional[duckdb.DuckDBPyConnection]:
    """DuckDB connection for v6 summary parquet queries.

    Returns None if summaries haven't been generated yet.
    Callers should check for None and return a helpful message.
    """
    global _summaries_db
    cfg = get_config()
    if _summaries_db is None:
        if not cfg.summaries_parquet.exists():
            return None
        try:
            _summaries_db = duckdb.connect()
            _summaries_db.execute(f"""
                CREATE VIEW IF NOT EXISTS summaries
                AS SELECT * FROM read_parquet('{cfg.summaries_parquet}')
            """)
        except Exception:
            return None
    return _summaries_db


# ═══════════════════════════════════════════════════════════════════════════════
# GITHUB (optional)
# ═══════════════════════════════════════════════════════════════════════════════

def get_github_db() -> Optional[duckdb.DuckDBPyConnection]:
    """Get cached DuckDB connection for GitHub data."""
    global _github_db
    cfg = get_config()
    if _github_db is None:
        _github_db = duckdb.connect()
        if cfg.github_repos_parquet.exists():
            _github_db.execute(f"""
                CREATE VIEW IF NOT EXISTS github_repos
                AS SELECT * FROM read_parquet('{cfg.github_repos_parquet}')
            """)
        if cfg.github_commits_parquet.exists():
            _github_db.execute(f"""
                CREATE VIEW IF NOT EXISTS github_commits
                AS SELECT * FROM read_parquet('{cfg.github_commits_parquet}')
            """)
    return _github_db


# ═══════════════════════════════════════════════════════════════════════════════
# MARKDOWN CORPUS (optional)
# ═══════════════════════════════════════════════════════════════════════════════

def get_markdown_db() -> Optional[duckdb.DuckDBPyConnection]:
    """Get cached DuckDB connection for markdown corpus."""
    global _markdown_db
    cfg = get_config()
    if _markdown_db is None and cfg.markdown_parquet.exists():
        _markdown_db = duckdb.connect()
        _markdown_db.execute(f"""
            CREATE VIEW IF NOT EXISTS markdown_docs
            AS SELECT * FROM read_parquet('{cfg.markdown_parquet}')
        """)
    return _markdown_db


# ═══════════════════════════════════════════════════════════════════════════════
# PRINCIPLES (for alignment_check)
# ═══════════════════════════════════════════════════════════════════════════════

def get_principles() -> dict:
    """Load principles from configured YAML/JSON file."""
    global _principles_data
    if _principles_data is None:
        cfg = get_config()
        if cfg.principles_path and cfg.principles_path.exists():
            suffix = cfg.principles_path.suffix.lower()
            with open(cfg.principles_path) as f:
                if suffix in (".yaml", ".yml"):
                    import yaml
                    _principles_data = yaml.safe_load(f) or {}
                elif suffix == ".json":
                    _principles_data = json.load(f)
                else:
                    _principles_data = {}
        else:
            _principles_data = {}
    return _principles_data


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def sanitize_sql_value(value: str) -> str:
    """Sanitize a string value for use in LanceDB WHERE filters.

    LanceDB .where() uses SQL-like expressions but doesn't support
    parameterized queries. We use an allowlist approach: only keep
    alphanumeric characters, hyphens, underscores, spaces, and dots.
    """
    if not isinstance(value, str):
        return str(value)
    import re
    # Allowlist: keep only safe characters for filter values
    return re.sub(r"[^a-zA-Z0-9\s\-_.,]", "", value)[:200]


def parse_json_field(value) -> list:
    """Safely parse a JSON string field from parquet. Returns list or empty list."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
        return [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        return [str(value)] if value else []


# ═══════════════════════════════════════════════════════════════════════════════
# PRE-WARMING
# ═══════════════════════════════════════════════════════════════════════════════

def prewarm():
    """Pre-load embedding model and LanceDB connection for fast first query."""
    print("Pre-warming brain-mcp...", file=sys.stderr)

    provider = get_embedding_model()
    db = get_lance_db()
    if db:
        try:
            db.open_table("message")
        except Exception:
            pass

    # Dummy embed to fully initialize the provider
    if provider:
        try:
            provider.embed_query("warmup")
        except Exception:
            pass

    print("brain-mcp ready!", file=sys.stderr)


def prewarm_async():
    """Pre-warm in background thread so MCP starts immediately."""
    import threading
    t = threading.Thread(target=prewarm, daemon=True)
    t.start()
