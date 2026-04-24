#!/usr/bin/env python3
"""
brain-mcp — Conversation summarizer.

Reads conversations from parquet, generates structured summaries using an
LLM (Anthropic, OpenAI, or Ollama), and writes results to parquet + LanceDB.

Summaries power the prosthetic tools (tunnel_state, context_recovery, etc.)

All settings come from config.toml via config.py.

Usage:
    python -m summarize.summarize                # Summarize new conversations
    python -m summarize.summarize full           # Re-summarize everything
    python -m summarize.summarize embed          # Just embed existing summaries
    python -m summarize.summarize stats          # Show stats
"""

import sys
import os
import gc
import json
import hashlib
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(line_buffering=True)
os.environ["PYTHONUNBUFFERED"] = "1"

import pandas as pd

from brain_mcp.config import get_config


# ═══════════════════════════════════════════════════════════════════════════════
# DOMAIN NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def normalize_domain(d):
    """Normalize domain string: lowercase, underscores→hyphens."""
    if not d:
        return ""
    if isinstance(d, list):
        d = d[0] if d else ""
    if not isinstance(d, str) or not d:
        return ""
    return d.strip().lower().replace("_", "-")


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARIZATION PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

SUMMARY_PROMPT = None  # Loaded from file on first use

def _get_summary_prompt() -> str:
    """Load the enhanced extraction prompt.

    Resolution order:
      1. Package resource at brain_mcp/_prompts/enhanced-extraction-v5.txt
         (shipped with the wheel — the authoritative location)
      2. BRAIN_HOME/prompts/enhanced-extraction-v5.txt (user override)
      3. Legacy sibling-repo path (deprecated, kept for backwards-compat with cogro)
    """
    global SUMMARY_PROMPT
    if SUMMARY_PROMPT is not None:
        return SUMMARY_PROMPT

    # 1. Primary: package-shipped prompt (importlib.resources)
    try:
        from importlib.resources import files
        pkg_prompt = files("brain_mcp").joinpath("_prompts", "enhanced-extraction-v5.txt")
        if pkg_prompt.is_file():
            SUMMARY_PROMPT = pkg_prompt.read_text()
            return SUMMARY_PROMPT
    except (ImportError, ModuleNotFoundError, FileNotFoundError):
        pass

    # 2. User override: BRAIN_HOME/prompts/
    brain_home = os.environ.get("BRAIN_HOME", "")
    if brain_home:
        bh_path = Path(brain_home) / "prompts" / "enhanced-extraction-v5.txt"
        if bh_path.exists():
            SUMMARY_PROMPT = bh_path.read_text()
            return SUMMARY_PROMPT

    # 3. Legacy cogro sibling path (deprecated)
    legacy = Path(__file__).parent.parent.parent.parent / "clawd" / "cogro" / "prompts" / "enhanced-extraction-v5.txt"
    if legacy.exists():
        SUMMARY_PROMPT = legacy.read_text()
        return SUMMARY_PROMPT

    raise FileNotFoundError(
        "Enhanced extraction prompt not found. Expected at brain_mcp/_prompts/enhanced-extraction-v5.txt "
        "(shipped with package) or $BRAIN_HOME/prompts/enhanced-extraction-v5.txt."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LLM PROVIDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _call_anthropic(prompt: str, model: str, api_key: str) -> str:
    """Call Anthropic API."""
    import anthropic
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _call_openai(prompt: str, model: str, api_key: str) -> str:
    """Call OpenAI API."""
    import openai
    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _call_gemini(prompt: str, model: str, api_key: str) -> str:
    """Call Google Gemini via OpenAI-compatible endpoint."""
    import openai
    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    response = client.chat.completions.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


_openrouter_client = None
_openrouter_key = None

def _call_openrouter(prompt: str, model: str, api_key: str) -> str:
    """Call any model via OpenRouter (OpenAI-compatible). Reuses client."""
    import openai
    global _openrouter_client, _openrouter_key
    if _openrouter_client is None or _openrouter_key != api_key:
        _openrouter_client = openai.OpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
        _openrouter_key = api_key
    response = _openrouter_client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


def _call_ollama(prompt: str, model: str, **_) -> str:
    """Call local Ollama instance."""
    import requests
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["response"]


PROVIDERS = {
    "anthropic": _call_anthropic,
    "openai": _call_openai,
    "gemini": _call_gemini,
    "openrouter": _call_openrouter,
    "local": _call_ollama,
    "ollama": _call_ollama,
}


def call_llm(prompt: str, max_retries: int = 3, backoff: float = 5.0) -> str:
    """Call the configured LLM provider with retry on transient errors."""
    import time as _time

    cfg = get_config()
    provider = cfg.summarizer.provider
    model = cfg.summarizer.model
    api_key = os.environ.get(cfg.summarizer.api_key_env, "")

    call_fn = PROVIDERS.get(provider)
    if not call_fn:
        raise ValueError(f"Unknown provider: {provider}. Use: {list(PROVIDERS.keys())}")

    last_err = None
    for attempt in range(max_retries):
        try:
            return call_fn(prompt, model, api_key)
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            # Retry on transient errors (connection, timeout, 5xx, rate limit)
            if any(kw in err_str for kw in ("connection", "timeout", "502", "503", "529", "rate")):
                wait = backoff * (2 ** attempt)
                print(f"  Retry {attempt+1}/{max_retries} after {wait:.0f}s ({e})", flush=True)
                _time.sleep(wait)
                continue
            raise  # non-transient error, don't retry
    raise last_err  # exhausted retries


# ═══════════════════════════════════════════════════════════════════════════════
# SUMMARIZATION PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def get_conversations_to_summarize(full: bool = False) -> list[dict]:
    """Load conversations grouped by conversation_id."""
    cfg = get_config()

    if not cfg.parquet_path.exists():
        print("Error: parquet not found. Run ingest first.", flush=True)
        sys.exit(1)

    df = pd.read_parquet(cfg.parquet_path)

    # Load existing summaries to skip
    existing_ids = set()
    if not full and cfg.summaries_jsonl.exists():
        with open(cfg.summaries_jsonl) as f:
            for line in f:
                try:
                    obj = json.loads(line.strip())
                    existing_ids.add(obj.get("conversation_id", ""))
                except json.JSONDecodeError:
                    continue
        print(f"Already summarized: {len(existing_ids)}", flush=True)

    # Group by conversation
    conversations = []
    for conv_id, group in df.groupby("conversation_id"):
        if conv_id in existing_ids:
            continue

        group = group.sort_values("msg_index")
        messages = []
        for _, row in group.iterrows():
            role = row.get("role", "user")
            content = str(row.get("content", ""))[:3000]  # Truncate long messages
            messages.append(f"{role}: {content}")

        # Skip very short conversations
        full_text = "\n\n".join(messages)
        if len(full_text) < 100:
            continue

        conversations.append({
            "conversation_id": str(conv_id),
            "source": group.iloc[0].get("source", "unknown"),
            "title": group.iloc[0].get("conversation_title", "Untitled"),
            "msg_count": len(group),
            "text": full_text[:15000],  # Cap total length for API
        })

    return conversations


def summarize_conversation(conv: dict) -> dict | None:
    """Summarize a single conversation, return structured record or None."""
    prompt = _get_summary_prompt().replace("{conversation}", conv["text"])

    try:
        response = call_llm(prompt)

        # Parse JSON from response (handle markdown code blocks)
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1]  # Remove first line
            text = text.rsplit("```", 1)[0]  # Remove last ```

        data = json.loads(text)

    except json.JSONDecodeError as e:
        print(f"  JSON parse error for {conv['conversation_id']}: {e}", flush=True)
        return None
    except Exception as e:
        print(f"  LLM error for {conv['conversation_id']}: {e}", flush=True)
        return None

    # Build record
    record = {
        "conversation_id": conv["conversation_id"],
        "source": conv["source"],
        "title": conv["title"],
        "msg_count": conv["msg_count"],
        "data": data,
    }

    return record


