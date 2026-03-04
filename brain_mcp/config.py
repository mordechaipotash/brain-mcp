"""
brain-mcp — Configuration loader.

Reads brain.yaml and provides all paths/settings to the rest of the system.
All paths are resolved relative to the config file location or absolute.
"""

import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

import yaml


@dataclass
class SourceConfig:
    """A conversation source definition."""
    type: str  # "claude-code", "clawdbot", "chatgpt", "generic"
    path: str
    format: str = "jsonl"  # "jsonl" or "json"
    name: Optional[str] = None  # display name

    @property
    def resolved_path(self) -> Path:
        return Path(self.path).expanduser().resolve()


@dataclass
class EmbeddingConfig:
    model: str = "nomic-ai/nomic-embed-text-v1.5"
    dim: int = 768
    batch_size: int = 50
    max_chars: int = 8000


@dataclass
class SummarizerConfig:
    enabled: bool = False
    provider: str = "anthropic"  # "anthropic", "openai", "local"
    model: str = "claude-sonnet-4-20250514"
    api_key_env: str = "ANTHROPIC_API_KEY"  # env var name for API key
    max_concurrent: int = 3


@dataclass
class BrainConfig:
    """Top-level configuration for brain-mcp."""
    # Directories
    data_dir: Path = Path("./data")
    vectors_dir: Path = Path("./vectors")

    # Sources
    sources: list[SourceConfig] = field(default_factory=list)

    # Embedding
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)

    # Summarizer
    summarizer: SummarizerConfig = field(default_factory=SummarizerConfig)

    # Principles (for alignment_check)
    principles_path: Optional[Path] = None

    # Domains (configurable list for prosthetic tools)
    domains: list[str] = field(default_factory=lambda: [
        "ai-dev", "backend-dev", "frontend-dev", "data-engineering",
        "devops", "database", "python", "web-scraping", "mobile-dev",
        "automation", "prompt-engineering", "documentation",
        "business-strategy", "career", "finance",
        "personal", "health", "education",
    ])

    # Server
    server_name: str = "my-brain"
    server_instructions: str = (
        "You are interfacing with a searchable brain — an indexed archive of AI conversations. "
        "Use these tools to search, synthesize, and navigate intellectual history."
    )

    # Derived paths (computed from data_dir / vectors_dir)
    @property
    def parquet_path(self) -> Path:
        return self.data_dir / "all_conversations.parquet"

    @property
    def summaries_parquet(self) -> Path:
        return self.data_dir / "brain_summaries_v6.parquet"

    @property
    def summaries_jsonl(self) -> Path:
        return self.data_dir / "brain_summaries_v6.jsonl"

    @property
    def lance_path(self) -> Path:
        return self.vectors_dir / "brain.lance"

    @property
    def summaries_lance(self) -> Path:
        return self.vectors_dir / "brain_summaries.lance"

    @property
    def github_repos_parquet(self) -> Path:
        return self.data_dir / "github_repos.parquet"

    @property
    def github_commits_parquet(self) -> Path:
        return self.data_dir / "github_commits.parquet"

    @property
    def markdown_parquet(self) -> Path:
        return self.data_dir / "markdown_files.parquet"

    @property
    def sync_state_path(self) -> Path:
        return self.data_dir / "sync_state.json"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"


def validate_config(cfg: 'BrainConfig') -> list[str]:
    """
    Validate configuration and return list of warnings.

    Checks that paths exist and warns about missing optional data.
    Does NOT raise exceptions — just logs warnings.
    """
    warnings = []

    if not cfg.data_dir.exists():
        warnings.append(
            f"Data directory does not exist: {cfg.data_dir}. "
            "It will be created on first ingest."
        )

    if not cfg.parquet_path.exists():
        warnings.append(
            "Conversation parquet not found. "
            "Run the ingest pipeline to import conversations."
        )

    if not cfg.lance_path.exists():
        warnings.append(
            "Vector database not found. "
            "Run the embed pipeline after ingesting conversations."
        )

    if not cfg.summaries_parquet.exists():
        warnings.append(
            "Summaries not found. "
            "Prosthetic tools (tunnel_state, etc.) require the "
            "summarize pipeline."
        )

    if cfg.principles_path and not cfg.principles_path.exists():
        warnings.append(
            f"Principles file not found: {cfg.principles_path}"
        )

    for src in cfg.sources:
        if not src.resolved_path.exists():
            warnings.append(
                f"Source path not found: {src.resolved_path} "
                f"(type={src.type})"
            )

    return warnings


