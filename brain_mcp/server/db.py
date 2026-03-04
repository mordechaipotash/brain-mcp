"""
brain-mcp — Database connection management.

Lazy-loaded, cached connections to DuckDB (parquet queries),
LanceDB (vector search), and the embedding model.

All connections read paths from config — no hardcoded paths.
"""

import json
import sys
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


# ═══════════════════════════════════════════════════════════════════════════════
# EMBEDDING MODEL
# ═══════════════════════════════════════════════════════════════════════════════

def get_embedding_model():
    """Get cached embedding model (lazy-loaded on first call).
    Returns None if sentence-transformers is not installed."""
    global _embedding_model
    if _embedding_model is None:
        try:
            cfg = get_config()
            from sentence_transformers import SentenceTransformer
            _embedding_model = SentenceTransformer(cfg.embedding.model, trust_remote_code=True)
        except ImportError:
            return None
    return _embedding_model


def get_embedding(text: str) -> Optional[list[float]]:
    """Get embedding vector for text. Returns None if model unavailable."""
    try:
        cfg = get_config()
        model = get_embedding_model()
        if model is None:
            return None
        embedding = model.encode(text[:cfg.embedding.max_chars], convert_to_numpy=True)
        return embedding.tolist()
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# CONVERSATIONS (DuckDB over parquet)
# ═══════════════════════════════════════════════════════════════════════════════

def get_conversations() -> duckdb.DuckDBPyConnection:
    """Get cached DuckDB connection with conversations view."""
    global _conversations_db
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
    """Sanitize a string value for use in SQL WHERE clauses.

    This is used for LanceDB `.where()` filters which don't support
    parameterized queries. Strips single quotes and other dangerous characters.
    """
    if not isinstance(value, str):
        return str(value)
    # Remove single quotes, semicolons, and SQL comment markers
    return value.replace("'", "").replace(";", "").replace("--", "").replace("/*", "").replace("*/", "")


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

    model = get_embedding_model()
    db = get_lance_db()
    if db:
        try:
            db.open_table("message")
        except Exception:
            pass

    # Dummy embed to fully initialize
    if model:
        model.encode("warmup", convert_to_numpy=True)

    print("brain-mcp ready!", file=sys.stderr)


def prewarm_async():
    """Pre-warm in background thread so MCP starts immediately."""
    import threading
    t = threading.Thread(target=prewarm, daemon=True)
    t.start()