def run_summarize(full: bool = False):
    """Run the summarization pipeline."""
    cfg = get_config()

    if not cfg.summarizer.enabled:
        print("Summarizer is disabled in config. Set summarizer.enabled: true", flush=True)
        sys.exit(1)

    conversations = get_conversations_to_summarize(full=full)
    print(f"Conversations to summarize: {len(conversations)}", flush=True)

    if not conversations:
        print("Nothing to summarize!", flush=True)
        return

    # Ensure data dir exists
    cfg.data_dir.mkdir(parents=True, exist_ok=True)

    # Open JSONL for append (or overwrite if full)
    mode = "w" if full else "a"
    jsonl_path = cfg.summaries_jsonl
    records = []

    with open(jsonl_path, mode) as f:
        for i, conv in enumerate(conversations, 1):
            title = conv.get('title') or conv.get('conversation_title') or ''
            if not isinstance(title, str):
                title = str(title) if title == title else ''  # handle NaN
            print(f"[{i}/{len(conversations)}] {title[:60]}...", flush=True)
            record = summarize_conversation(conv)
            if record:
                f.write(json.dumps(record) + "\n")
                f.flush()
                records.append(record)
            else:
                print(f"  Skipped (error)", flush=True)

    print(f"\n✅ Summarized {len(records)} conversations → {jsonl_path}", flush=True)

    # Clean up LLM clients before embed step (avoid "too many open files")
    global _openrouter_client
    if _openrouter_client is not None:
        try:
            _openrouter_client.close()
        except Exception:
            pass
        _openrouter_client = None
    gc.collect()

    # Convert to parquet + embed
    if records or full:
        jsonl_to_parquet()
        embed_summaries()