def load_config(config_path: Optional[str] = None) -> BrainConfig:
    """
    Load configuration from brain.yaml.

    Search order:
    1. Explicit path (if provided)
    2. BRAIN_CONFIG env var
    3. BRAIN_HOME env var (looks for brain.yaml inside)
    4. ./brain.yaml
    5. ~/.config/brain-mcp/brain.yaml
    """
    if config_path:
        path = Path(config_path)
    elif os.environ.get("BRAIN_CONFIG"):
        path = Path(os.environ["BRAIN_CONFIG"])
    elif os.environ.get("BRAIN_HOME"):
        brain_home = Path(os.environ["BRAIN_HOME"])
        path = brain_home / "brain.yaml"
        if not path.exists():
            print(
                f"BRAIN_HOME set to {brain_home} but no brain.yaml found there",
                file=sys.stderr,
            )
            return BrainConfig(data_dir=brain_home / "data",
                               vectors_dir=brain_home / "vectors")
    elif Path("brain.yaml").exists():
        path = Path("brain.yaml")
    elif (Path.home() / ".config" / "brain-mcp" / "brain.yaml").exists():
        path = Path.home() / ".config" / "brain-mcp" / "brain.yaml"
    else:
        # Return defaults
        print("No brain.yaml found, using defaults", file=sys.stderr)
        return BrainConfig()

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    config_dir = path.parent.resolve()

    def resolve(p: str) -> Path:
        """Resolve path relative to config file location."""
        expanded = Path(p).expanduser()
        if expanded.is_absolute():
            return expanded
        return (config_dir / expanded).resolve()

    # Build config
    cfg = BrainConfig()

    if "data_dir" in raw:
        cfg.data_dir = resolve(raw["data_dir"])
    if "vectors_dir" in raw:
        cfg.vectors_dir = resolve(raw["vectors_dir"])

    # Sources
    for src_raw in raw.get("sources", []):
        cfg.sources.append(SourceConfig(
            type=src_raw["type"],
            path=src_raw["path"],
            format=src_raw.get("format", "jsonl"),
            name=src_raw.get("name"),
        ))

    # Embedding
    emb_raw = raw.get("embedding", {})
    cfg.embedding = EmbeddingConfig(
        model=emb_raw.get("model", cfg.embedding.model),
        dim=emb_raw.get("dim", cfg.embedding.dim),
        batch_size=emb_raw.get("batch_size", cfg.embedding.batch_size),
        max_chars=emb_raw.get("max_chars", cfg.embedding.max_chars),
    )

    # Summarizer
    sum_raw = raw.get("summarizer", {})
    cfg.summarizer = SummarizerConfig(
        enabled=sum_raw.get("enabled", False),
        provider=sum_raw.get("provider", "anthropic"),
        model=sum_raw.get("model", cfg.summarizer.model),
        api_key_env=sum_raw.get("api_key_env", "ANTHROPIC_API_KEY"),
        max_concurrent=sum_raw.get("max_concurrent", 3),
    )

    # Principles
    if "principles" in raw and "path" in raw["principles"]:
        cfg.principles_path = resolve(raw["principles"]["path"])

    # Domains
    if "domains" in raw:
        cfg.domains = raw["domains"]

    # Server
    if "server" in raw:
        cfg.server_name = raw["server"].get("name", cfg.server_name)
        cfg.server_instructions = raw["server"].get("instructions", cfg.server_instructions)

    return cfg


# Module-level singleton (lazy)
_config: Optional[BrainConfig] = None


def get_config() -> BrainConfig:
    """Get the global config singleton."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: BrainConfig):
    """Override the global config (for testing)."""
    global _config
    _config = config
