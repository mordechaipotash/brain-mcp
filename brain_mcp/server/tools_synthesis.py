"""
brain-mcp — Synthesis tools.

Tools that combine multiple data sources to synthesize views,
check alignment, track concept evolution, and provide time-travel snapshots.
"""

from brain_mcp.config import get_config
from .db import (
    get_conversations,
    get_embedding,
    get_lance_db,
    get_summaries_lance,
    get_principles,
    has_summaries,
    lance_search,
    parse_json_field,
    SUMMARIES_TABLE,
)
from .tools_prosthetic import _cite


def register(mcp):
    """Register synthesis tools with the MCP server."""

    @mcp.tool()
    def what_do_i_think(topic: str, mode: str = "synthesize") -> str:
        """
        Synthesize what you think about a topic, or find similar past situations.

        Args:
            topic: The topic or situation to analyze
            mode: Analysis mode:
                - "synthesize" (default): Full synthesis with decisions, open questions, quotes
                - "precedent": Find similar past situations with context and decisions made
        """
        if mode == "precedent":
            return _find_precedent(topic)

        output = [f"## What do I think about: {topic}\n"]

        embedding = get_embedding(f"search_query: {topic}")
        if not embedding:
            return "Could not generate embedding for topic."

        lance = get_summaries_lance()
        if not lance:
            return _what_do_i_think_raw(topic, embedding, output)

        try:
            tbl = lance.open_table(SUMMARIES_TABLE)
            results = tbl.search(embedding).limit(20).to_list()
        except Exception as e:
            return f"Summary search error: {e}"

        if not results:
            output.append("_No structured thoughts found on this topic._")
            return "\n".join(output)

        # Prioritize by importance
        importance_order = {"breakthrough": 0, "significant": 1, "routine": 2}
        results.sort(key=lambda r: importance_order.get(r.get("importance", "routine"), 2))

        all_decisions = []
        all_open_questions = []
        all_quotables = []
        summaries_shown = 0

        output.append("### Summary of Thinking\n")
        for r in results[:10]:
            title = r.get("title", "Untitled") or "Untitled"
            summary = r.get("summary", "")
            importance = r.get("importance", "?")
            domain = r.get("domain_primary", "?")
            stage = r.get("thinking_stage", "?")
            conv_id = r.get("conversation_id", "?")
            summarized_at = r.get("summarized_at")
            citation = _cite(conv_id, summarized_at)

            if summary and summaries_shown < 5:
                imp_icon = {"breakthrough": "🔥", "significant": "⭐", "routine": "📝"}.get(importance, "📝")
                output.append(f"{imp_icon} **{title}** [{domain} | {stage}] {citation}")
                output.append(f"> {summary[:300]}{'...' if len(summary) > 300 else ''}")
                output.append("")
                summaries_shown += 1

            for d in parse_json_field(r.get("decisions")):
                if d and "none identified" not in str(d).lower():
                    all_decisions.append((d, title, conv_id, summarized_at))

            for q in parse_json_field(r.get("open_questions")):
                if q and "none identified" not in str(q).lower():
                    all_open_questions.append((q, title, conv_id, summarized_at))

            for q in parse_json_field(r.get("quotable")):
                if q and "none identified" not in str(q).lower():
                    all_quotables.append((q, title, conv_id, summarized_at))

        if all_decisions:
            output.append("### Key Decisions\n")
            seen = set()
            for decision, title, conv_id, ts in all_decisions[:10]:
                d_key = decision[:80].lower()
                if d_key not in seen:
                    seen.add(d_key)
                    output.append(f"- {decision[:200]} {_cite(conv_id, ts)}")
                    output.append(f"  _From: {title}_")

        if all_open_questions:
            output.append("\n### Still Open\n")
            seen = set()
            for question, title, conv_id, ts in all_open_questions[:8]:
                q_key = question[:80].lower()
                if q_key not in seen:
                    seen.add(q_key)
                    output.append(f"- {question[:200]} {_cite(conv_id, ts)}")
                    output.append(f"  _From: {title}_")

        if all_quotables:
            output.append("\n### Authentic Quotes\n")
            for quote, title, conv_id, ts in all_quotables[:5]:
                output.append(f"> \"{quote[:250]}\"")
                output.append(f"> — _{title}_ {_cite(conv_id, ts)}\n")

        return "\n".join(output)

    def _what_do_i_think_raw(topic: str, embedding: list[float], output: list[str]) -> str:
        """Fallback synthesis from raw messages when summaries aren't available."""
        cfg = get_config()

        # Semantic matches
        semantic_results = []
        if cfg.lance_path.exists():
            semantic_results = lance_search(embedding, limit=20, min_sim=0.3)

        # Keyword matches from conversations
        keyword_results = []
        try:
            con = get_conversations()
            keyword_results = con.execute("""
                SELECT conversation_title, substr(content, 1, 300) as preview,
                       created, conversation_id
                FROM conversations
                WHERE content ILIKE ? AND role = 'user'
                ORDER BY created DESC
                LIMIT 10
            """, [f"%{topic}%"]).fetchall()
        except Exception:
            pass

        if not semantic_results and not keyword_results:
            output.append("_No thoughts found on this topic._")
            return "\n".join(output)

        if semantic_results:
            output.append("### Semantically Related Thoughts\n")
            for title, content, year, month, sim in semantic_results[:10]:
                preview = content[:250] + "..." if len(content) > 250 else content
                output.append(f"**[{year}-{month:02d}]** {title or 'Untitled'} (sim: {sim:.2f})")
                output.append(f"> {preview}\n")

        if keyword_results:
            output.append("### Direct Mentions\n")
            seen_convos = set()
            for title, preview, created, conv_id in keyword_results:
                if conv_id in seen_convos:
                    continue
                seen_convos.add(conv_id)
                date_str = str(created)[:10]
                output.append(f"**[{date_str}]** {title or 'Untitled'}")
                output.append(f"> {preview}{'...' if len(preview) >= 300 else ''}\n")

        output.append("---")
        output.append("_Running without summaries. For richer synthesis with "
                       "decisions, open questions, and quotes: `brain-mcp summarize`_")
        return "\n".join(output)

    def _find_precedent_raw(situation: str, embedding: list[float]) -> str:
        """Fallback precedent search from raw messages when summaries aren't available."""
        cfg = get_config()
        results = []
        if cfg.lance_path.exists():
            results = lance_search(embedding, limit=15, min_sim=0.3)

        if not results:
            return f"No precedents found for: {situation}"

        output = [f"## Precedents for: {situation}\n"]
        output.append(f"_Found {len(results)} similar past situations_\n")

        for i, (title, content, year, month, sim) in enumerate(results[:10]):
            preview = content[:350] + "..." if len(content) > 350 else content
            output.append(f"### {i+1}. {title or 'Untitled'}")
            output.append(f"**Date**: {year}-{month:02d} | **Similarity**: {sim:.2f}")
            output.append(f"> {preview}\n")

        output.append("---")
        output.append("_Running without summaries. For richer precedent analysis "
                       "with decisions and domains: `brain-mcp summarize`_")
        return "\n".join(output)

    def _find_precedent(situation: str) -> str:
        """Find similar situations dealt with before."""
        embedding = get_embedding(f"search_query: {situation}")
        if not embedding:
            return "Could not generate embedding."

        lance = get_summaries_lance()
        if not lance:
            return _find_precedent_raw(situation, embedding)

        try:
            tbl = lance.open_table(SUMMARIES_TABLE)
            results = tbl.search(embedding).limit(15).to_list()
        except Exception as e:
            return f"Search error: {e}"

        if not results:
            return f"No precedents found for: {situation}"

        output = [f"## Precedents for: {situation}\n"]
        output.append(f"_Found {len(results)} similar past situations_\n")

        for i, r in enumerate(results[:10]):
            title = r.get("title", "Untitled") or "Untitled"
            summary = r.get("summary", "")
            importance = r.get("importance", "?")
            domain = r.get("domain_primary", "?")
            stage = r.get("thinking_stage", "?")
            conv_id = r.get("conversation_id", "?")
            source = r.get("source", "?")

            imp_icon = {"breakthrough": "🔥", "significant": "⭐", "routine": "📝"}.get(importance, "📝")
            output.append(f"### {i+1}. {imp_icon} {title}")
            output.append(f"**Domain**: {domain} | **Stage**: {stage} | **Source**: {source}")
            output.append(f"> {summary[:350]}{'...' if len(summary) > 350 else ''}")

            decisions = parse_json_field(r.get("decisions"))
            real_decisions = [d for d in decisions if d and "none identified" not in str(d).lower()]
            if real_decisions:
                output.append("**Decisions made**:")
                for d in real_decisions[:3]:
                    output.append(f"  - {d[:150]}")
            output.append(f"_ID: {conv_id[:20]}..._\n")

        return "\n".join(output)

    @mcp.tool()
    def alignment_check(decision: str) -> str:
        """
        Check if a decision aligns with your principles.
        Searches principles file and semantic history for guidance.
        """
        decision_lower = decision.lower()
        principles = get_principles()
        output = [f"## Alignment Check: {decision}\n"]

        # 1. Check principles (YAML/JSON)
        principles_section = principles.get("principles", principles.get(
            "SECTION_2_THE_EIGHT_UNIVERSAL_PRINCIPLES_DETAILED", {}
        ))

        relevant_principles = []
        if isinstance(principles_section, dict):
            for key, principle in principles_section.items():
                if isinstance(principle, dict):
                    name = principle.get("name", "").lower()
                    definition = principle.get("definition", "").lower()
                    if (decision_lower in name or decision_lower in definition or
                        any(word in definition for word in decision_lower.split() if len(word) > 4)):
                        relevant_principles.append({
                            "name": principle.get("name", key),
                            "definition": principle.get("definition", ""),
                            "formula": principle.get("core_formula") or principle.get("implementation_formula"),
                        })
        elif isinstance(principles_section, list):
            for principle in principles_section:
                if isinstance(principle, dict):
                    name = principle.get("name", "").lower()
                    definition = principle.get("definition", "").lower()
                    description = principle.get("description", "").lower()
                    text = f"{name} {definition} {description}"
                    if any(word in text for word in decision_lower.split() if len(word) > 4):
                        relevant_principles.append({
                            "name": principle.get("name", "Unknown"),
                            "definition": principle.get("definition", principle.get("description", "")),
                            "formula": principle.get("formula"),
                        })

        if relevant_principles:
            output.append("### Relevant Principles:\n")
            for p in relevant_principles[:3]:
                output.append(f"**{p['name']}**")
                output.append(f"> {p['definition'][:200]}...")
                if p.get("formula"):
                    output.append(f"_Formula: {p['formula']}_\n")

        # 2. Semantic search for related past decisions
        cfg = get_config()
        embedding = get_embedding(decision)
        if embedding and cfg.lance_path.exists():
            results = lance_search(embedding, limit=5, min_sim=0.35)
            if results:
                output.append("### Related Past Thinking:\n")
                for title, content, year, month, sim in results:
                    preview = content[:200] + "..." if len(content) > 200 else content
                    output.append(f"**[{year}-{month:02d}]** {title or 'Untitled'} (sim: {sim:.2f})")
                    output.append(f"> {preview}\n")

        if len(output) == 1:
            output.append("_No direct alignment guidance found. Try rephrasing or use semantic_search._")

        return "\n".join(output)

    @mcp.tool()
    def thinking_trajectory(topic: str, view: str = "full") -> str:
        """
        Track the evolution of thinking about a topic over time.

        Args:
            topic: The concept/term to track
            view: What to show:
                - "full" (default): Complete trajectory with genesis, temporal pattern, semantic matches, thinking stages
                - "velocity": How often the concept appears over time with trend analysis
                - "first": When the concept first appeared — the genesis moment
        """
        if view == "velocity":
            return _concept_velocity(topic)
        elif view == "first":
            return _first_mention(topic)

        output = [f"## Thinking Trajectory: '{topic}'\n"]
        cfg = get_config()

        # 1. Semantic matches from embeddings
        embedding = get_embedding(topic)
        semantic_results = []
        if embedding and cfg.lance_path.exists():
            lance_results = lance_search(embedding, limit=20, min_sim=0.3)
            semantic_results = [(r[2], r[3], r[0], r[1], r[4]) for r in lance_results]

        # 2. Keyword matches with temporal distribution
        try:
            conv_con = get_conversations()
            pattern = f"%{topic}%"
            temporal_dist = conv_con.execute("""
                SELECT
                    strftime(created, '%Y-%m') as period,
                    COUNT(*) as mentions
                FROM conversations
                WHERE content ILIKE ? AND role = 'user'
                GROUP BY period
                ORDER BY period
            """, [pattern]).fetchall()
        except Exception:
            temporal_dist = []

        # 3. First mention
        try:
            first_mention = conv_con.execute("""
                SELECT created, conversation_title, substr(content, 1, 200) as preview
                FROM conversations
                WHERE content ILIKE ? AND role = 'user'
                ORDER BY created ASC
                LIMIT 1
            """, [pattern]).fetchone()
        except Exception:
            first_mention = None

        if first_mention:
            output.append("### Genesis")
            output.append(f"**First appeared**: {str(first_mention[0])[:10]}")
            output.append(f"**Context**: {first_mention[1] or 'Untitled'}")
            output.append(f"> {first_mention[2]}...\n")

        if temporal_dist:
            output.append("### Temporal Pattern")
            total = sum(t[1] for t in temporal_dist)
            peak = max(temporal_dist, key=lambda x: x[1])
            output.append(f"**Total keyword mentions**: {total}")
            output.append(f"**Peak period**: {peak[0]} ({peak[1]} mentions)")

            output.append("\n**Recent activity**:")
            for period, count in temporal_dist[-6:]:
                bar = "█" * min(count, 20)
                output.append(f"  {period}: {bar} ({count})")

        if semantic_results:
            output.append("\n### Semantically Related Thoughts")
            output.append("_Messages conceptually similar, even without exact keyword match_\n")

            by_period = {}
            for year, month, title, content, sim in semantic_results[:10]:
                period = f"{year}-{month:02d}"
                if period not in by_period:
                    by_period[period] = []
                by_period[period].append((title, content[:150], sim))

            for period in sorted(by_period.keys(), reverse=True)[:4]:
                items = by_period[period]
                output.append(f"**{period}** ({len(items)} related thoughts):")
                for title, preview, sim in items[:2]:
                    output.append(f"  - [{sim:.2f}] {title or 'Untitled'}: {preview}...")

        # 4. Thinking Stage Progression from summaries
        lance = get_summaries_lance()
        if lance:
            try:
                v6_embedding = get_embedding(f"search_query: {topic}")
                if v6_embedding:
                    tbl = lance.open_table(SUMMARIES_TABLE)
                    v6_results = tbl.search(v6_embedding).limit(20).to_list()
                    if v6_results:
                        stage_order = {"exploring": 0, "crystallizing": 1, "refining": 2, "executing": 3}
                        stage_items = []
                        for r in v6_results:
                            stage = r.get("thinking_stage", "")
                            if stage in stage_order:
                                stage_items.append((
                                    stage_order[stage],
                                    stage,
                                    r.get("title", "Untitled") or "Untitled",
                                    r.get("importance", "?"),
                                    r.get("domain_primary", "?"),
                                ))
                        if stage_items:
                            stage_items.sort(key=lambda x: x[0])
                            output.append("\n### Thinking Stage Progression")
                            stage_icons = {"exploring": "🔍", "crystallizing": "💎", "refining": "🔧", "executing": "🚀"}
                            current_stage = None
                            for _, stage, title, imp, domain in stage_items:
                                if stage != current_stage:
                                    current_stage = stage
                                    output.append(f"\n{stage_icons.get(stage, '📝')} **{stage.upper()}**")
                                output.append(f"  - {title} [{domain} | {imp}]")
            except Exception:
                pass

        if not (temporal_dist or semantic_results):
            output.append("_No trajectory data found for this topic._")

        return "\n".join(output)

    def _concept_velocity(term: str, granularity: str = "month") -> str:
        """Track how often a concept appears over time."""
        con = get_conversations()
        if granularity == "quarter":
            time_group = "year || '-Q' || ((month-1)/3 + 1)"
        else:
            time_group = "year || '-' || LPAD(CAST(month AS VARCHAR), 2, '0')"

        pattern = f"%{term}%"
        results = con.execute(f"""
            SELECT {time_group} as period, COUNT(*) as mentions,
                   COUNT(DISTINCT conversation_id) as conversations
            FROM conversations
            WHERE content ILIKE ? AND role = 'user'
            GROUP BY {time_group}
            ORDER BY period ASC
        """, [pattern]).fetchall()

        if not results:
            return f"No mentions of '{term}' found"

        max_mentions = max(r[1] for r in results)
        peak_period = [r[0] for r in results if r[1] == max_mentions][0]

        if len(results) >= 6:
            early_avg = sum(r[1] for r in results[:3]) / 3
            recent_avg = sum(r[1] for r in results[-3:]) / 3
            if recent_avg > early_avg * 1.5:
                trend = "📈 ACCELERATING"
            elif recent_avg < early_avg * 0.5:
                trend = "📉 DECLINING"
            else:
                trend = "➡️ STABLE"
        else:
            trend = "📊 INSUFFICIENT DATA"

        output = [
            f"## Concept Velocity: '{term}'\n",
            f"**Trend**: {trend}",
            f"**Peak**: {peak_period} ({max_mentions} mentions)",
            f"**Total mentions**: {sum(r[1] for r in results)}\n",
            f"### Timeline:"
        ]
        for period, mentions, _ in results:
            bar = "█" * min(mentions, 30)
            peak_marker = " ← PEAK" if period == peak_period else ""
            output.append(f"{period}: {mentions:>3} {bar}{peak_marker}")

        return "\n".join(output)

    def _first_mention(term: str) -> str:
        """Find when a concept first appeared."""
        con = get_conversations()
        pattern = f"%{term}%"

        first = con.execute("""
            SELECT created, conversation_title, conversation_id,
                   substr(content, 1, 300) as preview, source
            FROM conversations
            WHERE content ILIKE ? AND role = 'user'
            ORDER BY created ASC LIMIT 1
        """, [pattern]).fetchone()

        if not first:
            return f"No mentions of '{term}' found"

        created, title, conv_id, preview, source = first

        total_mentions, total_convos = con.execute("""
            SELECT COUNT(*), COUNT(DISTINCT conversation_id)
            FROM conversations WHERE content ILIKE ? AND role = 'user'
        """, [pattern]).fetchone()

        last = con.execute("""
            SELECT created, conversation_title
            FROM conversations WHERE content ILIKE ? AND role = 'user'
            ORDER BY created DESC LIMIT 1
        """, [pattern]).fetchone()
        last_created, last_title = last

        time_span = last_created - created
        days_span = time_span.days if hasattr(time_span, "days") else 0

        output = [
            f"## First Mention: '{term}'\n",
            f"### Genesis Moment",
            f"**Date**: {created}",
            f"**Conversation**: {title or 'Untitled'}",
            f"**Source**: {source}",
            f"**Context**:",
            f"> {preview}...\n",
            f"### Journey Since",
            f"- **First**: {str(created)[:10]}",
            f"- **Latest**: {str(last_created)[:10]} ({last_title or 'Untitled'})",
            f"- **Span**: {days_span} days",
            f"- **Total mentions**: {total_mentions} across {total_convos} conversations",
            f"\n_Conversation ID: {conv_id}_",
        ]

        return "\n".join(output)

    @mcp.tool()
    def what_was_i_thinking(month: str) -> str:
        """
        Time-travel snapshot: What was on your mind during a specific month?
        Format: YYYY-MM (e.g., '2024-08')
        """
        con = get_conversations()
        try:
            year, mon = month.split("-")
            year, mon = int(year), int(mon)
        except Exception:
            return f"Invalid month format. Use YYYY-MM (e.g., '2024-08')"

        stats = con.execute("""
            SELECT COUNT(*) as total_msgs,
                   COUNT(DISTINCT conversation_id) as convos,
                   SUM(CASE WHEN role='user' THEN 1 ELSE 0 END) as user_msgs,
                   SUM(CASE WHEN has_question=1 AND role='user' THEN 1 ELSE 0 END) as questions_asked
            FROM conversations WHERE year = ? AND month = ?
        """, [year, mon]).fetchone()
        total_msgs, convos, user_msgs, questions = stats

        if total_msgs == 0:
            return f"No data found for {month}"

        avg_query = """
            SELECT AVG(monthly_count) FROM (
                SELECT COUNT(*) as monthly_count FROM conversations GROUP BY year, month
            )
        """
        avg_monthly = con.execute(avg_query).fetchone()[0] or 0

        top_titles = con.execute("""
            SELECT conversation_title, COUNT(*) as msg_count
            FROM conversations
            WHERE year = ? AND month = ?
              AND conversation_title IS NOT NULL
              AND conversation_title != '' AND conversation_title != 'Untitled'
            GROUP BY conversation_title ORDER BY msg_count DESC LIMIT 10
        """, [year, mon]).fetchall()

        sample_questions = con.execute("""
            SELECT substr(content, 1, 150) as question, created
            FROM conversations
            WHERE year = ? AND month = ? AND role = 'user' AND has_question = 1
            ORDER BY created DESC LIMIT 5
        """, [year, mon]).fetchall()

        sources = con.execute("""
            SELECT source, COUNT(*) as count FROM conversations
            WHERE year = ? AND month = ? GROUP BY source ORDER BY count DESC
        """, [year, mon]).fetchall()

        activity_level = (
            "🔥 HIGH" if total_msgs > avg_monthly * 1.5
            else "📊 NORMAL" if total_msgs > avg_monthly * 0.5
            else "📉 LOW"
        )

        output = [
            f"## What Was I Thinking: {month}\n",
            f"### Activity Level: {activity_level}",
            f"- **{total_msgs:,}** messages ({int(total_msgs/avg_monthly*100)}% of average)",
            f"- **{convos:,}** conversations",
            f"- **{user_msgs:,}** messages from you",
            f"- **{questions:,}** questions asked\n",
            "### Sources:"
        ]
        for source, count in sources:
            output.append(f"- {source}: {count:,}")

        if top_titles:
            output.append("\n### Top Conversations (themes):")
            for title, count in top_titles[:7]:
                output.append(f"- {title} ({count} msgs)")

        if sample_questions:
            output.append("\n### Sample Questions You Asked:")
            for q, _ in sample_questions:
                output.append(f"- \"{q}...\"")

        return "\n".join(output)