def jsonl_to_parquet():
    """Convert summaries JSONL to parquet."""
    cfg = get_config()
    jsonl_path = cfg.summaries_jsonl

    if not jsonl_path.exists():
        print("No JSONL file found.", flush=True)
        return

    records = []
    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            data = obj.get("data", {})

            # Handle both v5 (enhanced) and legacy formats
            summary_obj = data.get("summary", {})
            if isinstance(summary_obj, dict):
                # v5 enhanced format: summary is an object
                summary_text = summary_obj.get("text", "")
                domain_primary = normalize_domain(summary_obj.get("domain_primary", ""))
                domain_secondary = normalize_domain(summary_obj.get("domain_secondary", ""))
                thinking_stage = summary_obj.get("thinking_stage", "")
                importance = summary_obj.get("importance", "routine")
                emotional_tone = summary_obj.get("emotional_tone", "")
                cognitive_pattern = summary_obj.get("cognitive_pattern", "")
                resurface_when = summary_obj.get("resurface_when", "")
                quotable = summary_obj.get("quotable", "")
                # Concepts are objects in v5
                concepts_list = data.get("concepts", [])
                concepts_json = json.dumps(concepts_list)
                concept_names = [c["name"] for c in concepts_list if isinstance(c, dict)] if concepts_list else []
            else:
                # Legacy format: summary is a string
                summary_text = str(summary_obj) if summary_obj else ""
                domain_primary = normalize_domain(data.get("domain_primary", ""))
                domain_secondary = normalize_domain(data.get("domain_secondary", ""))
                thinking_stage = data.get("thinking_stage", "")
                importance = data.get("importance", "routine")
                emotional_tone = data.get("emotional_tone", "")
                cognitive_pattern = data.get("cognitive_pattern", "")
                resurface_when = ""
                quotable = json.dumps(data.get("quotable", []))
                concepts_list = data.get("concepts", [])
                concepts_json = json.dumps(concepts_list)
                concept_names = concepts_list if isinstance(concepts_list, list) else []

            rec = {
                "conversation_id": obj.get("conversation_id", ""),
                "source": obj.get("source", ""),
                "title": obj.get("title", ""),
                "msg_count": obj.get("msg_count", 0),
                "summary": summary_text,
                "concepts": concepts_json,
                "decisions": json.dumps(data.get("decisions", [])),
                "open_questions": json.dumps(data.get("open_questions", [])),
                "quotable": quotable if isinstance(quotable, str) else json.dumps(quotable),
                "importance": importance,
                "domain_primary": domain_primary,
                "domain_secondary": domain_secondary,
                "thinking_stage": thinking_stage,
                "emotional_tone": emotional_tone,
                "cognitive_pattern": cognitive_pattern,
                "resurface_when": resurface_when,
                "command_language": json.dumps(data.get("command_language", {})),
                # Fan-out data (stored as JSON for the fan-out script)
                "edges_json": json.dumps(data.get("edges", [])),
                "corrections_json": json.dumps(data.get("corrections", [])),
                "temporal_facts_json": json.dumps(data.get("temporal_facts", [])),
                "assets_json": json.dumps(data.get("assets", [])),
                "summarized_at": datetime.now().isoformat(),
                "summary_hash": hashlib.md5(summary_text.encode()).hexdigest()[:16],
            }

            # Build embedding text
            concepts_str = ", ".join(concept_names)
            raw_decisions = data.get("decisions", [])
            if isinstance(raw_decisions, list):
                decisions = "; ".join(
                    d.get("text", str(d)) if isinstance(d, dict) else str(d)
                    for d in raw_decisions
                )
            else:
                decisions = str(raw_decisions)
            raw_oq = data.get("open_questions", [])
            if isinstance(raw_oq, list):
                oq = "; ".join(
                    q.get("text", str(q)) if isinstance(q, dict) else str(q)
                    for q in raw_oq
                )
            else:
                oq = str(raw_oq)

            parts = [summary_text]
            if concepts_str:
                parts.append(f"Concepts: {concepts_str}")
            if decisions:
                parts.append(f"Decisions: {decisions}")
            if oq:
                parts.append(f"Open questions: {oq}")
            if resurface_when:
                parts.append(f"Resurface: {resurface_when}")

            rec["embedding_text"] = ". ".join(parts)
            records.append(rec)

    df = pd.DataFrame(records)
    parquet_path = cfg.summaries_parquet
    df.to_parquet(parquet_path, index=False)
    print(f"✅ Parquet: {len(df)} summaries → {parquet_path}", flush=True)


