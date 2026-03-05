#!/usr/bin/env python3
"""
brain-mcp — Conversation summarizer.

Reads conversations from parquet, generates structured summaries using an
LLM (Anthropic, OpenAI, or Ollama), and writes results to parquet + LanceDB.

Summaries power the prosthetic tools (tunnel_state, context_recovery, etc.)

All settings come from brain.yaml via config.py.

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

SUMMARY_PROMPT = """Analyze this conversation and extract structured metadata.

<conversation>
{conversation}
</conversation>

Return a JSON object with exactly these fields:
{{
  "summary": "2-3 sentence summary of what was discussed and accomplished",
  "key_insights": ["insight 1", "insight 2"],
  "concepts": ["concept1", "concept2"],
  "decisions": ["decision made 1", "decision made 2"],
  "open_questions": ["unresolved question 1"],
  "quotable": ["memorable or insightful quote from the conversation"],
  "domain_primary": "primary domain (e.g. ai-dev, frontend-dev, business-strategy, personal)",
  "domain_secondary": "secondary domain or empty string",
  "thinking_stage": "one of: exploring, crystallizing, refining, executing",
  "importance": "one of: breakthrough, significant, routine",
  "emotional_tone": "overall emotional tone (e.g. excited, frustrated, analytical, reflective)",
  "cognitive_pattern": "thinking pattern observed (e.g. deep-dive, rapid-iteration, brainstorming)",
  "problem_solving_approach": "approach used (e.g. systematic, creative, analytical)"
}}

Be precise. Use empty arrays [] for fields with no relevant data.
Return ONLY the JSON object, no other text."""


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


def _call_openrouter(prompt: str, model: str, api_key: str) -> str:
    """Call any model via OpenRouter (OpenAI-compatible)."""
    import openai
    client = openai.OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )
    response = client.chat.completions.create(
        model=model,
        max_tokens=2000,
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


def call_llm(prompt: str) -> str:
    """Call the configured LLM provider."""
    cfg = get_config()
    provider = cfg.summarizer.provider
    model = cfg.summarizer.model
    api_key = os.environ.get(cfg.summarizer.api_key_env, "")

    call_fn = PROVIDERS.get(provider)
    if not call_fn:
        raise ValueError(f"Unknown provider: {provider}. Use: {list(PROVIDERS.keys())}")

    return call_fn(prompt, model, api_key)


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
    prompt = SUMMARY_PROMPT.format(conversation=conv["text"])

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
            print(f"[{i}/{len(conversations)}] {conv['title'][:60]}...", flush=True)
            record = summarize_conversation(conv)
            if record:
                f.write(json.dumps(record) + "\n")
                f.flush()
                records.append(record)
            else:
                print(f"  Skipped (error)", flush=True)

    print(f"\n✅ Summarized {len(records)} conversations → {jsonl_path}", flush=True)

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

            rec = {
                "conversation_id": obj.get("conversation_id", ""),
                "source": obj.get("source", ""),
                "title": obj.get("title", ""),
                "msg_count": obj.get("msg_count", 0),
                "summary": data.get("summary", ""),
                "key_insights": json.dumps(data.get("key_insights", [])),
                "concepts": json.dumps(data.get("concepts", [])),
                "decisions": json.dumps(data.get("decisions", [])),
                "open_questions": json.dumps(data.get("open_questions", [])),
                "quotable": json.dumps(data.get("quotable", [])),
                "importance": data.get("importance", "routine"),
                "domain_primary": normalize_domain(data.get("domain_primary", "")),
                "domain_secondary": normalize_domain(data.get("domain_secondary", "")),
                "thinking_stage": data.get("thinking_stage", ""),
                "emotional_tone": data.get("emotional_tone", ""),
                "cognitive_pattern": data.get("cognitive_pattern", ""),
                "problem_solving_approach": data.get("problem_solving_approach", ""),
                "summarized_at": datetime.now().isoformat(),
                "summary_hash": hashlib.md5(data.get("summary", "").encode()).hexdigest()[:16],
            }

            # Build embedding text
            insights = "; ".join(data.get("key_insights", []))
            concepts = ", ".join(data.get("concepts", []))
            decisions = "; ".join(data.get("decisions", []))
            oq = "; ".join(data.get("open_questions", []))

            parts = [rec["summary"]]
            if insights:
                parts.append(f"Key insights: {insights}")
            if concepts:
                parts.append(f"Concepts: {concepts}")
            if decisions:
                parts.append(f"Decisions: {decisions}")
            if oq and oq != "none identified":
                parts.append(f"Open questions: {oq}")

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
