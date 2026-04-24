"""
brain-mcp — Cognitive Prosthetic tools (L2 + L3 in the SHELET stratification).

These are the SOUL of the prosthetic — they turn a search engine into a
cognitive aid that preserves context across focus tunnels.

With summaries: full structured analysis (thinking stages, decisions, etc.).
Without summaries: useful fallbacks from raw conversations + vectors.

Citation contract (SHELET L2/L3 requirement):
  Every non-structural claim in L2/L3 output MUST carry a citation.
  Format: `[conv_id · YYYY-MM-DD]` or `[summary_id · YYYY-MM-DD]`.
  The `_cite()` helper below produces the canonical format.

  Rollout status (see docs/adr/001):
    ✓ context_recovery — per-summary citations on Recent Summaries section
    ◯ tunnel_state — TODO: widen GROUP_CONCAT queries to preserve per-question provenance
    ◯ open_threads / dormant_contexts — TODO: surface summary_id per question
    ◯ switching_cost — TODO: cite source summaries for concept-overlap rows
"""

from datetime import datetime

from .db import (
    get_summaries_db, parse_json_field,
    get_conversations, get_embedding, lance_search, lance_count,
    sanitize_sql_value,
)


_SUMMARIES_HINT = (
    "\n---\n_Running without summaries. For richer analysis with thinking "
    "stages, open questions, and decisions: `brain-mcp summarize`_"
)


def _cite(source_id: str | None, ts: object = None) -> str:
    """Format a SHELET citation marker: [source_id · YYYY-MM-DD].

    Use for inline citations on L2/L3 output bullets.

    Args:
        source_id: conv_id, summary_id, or message_id. Truncated to 12 chars
                   for readability (citations are drilled via `get_conversation`).
        ts: datetime, ISO string, or None. Rendered as YYYY-MM-DD.

    Returns:
        Citation string like `[abc123def456 · 2026-04-24]`, or `[source_id]`
        when ts is unavailable, or empty string when source_id is missing.
    """
    if not source_id:
        return ""
    sid = str(source_id)[:12]
    if ts is None:
        return f"[{sid}]"
    try:
        if isinstance(ts, datetime):
            date_str = ts.strftime("%Y-%m-%d")
        elif isinstance(ts, str):
            # Handle ISO-ish strings — take first 10 chars
            date_str = ts[:10]
        else:
            date_str = str(ts)[:10]
    except Exception:
        return f"[{sid}]"
    return f"[{sid} · {date_str}]"

# Map domain names to broader search terms for raw conversation fallback
_DOMAIN_KEYWORDS = {
    "ai-dev": "AI machine learning LLM model GPT Claude agent",
    "backend-dev": "backend server API database endpoint REST GraphQL",
    "frontend-dev": "frontend React Next.js CSS HTML UI component",
    "data-engineering": "data pipeline ETL parquet database SQL",
    "devops": "deploy Docker CI CD infrastructure server",
    "database": "database SQL Postgres Supabase query table",
    "python": "Python pip poetry virtualenv package module",
    "web-scraping": "scrape crawl fetch parse HTML extract",
    "automation": "automate script cron schedule pipeline",
    "prompt-engineering": "prompt system instructions context token",
    "documentation": "docs README documentation guide reference",
    "business-strategy": "business strategy revenue pricing market",
    "career": "career job resume interview hiring",
    "finance": "finance budget cost revenue expense",
    "personal": "personal life family health",
    "health": "health sleep exercise medication ADHD",
    "education": "learn study course tutorial",
    "torah": "Torah parsha halacha shiur",
    "cognitive-architecture": "cognitive ADHD monotropic hyperfocus executive function",
    "wotc": "WOTC tax credit employment",
    "mobile-dev": "mobile iOS Android app Swift",
    "ai-strategy": "AI strategy adoption enterprise",
    "ai-image": "image generation diffusion stable Dall-E",
}


def _expand_domain_search(domain: str) -> str:
    """Expand a domain name into broader search keywords."""
    # Try exact domain keywords first
    if domain in _DOMAIN_KEYWORDS:
        return _DOMAIN_KEYWORDS[domain]
    # Fall back to splitting the domain name
    return domain.replace("-", " ").replace("_", " ")


def _domain_fallback_search(con, domain: str, limit: int):
    """Search for domain-related conversations using expanded keywords."""
    # First try exact domain name
    pattern = f"%{domain}%"
    rows = con.execute("""
        SELECT conversation_title, content, created, source
        FROM conversations
        WHERE (content ILIKE ? OR conversation_title ILIKE ?)
          AND role = 'user'
        ORDER BY created DESC
        LIMIT ?
    """, [pattern, pattern, limit * 3]).fetchall()

    if rows:
        return rows

    # Expand to related keywords
    keywords = _expand_domain_search(domain)
    for kw in keywords.split():
        if len(kw) < 3:
            continue
        kw_pattern = f"%{kw}%"
        kw_rows = con.execute("""
            SELECT conversation_title, content, created, source
            FROM conversations
            WHERE (content ILIKE ? OR conversation_title ILIKE ?)
              AND role = 'user'
            ORDER BY created DESC
            LIMIT ?
        """, [kw_pattern, kw_pattern, limit]).fetchall()
        rows.extend(kw_rows)

    # Deduplicate by content
    seen = set()
    unique = []
    for r in rows:
        key = (r[1] or "")[:100]
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique[:limit * 3]