def embed_summaries():
    """Embed summary texts into LanceDB for vector search."""
    cfg = get_config()

    if not cfg.summaries_parquet.exists():
        print("No summaries parquet found. Run summarize first.", flush=True)
        return

    import lancedb
    import numpy as np

    df = pd.read_parquet(cfg.summaries_parquet)
    print(f"Embedding {len(df)} summaries...", flush=True)

    from brain_mcp.embed.provider import get_provider
    provider = get_provider()
    print(f"  Using {provider.provider_name}", flush=True)

    texts = df["embedding_text"].tolist()
    valid_mask = [bool(t and len(str(t).strip()) > 10) for t in texts]
    valid_texts = [str(t)[:2000] for t, v in zip(texts, valid_mask) if v]
    valid_indices = [i for i, v in enumerate(valid_mask) if v]

    print(f"  {len(valid_texts)} / {len(texts)} have enough text", flush=True)

    # Batch embed using provider abstraction
    batch_size = cfg.embedding.batch_size
    all_embeddings = []
    for i in range(0, len(valid_texts), batch_size):
        batch = valid_texts[i:i + batch_size]
        batch_embs = provider.embed_batch(batch)
        all_embeddings.extend(batch_embs)
        done = min(i + batch_size, len(valid_texts))
        if done % 500 == 0 or done == len(valid_texts):
            print(f"  {done}/{len(valid_texts)} embedded", flush=True)
        if done % 200 == 0:
            gc.collect()

    # Build lance records
    lance_records = []
    for idx, emb_idx in enumerate(valid_indices):
        if idx >= len(all_embeddings):
            break
        row = df.iloc[emb_idx]
        lance_records.append({
            "conversation_id": row["conversation_id"],
            "source": row["source"],
            "title": row["title"],
            "summary": row["summary"],
            "importance": row["importance"],
            "domain_primary": row["domain_primary"],
            "thinking_stage": row["thinking_stage"],
            "concepts": row["concepts"],
            "open_questions": row["open_questions"],
            "decisions": row["decisions"],
            "quotable": row.get("quotable", "[]"),
            "embedding_text": row["embedding_text"],
            "vector": all_embeddings[idx],
        })

    # Write to LanceDB
    lance_path = cfg.summaries_lance
    lance_path.parent.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(lance_path))

    table_name = "summary"
    if table_name in db.table_names():
        db.drop_table(table_name)

    db.create_table(table_name, lance_records)
    print(f"✅ LanceDB: {len(lance_records)} summary vectors → {lance_path}", flush=True)

    del all_embeddings, lance_records
    gc.collect()


def show_stats():
    """Show summary stats."""
    cfg = get_config()

    if cfg.summaries_parquet.exists():
        df = pd.read_parquet(cfg.summaries_parquet)
        print(f"📊 Summaries: {len(df)} conversations")
        print(f"  Sources: {df['source'].value_counts().to_dict()}")
        print(f"  Importance: {df['importance'].value_counts().to_dict()}")
        if "domain_primary" in df.columns:
            top = df["domain_primary"].value_counts().head(10)
            print(f"  Top domains: {top.to_dict()}")
        if "thinking_stage" in df.columns:
            print(f"  Thinking stages: {df['thinking_stage'].value_counts().to_dict()}")
    else:
        print("No summaries parquet found.")

    lance_path = cfg.summaries_lance
    if lance_path.exists():
        import lancedb
        db = lancedb.connect(str(lance_path))
        if "summary" in db.table_names():
            tbl = db.open_table("summary")
            print(f"\n🔮 LanceDB: {tbl.count_rows()} summary vectors")
    else:
        print("No summary vectors found.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "incremental"

    if mode == "stats":
        show_stats()
    elif mode == "full":
        run_summarize(full=True)
    elif mode == "embed":
        jsonl_to_parquet()
        embed_summaries()
    elif mode == "parquet":
        jsonl_to_parquet()
    else:
        run_summarize(full=False)
