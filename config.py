"""
Intellectual DNA - Central Configuration

Single source of truth for all paths and constants.
All pipeline scripts import from here.
"""

from pathlib import Path

# =============================================================================
# BASE PATHS
# =============================================================================

BASE = Path("/Users/mordechai/intellectual_dna")
DATA_DIR = BASE / "data"

# =============================================================================
# DATA FILES
# =============================================================================

PARQUET_PATH = DATA_DIR / "all_conversations.parquet"
EMBEDDINGS_DB = BASE / "mordelab/02-monotropic-prosthetic/embeddings.duckdb"
YOUTUBE_PARQUET = DATA_DIR / "youtube_rows.parquet"

# GitHub data
GITHUB_REPOS_PARQUET = DATA_DIR / "github_repos.parquet"
GITHUB_COMMITS_PARQUET = DATA_DIR / "github_commits.parquet"

# LanceDB vectors (2026 migration from DuckDB VSS)
LANCE_PATH = BASE / "vectors" / "brain.lance"

# Markdown corpus (harvested docs)
MARKDOWN_PARQUET = DATA_DIR / "facts" / "markdown_files_v20.parquet"

# Import staging
CLAUDE_CODE_IMPORT_PARQUET = DATA_DIR / "claude_code_local_import.parquet"
CLAUDE_CODE_METADATA_PARQUET = DATA_DIR / "claude_code_metadata.parquet"
BACKUP_DIR = DATA_DIR / "backups"

# =============================================================================
# SUMMARIES (v6 structured summaries for prosthetic tools)
# =============================================================================

SUMMARIES_V6_PARQUET = DATA_DIR / "brain_summaries_v6.parquet"
SUMMARIES_V6_LANCE = BASE / "vectors"  # LanceDB connection dir
SUMMARIES_V6_TABLE = "brain_summaries"  # Table name inside LanceDB

# =============================================================================
# EXTERNAL SOURCES
# =============================================================================

CLAUDE_PROJECTS = Path.home() / ".claude/projects"

# =============================================================================
# EMBEDDING CONFIG
# =============================================================================

EMBEDDING_MODEL = "nomic-ai/nomic-embed-text-v1.5"
EMBEDDING_DIM = 768
EMBEDDING_BATCH_SIZE = 50
EMBEDDING_MAX_CHARS = 8000

# =============================================================================
# SEED PRINCIPLES (Knowledge Graph)
# =============================================================================

SEED_PATH = BASE / "mordelab/02-monotropic-prosthetic/SEED-MORDETROPIC-128KB-MASTER.json"

# =============================================================================
# SQL CONSTANTS
# =============================================================================

# Use this in DuckDB queries for consistent NULL handling
EFFECTIVE_ID_SQL = "COALESCE(message_id, 'syn_' || conversation_id || '_' || msg_index)"