def register(mcp):
    """Register prosthetic tools with the MCP server."""

    @mcp.tool()
    def tunnel_state(domain: str, limit: int = 10) -> str:
        """
        Reconstruct cognitive save-state for a domain — where you left off.
        Returns: thinking stage, open questions, decisions, concepts, emotional tone.
        The 'load game' button for your mind.
        """
        db = get_summaries_db()
        if db:
            rows = db.execute("""
                SELECT summary, thinking_stage, importance, emotional_tone,
                       open_questions, decisions, concepts, key_insights, connections_to,
                       cognitive_pattern, problem_solving_approach, msg_count, title, source,
                       conversation_id, summarized_at
                FROM summaries
                WHERE domain_primary = ?
                ORDER BY summarized_at DESC
                LIMIT ?
            """, [domain, limit]).fetchall()

            if not rows:
                return f"No conversations found for domain: {domain}"

            cols = [
                "summary", "thinking_stage", "importance", "emotional_tone",
                "open_questions", "decisions", "concepts", "key_insights",
                "connections_to", "cognitive_pattern", "problem_solving_approach",
                "msg_count", "title", "source",
                "conversation_id", "summarized_at",
            ]

            latest = dict(zip(cols, rows[0]))

            all_oq, all_dec, all_concepts, all_insights, all_connections = [], [], set(), [], set()
            importance_counts = {"breakthrough": 0, "significant": 0, "routine": 0}
            stage_counts = {}

            for row in rows:
                r = dict(zip(cols, row))
                for q in parse_json_field(r["open_questions"]):
                    if q and q.lower() != "none identified" and q not in all_oq:
                        all_oq.append(q)
                for d in parse_json_field(r["decisions"]):
                    if d and d not in all_dec:
                        all_dec.append(d)
                for c in parse_json_field(r["concepts"]):
                    all_concepts.add(c)
                for i in parse_json_field(r["key_insights"]):
                    if i and i not in all_insights:
                        all_insights.append(i)
                for c in parse_json_field(r["connections_to"]):
                    all_connections.add(c)
                imp = r["importance"] or "routine"
                importance_counts[imp] = importance_counts.get(imp, 0) + 1
                stage = r["thinking_stage"] or ""
                stage_counts[stage] = stage_counts.get(stage, 0) + 1

            output = [f"## 🧠 Tunnel State: {domain}\n"]
            output.append(f"**Current stage:** {latest['thinking_stage'] or 'unknown'}")
            output.append(f"**Emotional tone:** {latest['emotional_tone'] or 'unknown'}")
            output.append(f"**Conversations:** {len(rows)} (last {limit})")
            bt = importance_counts.get("breakthrough", 0)
            if bt:
                output.append(f"**Breakthroughs:** {bt} 💎")
            output.append(f"**Cognitive pattern:** {latest['cognitive_pattern'] or 'unknown'}")
            output.append(f"**Problem solving:** {latest['problem_solving_approach'] or 'unknown'}")

            if all_oq:
                output.append(f"\n### ❓ Open Questions ({len(all_oq)})")
                for q in all_oq[:10]:
                    output.append(f"  - {q}")
                if len(all_oq) > 10:
                    output.append(f"  _... and {len(all_oq)-10} more_")

            if all_dec:
                output.append(f"\n### ✅ Decisions ({len(all_dec)})")
                for d in all_dec[:7]:
                    output.append(f"  - {d}")
                if len(all_dec) > 7:
                    output.append(f"  _... and {len(all_dec)-7} more_")

            if all_concepts:
                output.append(f"\n### 🏷️ Active Concepts ({len(all_concepts)})")
                output.append(f"  {', '.join(sorted(all_concepts)[:15])}")

            if all_insights:
                output.append(f"\n### 💡 Key Insights")
                for i in all_insights[:5]:
                    output.append(f"  - {i}")

            if all_connections:
                output.append(f"\n### 🔗 Connected Domains")
                output.append(f"  {', '.join(sorted(all_connections)[:10])}")

            if stage_counts:
                output.append(f"\n### 📊 Thinking Stage History")
                for s, c in sorted(stage_counts.items(), key=lambda x: -x[1]):
                    output.append(f"  {s or 'unknown'}: {c}")

            # SHELET L2 citation requirement: surface source trail
            output.append(f"\n### 📎 Sources ({len(rows)})")
            for row in rows:
                r = dict(zip(cols, row))
                title = (r.get("title") or "Untitled")[:50]
                output.append(f"  - {title} {_cite(r.get('conversation_id'), r.get('summarized_at'))}")

            return "\n".join(output)

        # ── Fallback: raw conversations + vectors ──
        try:
            con = get_conversations()
        except FileNotFoundError:
            return "No conversation data found. Run the ingest pipeline first."

        rows = _domain_fallback_search(con, domain, limit)

        if not rows:
            return f"No conversations found matching domain: {domain}. Try a broader search with `semantic_search` or `search_conversations`."

        questions = [r for r in rows if r[1] and "?" in r[1]]
        titles = list(dict.fromkeys(r[0] for r in rows if r[0]))

        output = [f"## 🧠 Tunnel State: {domain} (from raw conversations)\n"]
        output.append(f"**Matching messages:** {len(rows)}")
        output.append(f"**Questions asked:** {len(questions)}")
        output.append(f"**Distinct conversations:** {len(titles)}")

        if titles:
            output.append(f"\n### 📋 Recent Conversations")
            for t in titles[:10]:
                output.append(f"  - {t}")

        if questions:
            output.append(f"\n### ❓ Recent Questions ({len(questions)})")
            for _, content, created, source in questions[:10]:
                snippet = (content or "")[:200].replace("\n", " ")
                output.append(f"  - [{source}] {snippet}")

        output.append(f"\n### 💬 Sample Messages")
        for title, content, created, source in rows[:5]:
            snippet = (content or "")[:250].replace("\n", " ")
            output.append(f"  **{(title or 'Untitled')[:60]}** ({source})")
            output.append(f"  > {snippet}")
            output.append("")

        output.append(_SUMMARIES_HINT)
        return "\n".join(output)

    @mcp.tool()
    def dormant_contexts(min_importance: str = "significant", limit: int = 20) -> str:
        """
        Find abandoned tunnels — domains with open questions you haven't resolved.
        The 'what have I forgotten?' alarm.
        """
        db = get_summaries_db()
        if db:
            rows = db.execute("""
                SELECT domain_primary, COUNT(*) as conv_count,
                       GROUP_CONCAT(open_questions, '|||') as all_oq,
                       GROUP_CONCAT(importance, ',') as importances,
                       MAX(thinking_stage) as latest_stage
                FROM summaries
                WHERE domain_primary != '' AND domain_primary IS NOT NULL
                GROUP BY domain_primary
                ORDER BY conv_count DESC
            """).fetchall()

            if not rows:
                return "No domain data found."

            importance_rank = {"breakthrough": 3, "significant": 2, "routine": 1}
            min_rank = importance_rank.get(min_importance, 1)

            results = []
            for domain, count, all_oq_str, importances_str, stage in rows:
                imps = (importances_str or "").split(",")
                max_imp = max(importance_rank.get(i.strip(), 0) for i in imps if i.strip())
                if max_imp < min_rank:
                    continue
                questions = []
                for chunk in (all_oq_str or "").split("|||"):
                    for q in parse_json_field(chunk):
                        if q and q.lower() != "none identified" and q not in questions:
                            questions.append(q)
                if not questions:
                    continue
                bt_count = sum(1 for i in imps if i.strip() == "breakthrough")
                results.append((domain, count, questions, stage, bt_count))

            results.sort(key=lambda x: (-x[4], -len(x[2]), -x[1]))

            output = [f"## 🔴 Dormant Contexts (importance >= {min_importance})\n"]
            output.append(f"_Domains with unresolved open questions_\n")

            for domain, count, questions, stage, bt in results[:limit]:
                bt_marker = " 💎" if bt else ""
                output.append(f"### {domain}{bt_marker}")
                output.append(f"_{count} conversations | Stage: {stage or 'unknown'}_")
                output.append(f"**{len(questions)} open questions:**")
                for q in questions[:5]:
                    output.append(f"  ❓ {q}")
                if len(questions) > 5:
                    output.append(f"  _... and {len(questions)-5} more_")
                output.append("")

            output.append(f"_Total: {len(results)} domains with open questions_")
            return "\n".join(output)

        # ── Fallback: raw conversations ──
        try:
            con = get_conversations()
        except FileNotFoundError:
            return "No conversation data found. Run the ingest pipeline first."

        rows = con.execute("""
            SELECT conversation_title,
                   MAX(created) as last_active,
                   COUNT(*) as msgs,
                   SUM(CASE WHEN has_question = 1 AND role = 'user' THEN 1 ELSE 0 END) as questions
            FROM conversations
            GROUP BY conversation_title
            HAVING questions > 0
            ORDER BY last_active ASC
        """).fetchall()

        if not rows:
            return "No conversations with questions found."

        output = [f"## 🔴 Dormant Contexts (from raw conversations)\n"]
        output.append(f"_Topics with unanswered questions, oldest first_\n")

        shown = 0
        for title, last_active, msgs, q_count in rows:
            if shown >= limit:
                break
            if not title:
                continue
            date_str = str(last_active)[:10] if last_active else "unknown"
            output.append(f"### {title}")
            output.append(f"_{msgs} messages | {q_count} questions | Last active: {date_str}_")
            output.append("")
            shown += 1

        output.append(f"\n_Total: {len(rows)} topics with open questions_")
        output.append(_SUMMARIES_HINT)
        return "\n".join(output)

    @mcp.tool()
    def context_recovery(domain: str, summary_count: int = 5) -> str:
        """
        Full 'waking up' brief for re-entering a domain.
        Returns recent summaries + accumulated state — everything needed to resume work.
        The prosthetic's core value: making re-entry cheap.
        """
        db = get_summaries_db()
        if db:
            rows = db.execute("""
                SELECT title, source, summary, thinking_stage, importance,
                       emotional_tone, open_questions, decisions, key_insights,
                       concepts, connections_to, quotable, cognitive_pattern,
                       problem_solving_approach, msg_count
                FROM summaries
                WHERE domain_primary = ?
                ORDER BY summarized_at DESC
                LIMIT ?
            """, [domain, summary_count + 10]).fetchall()

            if not rows:
                return f"No conversations found for domain: {domain}"

            cols = [
                "title", "source", "summary", "thinking_stage", "importance",
                "emotional_tone", "open_questions", "decisions", "key_insights",
                "concepts", "connections_to", "quotable", "cognitive_pattern",
                "problem_solving_approach", "msg_count",
            ]

            all_oq, all_dec, all_insights, all_quotes = [], [], [], []
            for row in rows:
                r = dict(zip(cols, row))
                for q in parse_json_field(r["open_questions"]):
                    if q and q.lower() != "none identified" and q not in all_oq:
                        all_oq.append(q)
                for d in parse_json_field(r["decisions"]):
                    if d and d not in all_dec:
                        all_dec.append(d)
                for i in parse_json_field(r["key_insights"]):
                    if i and i not in all_insights:
                        all_insights.append(i)
                for q in parse_json_field(r["quotable"]):
                    if q and q not in all_quotes:
                        all_quotes.append(q)

            latest = dict(zip(cols, rows[0]))
            output = [f"## 🔄 Context Recovery: {domain}\n"]
            output.append(
                f"**Stage:** {latest['thinking_stage'] or '?'} | "
                f"**Tone:** {latest['emotional_tone'] or '?'} | "
                f"**Conversations:** {len(rows)}"
            )

            output.append(f"\n### 📋 Recent Summaries\n")
            for row in rows[:summary_count]:
                r = dict(zip(cols, row))
                title = (r["title"] or "Untitled")[:60]
                imp_icon = "💎" if r["importance"] == "breakthrough" else "⭐" if r["importance"] == "significant" else "·"
                citation = _cite(r.get("conversation_id"), r.get("summarized_at"))
                output.append(f"**{imp_icon} {title}** ({r['source']}, {r['msg_count']} msgs) {citation}")
                output.append(f"> {(r['summary'] or '')[:300]}{'...' if len(r['summary'] or '') > 300 else ''}")
                output.append("")

            if all_oq:
                output.append(f"### ❓ Accumulated Open Questions ({len(all_oq)})")
                for q in all_oq[:10]:
                    output.append(f"  - {q}")
                if len(all_oq) > 10:
                    output.append(f"  _... and {len(all_oq)-10} more_")

            if all_dec:
                output.append(f"\n### ✅ Key Decisions ({len(all_dec)})")
                for d in all_dec[:7]:
                    output.append(f"  - {d}")

            if all_insights:
                output.append(f"\n### 💡 Key Insights")
                for i in all_insights[:5]:
                    output.append(f"  - {i}")

            if all_quotes:
                output.append(f"\n### 💬 Quotable")
                output.append(f'  > "{all_quotes[0][:200]}"')

            return "\n".join(output)

        # ── Fallback: raw conversations + vectors ──
        try:
            con = get_conversations()
        except FileNotFoundError:
            return "No conversation data found. Run the ingest pipeline first."

        # Context recovery needs role column, so do a custom search with role included
        keywords = _expand_domain_search(domain)
        all_keywords = [domain] + [kw for kw in keywords.split() if len(kw) >= 3]
        rows = []
        for kw in all_keywords:
            kw_pattern = f"%{kw}%"
            kw_rows = con.execute("""
                SELECT conversation_title, content, created, source, role
                FROM conversations
                WHERE (content ILIKE ? OR conversation_title ILIKE ?)
                ORDER BY created DESC
                LIMIT ?
            """, [kw_pattern, kw_pattern, summary_count * 10]).fetchall()
            if kw_rows:
                rows = kw_rows
                break

        if not rows:
            return f"No conversations found matching domain: {domain}. Try a broader search with `semantic_search` or `search_conversations`."

        # Also pull semantic matches for richer context
        semantic_matches = []
        emb = get_embedding(domain)
        if emb:
            semantic_matches = lance_search(emb, limit=10, min_sim=0.3)

        titles = list(dict.fromkeys(r[0] for r in rows if r[0]))
        user_msgs = [(t, c, cr, s) for t, c, cr, s, role in rows if role == "user"]
        questions = [r for r in user_msgs if r[1] and "?" in r[1]]

        output = [f"## 🔄 Context Recovery: {domain} (from raw conversations)\n"]
        output.append(f"**Matching messages:** {len(rows)} | **Conversations:** {len(titles)}")

        output.append(f"\n### 📋 Recent Activity\n")
        for title, content, created, source, role in rows[:summary_count * 2]:
            if not content:
                continue
            snippet = (content or "")[:300].replace("\n", " ")
            role_icon = "👤" if role == "user" else "🤖"
            output.append(f"{role_icon} **{(title or 'Untitled')[:60]}** ({source})")
            output.append(f"> {snippet}{'...' if len(content or '') > 300 else ''}")
            output.append("")

        if semantic_matches:
            output.append(f"### 🔍 Semantically Related")
            for s_title, s_content, s_year, s_month, sim in semantic_matches[:5]:
                snippet = (s_content or "")[:200].replace("\n", " ")
                output.append(f"  **{(s_title or 'Untitled')[:60]}** ({s_year}-{s_month:02d}, {sim:.0%})")
                output.append(f"  > {snippet}")
                output.append("")

        if questions:
            output.append(f"### ❓ Questions You Asked ({len(questions)})")
            for _, content, created, source in questions[:8]:
                snippet = (content or "")[:200].replace("\n", " ")
                output.append(f"  - [{source}] {snippet}")

        output.append(_SUMMARIES_HINT)
        return "\n".join(output)

    @mcp.tool()
    def tunnel_history(domain: str) -> str:
        """
        Meta-view of your engagement with a domain over time.
        Shows total conversations, thinking stage distribution, importance peaks,
        and cognitive patterns.
        """
        db = get_summaries_db()
        if db:
            rows = db.execute("""
                SELECT thinking_stage, importance, emotional_tone,
                       cognitive_pattern, problem_solving_approach, concepts, source
                FROM summaries WHERE domain_primary = ?
            """, [domain]).fetchall()

            if not rows:
                return f"No conversations found for domain: {domain}"

            cols = [
                "thinking_stage", "importance", "emotional_tone",
                "cognitive_pattern", "problem_solving_approach", "concepts", "source",
            ]

            stage_counts, imp_counts, tone_counts = {}, {}, {}
            pattern_counts, approach_counts, source_counts = {}, {}, {}
            all_concepts = {}

            for row in rows:
                r = dict(zip(cols, row))
                s = r["thinking_stage"] or "unknown"
                stage_counts[s] = stage_counts.get(s, 0) + 1
                i = r["importance"] or "routine"
                imp_counts[i] = imp_counts.get(i, 0) + 1
                t = r["emotional_tone"] or ""
                if t:
                    tone_counts[t] = tone_counts.get(t, 0) + 1
                p = r["cognitive_pattern"] or ""
                if p:
                    pattern_counts[p] = pattern_counts.get(p, 0) + 1
                a = r["problem_solving_approach"] or ""
                if a:
                    approach_counts[a] = approach_counts.get(a, 0) + 1
                src = r["source"] or ""
                if src:
                    source_counts[src] = source_counts.get(src, 0) + 1
                for c in parse_json_field(r["concepts"]):
                    all_concepts[c] = all_concepts.get(c, 0) + 1

            output = [f"## 📊 Tunnel History: {domain}\n"]
            output.append(f"**Total conversations:** {len(rows)}")
            bt = imp_counts.get("breakthrough", 0)
            sig = imp_counts.get("significant", 0)
            output.append(f"**Importance:** {bt} breakthrough, {sig} significant, {imp_counts.get('routine', 0)} routine")

            output.append(f"\n### Thinking Stages")
            for s, c in sorted(stage_counts.items(), key=lambda x: -x[1]):
                pct = c / len(rows) * 100
                bar = "█" * int(pct / 5)
                output.append(f"  {s}: {c} ({pct:.0f}%) {bar}")

            if source_counts:
                output.append(f"\n### Sources")
                for s, c in sorted(source_counts.items(), key=lambda x: -x[1]):
                    output.append(f"  {s}: {c}")

            if pattern_counts:
                output.append(f"\n### Cognitive Patterns")
                for p, c in sorted(pattern_counts.items(), key=lambda x: -x[1])[:7]:
                    output.append(f"  {p}: {c}")

            if approach_counts:
                output.append(f"\n### Problem Solving Approaches")
                for a, c in sorted(approach_counts.items(), key=lambda x: -x[1])[:7]:
                    output.append(f"  {a}: {c}")

            if tone_counts:
                output.append(f"\n### Emotional Tones")
                for t, c in sorted(tone_counts.items(), key=lambda x: -x[1])[:5]:
                    output.append(f"  {t}: {c}")

            if all_concepts:
                output.append(f"\n### Top Concepts ({len(all_concepts)} total)")
                for c, n in sorted(all_concepts.items(), key=lambda x: -x[1])[:10]:
                    output.append(f"  {c}: {n}")

            return "\n".join(output)

        # ── Fallback: raw conversations grouped by month ──
        try:
            con = get_conversations()
        except FileNotFoundError:
            return "No conversation data found. Run the ingest pipeline first."

        # Use expanded keywords for broader matching
        keywords = _expand_domain_search(domain)
        all_keywords = [domain] + [kw for kw in keywords.split() if len(kw) >= 3]
        
        rows = []
        for kw in all_keywords:
            kw_pattern = f"%{kw}%"
            kw_rows = con.execute("""
                SELECT year, month, source, COUNT(*) as msgs,
                       SUM(CASE WHEN has_question = 1 AND role = 'user' THEN 1 ELSE 0 END) as questions
                FROM conversations
                WHERE content ILIKE ? OR conversation_title ILIKE ?
                GROUP BY year, month, source
                ORDER BY year, month
            """, [kw_pattern, kw_pattern]).fetchall()
            if kw_rows:
                rows = kw_rows
                break

        if not rows:
            return f"No conversations found matching domain: {domain}. Try a broader search with `semantic_search` or `search_conversations`."

        total_msgs = sum(r[3] for r in rows)
        total_qs = sum(r[4] for r in rows)
        source_counts = {}
        month_counts = {}
        for year, month, source, msgs, qs in rows:
            key = f"{year}-{month:02d}"
            month_counts[key] = month_counts.get(key, 0) + msgs
            if source:
                source_counts[source] = source_counts.get(source, 0) + msgs

        output = [f"## 📊 Tunnel History: {domain} (from raw conversations)\n"]
        output.append(f"**Total messages:** {total_msgs}")
        output.append(f"**Questions asked:** {total_qs}")
        output.append(f"**Active months:** {len(month_counts)}")

        output.append(f"\n### 📅 Activity Over Time")
        max_msgs = max(month_counts.values()) if month_counts else 1
        for period, msgs in sorted(month_counts.items()):
            bar_len = int(msgs / max_msgs * 20)
            bar = "█" * bar_len
            output.append(f"  {period}: {msgs:>4} msgs {bar}")

        if source_counts:
            output.append(f"\n### 📡 Sources")
            for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
                output.append(f"  {src}: {count}")

        output.append(_SUMMARIES_HINT)
        return "\n".join(output)

    @mcp.tool()
    def switching_cost(current_domain: str, target_domain: str) -> str:
        """
        Estimate cognitive cost of switching between domains.
        Factors: open questions left behind, shared concepts (overlap discount).
        Returns 0-1 score where lower = cheaper switch.
        """
        db = get_summaries_db()
        if db:
            cur_rows = db.execute("""
                SELECT open_questions, concepts, thinking_stage
                FROM summaries WHERE domain_primary = ?
            """, [current_domain]).fetchall()

            tgt_rows = db.execute("""
                SELECT open_questions, concepts, thinking_stage
                FROM summaries WHERE domain_primary = ?
            """, [target_domain]).fetchall()

            if not cur_rows:
                return f"No data for current domain: {current_domain}"
            if not tgt_rows:
                return f"No data for target domain: {target_domain}"

            cur_oq = set()
            cur_concepts = set()
            for row in cur_rows:
                for q in parse_json_field(row[0]):
                    if q and q.lower() != "none identified":
                        cur_oq.add(q)
                for c in parse_json_field(row[1]):
                    cur_concepts.add(c)
            cur_stage = cur_rows[0][2] or "unknown"

            tgt_concepts = set()
            for row in tgt_rows:
                for c in parse_json_field(row[1]):
                    tgt_concepts.add(c)
            tgt_stage = tgt_rows[0][2] or "unknown"

            shared = cur_concepts & tgt_concepts
            oq_cost = min(len(cur_oq) / 10.0, 1.0)
            overlap_discount = min(len(shared) / max(len(cur_concepts), 1), 1.0)
            stage_cost = {"executing": 0.8, "refining": 0.6, "crystallizing": 0.4, "exploring": 0.2}.get(cur_stage, 0.3)
            score = round((oq_cost * 0.35) + (stage_cost * 0.35) - (overlap_discount * 0.3), 3)
            score = max(0.0, min(1.0, score))

            if score < 0.3:
                rec = "✅ Low cost — go for it"
            elif score < 0.6:
                rec = "⚠️ Moderate — consider noting current open questions first"
            else:
                rec = "🔴 High cost — significant unfinished work in current domain"

            output = [f"## 🔀 Switching Cost: {current_domain} → {target_domain}\n"]
            output.append(f"### Score: **{score}** / 1.0  ({rec})\n")
            output.append(f"**Current domain:** {current_domain}")
            output.append(f"  Stage: {cur_stage}")
            output.append(f"  Open questions: {len(cur_oq)}")
            output.append(f"  Concepts: {len(cur_concepts)}")
            output.append(f"\n**Target domain:** {target_domain}")
            output.append(f"  Stage: {tgt_stage}")
            output.append(f"  Conversations: {len(tgt_rows)}")
            output.append(f"  Concepts: {len(tgt_concepts)}")
            output.append(f"\n**Overlap:** {len(shared)} shared concepts")
            if shared:
                output.append(f"  {', '.join(sorted(shared)[:10])}")
            output.append(f"\n**Cost breakdown:**")
            output.append(f"  Abandonment (open Qs): {oq_cost:.2f}")
            output.append(f"  Stage penalty ({cur_stage}): {stage_cost:.2f}")
            output.append(f"  Overlap discount: -{overlap_discount:.2f}")
            if cur_oq:
                output.append(f"\n**Questions you'd leave behind:**")
                for q in list(cur_oq)[:5]:
                    output.append(f"  ❓ {q}")

            return "\n".join(output)

        # ── Fallback: raw conversations ──
        try:
            con = get_conversations()
        except FileNotFoundError:
            return "No conversation data found. Run the ingest pipeline first."

        cur_pattern = f"%{current_domain}%"
        tgt_pattern = f"%{target_domain}%"

        cur_stats = con.execute("""
            SELECT COUNT(*) as msgs,
                   SUM(CASE WHEN has_question = 1 AND role = 'user' THEN 1 ELSE 0 END) as questions,
                   MAX(created) as last_active
            FROM conversations
            WHERE content ILIKE ? OR conversation_title ILIKE ?
        """, [cur_pattern, cur_pattern]).fetchone()

        tgt_stats = con.execute("""
            SELECT COUNT(*) as msgs,
                   SUM(CASE WHEN has_question = 1 AND role = 'user' THEN 1 ELSE 0 END) as questions,
                   MAX(created) as last_active
            FROM conversations
            WHERE content ILIKE ? OR conversation_title ILIKE ?
        """, [tgt_pattern, tgt_pattern]).fetchone()

        cur_msgs, cur_qs, cur_last = cur_stats or (0, 0, None)
        tgt_msgs, tgt_qs, tgt_last = tgt_stats or (0, 0, None)

        if not cur_msgs:
            return f"No data for current domain: {current_domain}"
        if not tgt_msgs:
            return f"No data for target domain: {target_domain}"

        # Check shared conversation titles
        shared_titles = con.execute("""
            SELECT COUNT(DISTINCT conversation_title) FROM conversations
            WHERE (content ILIKE ? OR conversation_title ILIKE ?)
              AND conversation_title IN (
                  SELECT DISTINCT conversation_title FROM conversations
                  WHERE content ILIKE ? OR conversation_title ILIKE ?
              )
        """, [cur_pattern, cur_pattern, tgt_pattern, tgt_pattern]).fetchone()[0]

        # Rough heuristic: questions = attachment, shared titles = overlap
        cur_qs = cur_qs or 0
        question_cost = min(cur_qs / 10.0, 1.0)
        volume_cost = min(cur_msgs / 100.0, 0.5)
        overlap_discount = min(shared_titles / max(cur_msgs / 10, 1), 1.0)
        score = round((question_cost * 0.4) + (volume_cost * 0.3) - (overlap_discount * 0.3), 3)
        score = max(0.0, min(1.0, score))

        if score < 0.3:
            rec = "✅ Low cost — go for it"
        elif score < 0.6:
            rec = "⚠️ Moderate — consider noting current questions first"
        else:
            rec = "🔴 High cost — significant activity in current domain"

        output = [f"## 🔀 Switching Cost: {current_domain} → {target_domain} (estimated)\n"]
        output.append(f"### Score: **{score}** / 1.0  ({rec})\n")
        output.append(f"**Current domain:** {current_domain}")
        output.append(f"  Messages: {cur_msgs}")
        output.append(f"  Questions: {cur_qs}")
        output.append(f"  Last active: {str(cur_last)[:10] if cur_last else 'unknown'}")
        output.append(f"\n**Target domain:** {target_domain}")
        output.append(f"  Messages: {tgt_msgs}")
        output.append(f"  Questions: {tgt_qs}")
        output.append(f"  Last active: {str(tgt_last)[:10] if tgt_last else 'unknown'}")
        output.append(f"\n**Shared conversations:** {shared_titles}")
        output.append(f"\n**Cost breakdown (approximate):**")
        output.append(f"  Question attachment: {question_cost:.2f}")
        output.append(f"  Volume penalty: {volume_cost:.2f}")
        output.append(f"  Overlap discount: -{overlap_discount:.2f}")
        output.append(_SUMMARIES_HINT)
        return "\n".join(output)

    @mcp.tool()
    def cognitive_patterns(domain: str = None) -> str:
        """
        Analyze cognitive patterns and problem-solving approaches.
        Answers: 'When do I think best?' with data.
        """
        db = get_summaries_db()
        if db:
            if domain:
                rows = db.execute("""
                    SELECT cognitive_pattern, problem_solving_approach, importance,
                           emotional_tone, thinking_stage, content_category
                    FROM summaries WHERE domain_primary = ?
                """, [domain]).fetchall()
            else:
                rows = db.execute("""
                    SELECT cognitive_pattern, problem_solving_approach, importance,
                           emotional_tone, thinking_stage, content_category
                    FROM summaries
                """).fetchall()

            if not rows:
                return f"No data found{f' for domain: {domain}' if domain else ''}"

            cols = [
                "cognitive_pattern", "problem_solving_approach", "importance",
                "emotional_tone", "thinking_stage", "content_category",
            ]

            pattern_counts, approach_counts, tone_counts = {}, {}, {}
            bt_patterns, bt_approaches, bt_tones = {}, {}, {}
            category_counts = {}
            total, bt_total = len(rows), 0

            for row in rows:
                r = dict(zip(cols, row))
                p = r["cognitive_pattern"] or ""
                if p:
                    pattern_counts[p] = pattern_counts.get(p, 0) + 1
                a = r["problem_solving_approach"] or ""
                if a:
                    approach_counts[a] = approach_counts.get(a, 0) + 1
                t = r["emotional_tone"] or ""
                if t:
                    tone_counts[t] = tone_counts.get(t, 0) + 1
                cat = r["content_category"] or ""
                if cat:
                    category_counts[cat] = category_counts.get(cat, 0) + 1
                if r["importance"] == "breakthrough":
                    bt_total += 1
                    if p:
                        bt_patterns[p] = bt_patterns.get(p, 0) + 1
                    if a:
                        bt_approaches[a] = bt_approaches.get(a, 0) + 1
                    if t:
                        bt_tones[t] = bt_tones.get(t, 0) + 1

            output = [f"## 🧬 Cognitive Patterns{f' ({domain})' if domain else ''}\n"]
            output.append(f"_Analyzed {total} conversations ({bt_total} breakthroughs)_\n")

            output.append(f"### Cognitive Patterns")
            for p, c in sorted(pattern_counts.items(), key=lambda x: -x[1])[:10]:
                bt = bt_patterns.get(p, 0)
                bt_mark = f" (💎×{bt})" if bt else ""
                output.append(f"  {p}: {c} ({c/total*100:.0f}%){bt_mark}")

            output.append(f"\n### Problem Solving Approaches")
            for a, c in sorted(approach_counts.items(), key=lambda x: -x[1])[:10]:
                bt = bt_approaches.get(a, 0)
                bt_mark = f" (💎×{bt})" if bt else ""
                output.append(f"  {a}: {c} ({c/total*100:.0f}%){bt_mark}")

            output.append(f"\n### Emotional Tones")
            for t, c in sorted(tone_counts.items(), key=lambda x: -x[1])[:8]:
                bt = bt_tones.get(t, 0)
                bt_mark = f" (💎×{bt})" if bt else ""
                output.append(f"  {t}: {c}{bt_mark}")

            if category_counts:
                output.append(f"\n### Content Categories")
                for cat, c in sorted(category_counts.items(), key=lambda x: -x[1])[:8]:
                    output.append(f"  {cat}: {c}")

            if bt_patterns:
                top_bt = max(bt_patterns, key=bt_patterns.get)
                output.append(f"\n### 💎 Breakthrough Insight")
                output.append(f"Your breakthroughs most associate with **{top_bt}** thinking")
                if bt_tones:
                    top_tone = max(bt_tones, key=bt_tones.get)
                    output.append(f"and tend to happen when you're in a **{top_tone}** emotional state.")

            return "\n".join(output)

        # ── Fallback: basic stats from raw conversations ──
        try:
            con = get_conversations()
        except FileNotFoundError:
            return "No conversation data found. Run the ingest pipeline first."

        if domain:
            # Use expanded keywords for broader matching
            keywords = _expand_domain_search(domain)
            all_keywords = [domain] + [kw for kw in keywords.split() if len(kw) >= 3]
            
            stats = None
            matched_pattern = None
            for kw in all_keywords:
                kw_pattern = f"%{kw}%"
                kw_stats = con.execute("""
                    SELECT COUNT(*) as msgs,
                           SUM(CASE WHEN has_question = 1 AND role = 'user' THEN 1 ELSE 0 END) as questions,
                           COUNT(DISTINCT source) as sources,
                           COUNT(DISTINCT conversation_title) as convos,
                           MIN(created) as first_seen,
                           MAX(created) as last_seen
                    FROM conversations
                    WHERE content ILIKE ? OR conversation_title ILIKE ?
                """, [kw_pattern, kw_pattern]).fetchone()
                if kw_stats and kw_stats[0] > 0:
                    stats = kw_stats
                    matched_pattern = kw_pattern
                    break
            
            if not stats or stats[0] == 0:
                matched_pattern = f"%{domain}%"
                stats = (0, 0, 0, 0, None, None)

            pattern = matched_pattern or f"%{domain}%"

            month_rows = con.execute("""
                SELECT year, month, COUNT(*) as msgs
                FROM conversations
                WHERE content ILIKE ? OR conversation_title ILIKE ?
                GROUP BY year, month
                ORDER BY msgs DESC
                LIMIT 5
            """, [pattern, pattern]).fetchall()

            source_rows = con.execute("""
                SELECT source, COUNT(*) as msgs
                FROM conversations
                WHERE content ILIKE ? OR conversation_title ILIKE ?
                GROUP BY source
                ORDER BY msgs DESC
            """, [pattern, pattern]).fetchall()
        else:
            stats = con.execute("""
                SELECT COUNT(*) as msgs,
                       SUM(CASE WHEN has_question = 1 AND role = 'user' THEN 1 ELSE 0 END) as questions,
                       COUNT(DISTINCT source) as sources,
                       COUNT(DISTINCT conversation_title) as convos,
                       MIN(created) as first_seen,
                       MAX(created) as last_seen
                FROM conversations
            """).fetchone()

            month_rows = con.execute("""
                SELECT year, month, COUNT(*) as msgs
                FROM conversations
                GROUP BY year, month
                ORDER BY msgs DESC
                LIMIT 5
            """).fetchall()

            source_rows = con.execute("""
                SELECT source, COUNT(*) as msgs
                FROM conversations
                GROUP BY source
                ORDER BY msgs DESC
            """).fetchall()

        total_msgs, total_qs, n_sources, n_convos, first, last = stats
        if not total_msgs:
            return f"No data found{f' for domain: {domain}' if domain else ''}"

        output = [f"## 🧬 Cognitive Patterns{f' ({domain})' if domain else ''} (basic stats)\n"]
        output.append(f"_Cannot determine cognitive patterns without summaries — showing activity stats_\n")
        output.append(f"**Total messages:** {total_msgs:,}")
        output.append(f"**Questions asked:** {total_qs:,}")
        output.append(f"**Conversations:** {n_convos:,}")
        output.append(f"**Sources:** {n_sources}")
        output.append(f"**Active period:** {str(first)[:10]} to {str(last)[:10]}")

        if month_rows:
            output.append(f"\n### 📅 Most Active Months")
            for year, month, msgs in month_rows:
                output.append(f"  {year}-{month:02d}: {msgs} messages")

        if source_rows:
            output.append(f"\n### 📡 Sources")
            for src, msgs in source_rows:
                output.append(f"  {src}: {msgs:,}")

        output.append(_SUMMARIES_HINT)
        return "\n".join(output)

    @mcp.tool()
    def open_threads(limit_per_domain: int = 5, max_domains: int = 20) -> str:
        """
        Global inventory of ALL open questions across ALL domains.
        The 'unfinished business' dashboard.
        """
        db = get_summaries_db()
        if db:
            rows = db.execute("""
                SELECT domain_primary, open_questions, importance, thinking_stage
                FROM summaries
                WHERE domain_primary != '' AND domain_primary IS NOT NULL
            """).fetchall()

            if not rows:
                return "No data found."

            domain_data = {}
            for domain, oq_str, importance, stage in rows:
                if domain not in domain_data:
                    domain_data[domain] = {"questions": [], "count": 0, "bt": 0, "stage": stage}
                domain_data[domain]["count"] += 1
                if importance == "breakthrough":
                    domain_data[domain]["bt"] += 1
                for q in parse_json_field(oq_str):
                    if q and q.lower() != "none identified" and q not in domain_data[domain]["questions"]:
                        domain_data[domain]["questions"].append(q)

            active = [(d, v) for d, v in domain_data.items() if v["questions"]]
            active.sort(key=lambda x: (-x[1]["bt"], -len(x[1]["questions"])))

            total_q = sum(len(v["questions"]) for _, v in active)
            output = [f"## 🧵 Open Threads\n"]
            output.append(f"**{total_q} open questions** across **{len(active)} domains**\n")

            for domain, data in active[:max_domains]:
                bt = f" 💎×{data['bt']}" if data["bt"] else ""
                output.append(f"### {domain}{bt} ({len(data['questions'])} questions, {data['count']} convos)")
                for q in data["questions"][:limit_per_domain]:
                    output.append(f"  ❓ {q}")
                if len(data["questions"]) > limit_per_domain:
                    output.append(f"  _... and {len(data['questions'])-limit_per_domain} more_")
                output.append("")

            return "\n".join(output)

        # ── Fallback: user questions from raw conversations ──
        try:
            con = get_conversations()
        except FileNotFoundError:
            return "No conversation data found. Run the ingest pipeline first."

        rows = con.execute("""
            SELECT conversation_title, content, created
            FROM conversations
            WHERE role = 'user' AND has_question = 1
            ORDER BY created DESC
            LIMIT 50
        """).fetchall()

        if not rows:
            return "No questions found in conversations."

        # Group by conversation title
        topic_questions = {}
        for title, content, created in rows:
            key = title or "Untitled"
            if key not in topic_questions:
                topic_questions[key] = []
            topic_questions[key].append((content, created))

        total_q = sum(len(qs) for qs in topic_questions.values())
        output = [f"## 🧵 Open Threads (from raw conversations)\n"]
        output.append(f"**{total_q} recent questions** across **{len(topic_questions)} topics**\n")

        shown = 0
        for topic, questions in list(topic_questions.items()):
            if shown >= max_domains:
                break
            output.append(f"### {topic} ({len(questions)} questions)")
            for content, created in questions[:limit_per_domain]:
                snippet = (content or "")[:200].replace("\n", " ")
                output.append(f"  ❓ {snippet}")
            if len(questions) > limit_per_domain:
                output.append(f"  _... and {len(questions)-limit_per_domain} more_")
            output.append("")
            shown += 1

        output.append(_SUMMARIES_HINT)
        return "\n".join(output)

    @mcp.tool()
    def trust_dashboard() -> str:
        """
        System-wide stats proving the prosthetic works.
        Shows everything that's preserved: conversations, domains, questions, decisions.
        The 'everything is okay' view.
        """
        db = get_summaries_db()
        if db:
            stats = db.execute("""
                SELECT COUNT(*) as total,
                       COUNT(DISTINCT domain_primary) as domains,
                       COUNT(CASE WHEN importance = 'breakthrough' THEN 1 END) as breakthroughs
                FROM summaries
            """).fetchone()
            total, domains, breakthroughs = stats

            rows = db.execute("SELECT open_questions, decisions FROM summaries").fetchall()
            total_oq, total_dec = 0, 0
            for oq_str, dec_str in rows:
                oqs = parse_json_field(oq_str)
                total_oq += sum(1 for q in oqs if q and q.lower() != "none identified")
                total_dec += len(parse_json_field(dec_str))

            sources = db.execute("""
                SELECT source, COUNT(*) as count FROM summaries GROUP BY source ORDER BY count DESC
            """).fetchall()

            domain_rows = db.execute("""
                SELECT domain_primary, COUNT(*) as count,
                       MAX(thinking_stage) as stage,
                       COUNT(CASE WHEN importance='breakthrough' THEN 1 END) as bt
                FROM summaries
                WHERE domain_primary != '' AND domain_primary IS NOT NULL
                GROUP BY domain_primary ORDER BY count DESC
            """).fetchall()

            oq_domains = set()
            for row in db.execute("SELECT domain_primary, open_questions FROM summaries").fetchall():
                for q in parse_json_field(row[1]):
                    if q and q.lower() != "none identified":
                        oq_domains.add(row[0])
                        break

            output = [f"## 🛡️ Trust Dashboard\n"]
            output.append(f"_Your cognitive safety net — proof that nothing is lost_\n")
            output.append(f"### 📊 Global Metrics")
            output.append(f"  **Conversations indexed:** {total:,}")
            output.append(f"  **Domains tracked:** {domains}")
            output.append(f"  **Open questions preserved:** {total_oq:,}")
            output.append(f"  **Decisions preserved:** {total_dec:,}")
            output.append(f"  **Breakthroughs captured:** {breakthroughs} 💎")
            output.append(f"  **Domains with active threads:** {len(oq_domains)}")

            output.append(f"\n### 📡 Sources")
            for src, count in sources:
                output.append(f"  {src}: {count:,}")

            output.append(f"\n### 🗺️ Domain Coverage (top 15)")
            for domain, count, stage, bt in domain_rows[:15]:
                bt_mark = f" 💎×{bt}" if bt else ""
                has_oq = " 🔴" if domain in oq_domains else " ✅"
                output.append(f"  {domain}: {count} convos ({stage or '?'}){bt_mark}{has_oq}")

            output.append(f"\n### 🔑 Safety Net Status")
            output.append(f"  {'🟢' if total > 5000 else '🟡'} Coverage: {total:,} conversations")
            output.append(f"  {'🟢' if domains > 15 else '🟡'} Breadth: {domains} domains")
            output.append(f"  {'🟢' if breakthroughs > 10 else '🟡'} Depth: {breakthroughs} breakthroughs captured")
            output.append(f"  {'🔴' if len(oq_domains) > 10 else '🟢'} Open threads: {len(oq_domains)} domains need attention")

            return "\n".join(output)

        # ── Fallback: stats from raw conversations + vectors ──
        try:
            con = get_conversations()
        except FileNotFoundError:
            return "No conversation data found. Run the ingest pipeline first."

        stats = con.execute("""
            SELECT COUNT(*) as total,
                   COUNT(DISTINCT conversation_title) as convos,
                   COUNT(DISTINCT source) as sources,
                   MIN(created) as first_msg,
                   MAX(created) as last_msg,
                   SUM(CASE WHEN has_question = 1 AND role = 'user' THEN 1 ELSE 0 END) as questions
            FROM conversations
        """).fetchone()
        total, convos, n_sources, first_msg, last_msg, total_qs = stats

        source_rows = con.execute("""
            SELECT source, COUNT(*) as count
            FROM conversations
            GROUP BY source
            ORDER BY count DESC
        """).fetchall()

        vec_count = lance_count()

        output = [f"## 🛡️ Trust Dashboard\n"]
        output.append(f"_Your cognitive safety net — what's preserved_\n")

        output.append(f"### 📊 Data Inventory")
        output.append(f"  **Total messages:** {total:,}")
        output.append(f"  **Conversations:** {convos:,}")
        output.append(f"  **Questions captured:** {total_qs:,}")
        output.append(f"  **Vectors indexed:** {vec_count:,}")
        output.append(f"  **Date range:** {str(first_msg)[:10]} to {str(last_msg)[:10]}")

        if source_rows:
            output.append(f"\n### 📡 Sources")
            for src, count in source_rows:
                output.append(f"  {src}: {count:,}")

        output.append(f"\n### 🔑 Pipeline Status")
        output.append(f"  🟢 Conversations: {total:,} messages ingested")
        output.append(f"  {'🟢' if vec_count > 0 else '🔴'} Vectors: {vec_count:,} embeddings indexed")
        output.append(f"  🔴 Summaries: not generated")
        output.append(f"\n### What's available without summaries")
        output.append(f"  - Keyword search across all conversations")
        output.append(f"  - Semantic (vector) search")
        output.append(f"  - Basic conversation stats and history")
        output.append(f"  - Approximate open threads and switching costs")
        output.append(f"\n### What summaries add")
        output.append(f"  - Thinking stages, cognitive patterns, emotional tones")
        output.append(f"  - Structured open questions and decisions")
        output.append(f"  - Domain classification and breakthrough detection")
        output.append(f"  - Key insights and quotable moments")
        output.append(_SUMMARIES_HINT)
        return "\n".join(output)
