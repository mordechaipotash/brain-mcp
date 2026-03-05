# brain-mcp ingest package
"""Conversation ingesters for various AI chat sources."""

import sys
from pathlib import Path

import pandas as pd


def run_all_ingesters(cfg) -> int:
    """
    Run all configured ingesters and write results to parquet.

    Args:
        cfg: BrainConfig instance with sources and data_dir configured.

    Returns:
        Total number of messages ingested.
    """
    from .claude_code import ingest as ingest_claude_code
    from .chatgpt import ingest as ingest_chatgpt
    from .clawdbot import ingest as ingest_clawdbot
    from .cursor import ingest as ingest_cursor
    from .chatgpt_export import ingest as ingest_chatgpt_export
    from .gemini_cli import ingest as ingest_gemini_cli
    from .generic import ingest_path

    INGESTERS = {
        "claude-code": ingest_claude_code,
        "chatgpt": ingest_chatgpt,
        "clawdbot": ingest_clawdbot,
        "cursor": ingest_cursor,
        "chatgpt-export": ingest_chatgpt_export,
        "gemini-cli": ingest_gemini_cli,
    }

    all_records = []

    for source in (cfg.sources or []):
        source_type = source.type if hasattr(source, 'type') else source.get("type", "")
        source_path = source.path if hasattr(source, 'path') else source.get("path", "")
        source_name = (source.name if hasattr(source, 'name') else source.get("name")) or source_type

        ingester = INGESTERS.get(source_type)
        if ingester:
            print(f"Ingesting {source_name} from {source_path}...")
            records = ingester(source_path)
            all_records.extend(records)
        elif source_type == "generic":
            print(f"Ingesting {source_name} (generic) from {source_path}...")
            records = ingest_path(Path(source_path).expanduser(), source_name)
            all_records.extend(records)
        else:
            print(f"Unknown source type: {source_type}", file=sys.stderr)

    if all_records:
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(all_records)
        parquet_path = cfg.parquet_path
        df.to_parquet(parquet_path, index=False)
        print(f"Wrote {len(df):,} messages to {parquet_path}")
    else:
        print("No messages ingested.")

    return len(all_records)
