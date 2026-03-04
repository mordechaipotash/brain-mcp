"""
brain-mcp — Search tools.

Semantic search, summary search, unified cross-source search, and doc search.
"""

import re

from brain_mcp.config import get_config
from .db import (
    get_conversations,
    get_embedding,
    get_lance_db,
    get_lance_db as _get_lance_db,
    get_summaries_lance,
    get_markdown_db,
    get_github_db,
    lance_search,
    lance_count,
    parse_json_field,
    sanitize_sql_value,
    SUMMARIES_TABLE,
)


def register(mcp):
    """Register search tools with the MCP server."""

    @mcp.tool()
    def semantic_search(query: str, limit: int = 10) -> str:
        """
        Search conversations using semantic similarity (vector embeddings).
        Finds messages that are conceptually similar to your query,
        even if they don't contain the exact words.

        More powerful than keyword search for finding related ideas.
        """
        cfg = get_config()

        if not cfg.lance_path.exists():
            return "Vector database not found. Run the embed pipeline first."

        embedding = get_embedding(query)
        if not embedding:
            try:
                import sentence_transformers  # noqa: F401
                return "Could not generate embedding for query."
            except ImportError:
                return ("Semantic search requires the embedding model.\n"
                        "Install with: pip install brain-mcp[embed]\n"
                        "Then run: brain-mcp embed\n\n"
                        "Meanwhile, use search_conversations() for keyword search.")

        db = get_lance_db()
        if not db:
            return "Could not connect to vector database."

        try:
            tbl = db.open_table("message")
            results = tbl.search(embedding).limit(limit).to_pandas()
        except Exception as e:
            return f"Search error: {e}"

        if results.empty:
            return "No results found."

        output = [f"## Semantic Search: '{query}'\n"]
        output.append(f"_Found {len(results)} semantically similar messages_\n")

        for i, row in results.iterrows():
            title = row.get("conversation_title") or "Untitled"
            content = str(row.get("content", ""))
            year = row.get("year", 0)
            month = row.get("month", 0)
            distance = row.get("_distance", 0)

            preview = content[:400] + "..." if len(content) > 400 else content

            output.append(f"### {i+1}. [{year}-{month:02d}] {title}")
            output.append(f"**Similarity**: {distance:.4f}")
            output.append(f"> {preview}\n")

        return "\n".join(output)

    @mcp.tool()
    def search_summaries(
        query: str,
        extract: str = "summary",
        limit: int = 10,
        domain: str = None,
        importance: str = None,
        thinking_stage: str = None,
        source: str = None,
        mode: str = "hybrid",
    ) -> str:
        """
        Search conversation summaries with hybrid vector + keyword search.

        Args:
            query: Search query
            extract: What to extract from results:
                - "summary" (default): Full summary with metadata
                - "questions": Open questions from matching conversations
                - "decisions": Decisions made in matching conversations
                - "quotes": Quotable phrases from matching conversations
            limit: Max results (default 10)
            domain: Filter by domain (e.g. "ai-dev", "business-strategy")
            importance: Filter by importance ("breakthrough", "significant", "routine")
            thinking_stage: Filter by stage ("exploring", "crystallizing", "refining", "executing")
            source: Filter by source ("claude-code", "chatgpt", etc.)
            mode: Search mode — "hybrid" (default), "vector", "fts"
        """
        if extract == "questions":
            return _extract_open_questions(query, domain=domain, importance=importance,
                                           thinking_stage=thinking_stage, source=source,
                                           limit=limit, mode=mode)
        elif extract == "decisions":
            return _extract_decisions(query, domain=domain, importance=importance,
                                      thinking_stage=thinking_stage, source=source,
                                      limit=limit, mode=mode)
        elif extract == "quotes":
            return _extract_quotes(query, domain=domain, importance=importance,
                                   thinking_stage=thinking_stage, source=source,
                                   limit=limit, mode=mode)

        lance = get_summaries_lance()
        if not lance:
            return (
                "Summary vector database not found. "
                "To use summary search, run the summarize pipeline:\n"
                "  python -m summarize.summarize\n\n"
                "For basic search, use semantic_search or search_conversations instead."
            )

        try:
            tbl = lance.open_table(SUMMARIES_TABLE)
        except Exception as e:
            return f"Summary table not found ({e})."

        # Build SQL filter (sanitize user inputs for LanceDB where clauses)
        filters = []
        if domain:
            filters.append(f"domain_primary = '{sanitize_sql_value(domain)}'")
        if importance:
            filters.append(f"importance = '{sanitize_sql_value(importance)}'")
        if thinking_stage:
            filters.append(f"thinking_stage = '{sanitize_sql_value(thinking_stage)}'")
        if source:
            filters.append(f"source = '{sanitize_sql_value(source)}'")
        where_clause = " AND ".join(filters) if filters else None

        try:
            if mode == "hybrid":
                search = tbl.search(query, query_type="hybrid").limit(limit * 3)
                if where_clause:
                    search = search.where(where_clause)
                results = search.to_list()
            elif mode == "fts":
                search = tbl.search(query, query_type="fts").limit(limit)
                if where_clause:
                    search = search.where(where_clause)
                results = search.to_list()
            else:
                embedding = get_embedding(f"search_query: {query}")
                if not embedding:
                    return "Could not generate embedding."
                search = tbl.search(embedding).limit(limit)
                if where_clause:
                    search = search.where(where_clause)
                results = search.to_list()
        except Exception:
            # Fallback to vector-only
            embedding = get_embedding(f"search_query: {query}")
            if not embedding:
                return "Could not generate embedding."
            search = tbl.search(embedding).limit(limit)
            if where_clause:
                search = search.where(where_clause)
            results = search.to_list()

        if not results:
            return f"No summaries found for: {query}"

        # Optional cross-encoder reranking
        try:
            from sentence_transformers import CrossEncoder
            reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            pairs = [(query, r.get("summary", "")) for r in results]
            scores = reranker.predict(pairs)
            for i, r in enumerate(results):
                r["rerank_score"] = float(scores[i])
            results.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
        except Exception:
            pass

        results = results[:limit]

        output = [f"## Summary Search: '{query}'\n"]
        if where_clause:
            output.append(f"_Filters: {where_clause}_\n")
        output.append(f"_Found {len(results)} matching conversation summaries_\n")

        for i, r in enumerate(results):
            title = (r.get("title") or "Untitled")[:80]
            summary = (r.get("summary") or "")[:400]
            imp = r.get("importance", "?")
            domain_p = r.get("domain_primary", "?")
            stage = r.get("thinking_stage", "?")
            src = r.get("source", "?")
            concepts_raw = parse_json_field(r.get("concepts"))
            concepts_str = ", ".join(concepts_raw[:8]) if concepts_raw else ""
            rerank = r.get("rerank_score")
            conv_id = r.get("conversation_id", "?")

            imp_icon = {"breakthrough": "🔥", "significant": "⭐", "routine": "📝"}.get(imp, "📝")
            output.append(f"### {i+1}. {imp_icon} {title}")
            output.append(f"**Domain**: {domain_p} | **Stage**: {stage} | **Source**: {src} | **Importance**: {imp}")
            if concepts_str:
                output.append(f"**Concepts**: {concepts_str}")
            if rerank is not None:
                output.append(f"**Relevance**: {rerank:.2f} (reranked)")
            output.append(f"> {summary}")
            output.append(f"_Conversation ID: {conv_id}_\n")

        return "\n".join(output)

    def _summary_search_core(query, domain=None, importance=None, thinking_stage=None,
                              source=None, limit=10, mode="hybrid"):
        """Shared search logic for all extract modes."""
        lance = get_summaries_lance()
        if not lance:
            return []
        try:
            tbl = lance.open_table(SUMMARIES_TABLE)
        except Exception:
            return []

        filters = []
        if domain:
            filters.append(f"domain_primary = '{sanitize_sql_value(domain)}'")
        if importance:
            filters.append(f"importance = '{sanitize_sql_value(importance)}'")
        if thinking_stage:
            filters.append(f"thinking_stage = '{sanitize_sql_value(thinking_stage)}'")
        if source:
            filters.append(f"source = '{sanitize_sql_value(source)}'")
        where_clause = " AND ".join(filters) if filters else None

        try:
            if mode == "hybrid":
                search = tbl.search(query, query_type="hybrid").limit(limit * 3)
                if where_clause:
                    search = search.where(where_clause)
                return search.to_list()
            elif mode == "fts":
                search = tbl.search(query, query_type="fts").limit(limit * 2)
                if where_clause:
                    search = search.where(where_clause)
                return search.to_list()
            else:
                embedding = get_embedding(f"search_query: {query}")
                if not embedding:
                    return []
                search = tbl.search(embedding).limit(limit * 2)
                if where_clause:
                    search = search.where(where_clause)
                return search.to_list()
        except Exception:
            embedding = get_embedding(f"search_query: {query}")
            if not embedding:
                return []
            search = tbl.search(embedding).limit(limit * 2)
            if where_clause:
                search = search.where(where_clause)
            return search.to_list()

    def _extract_open_questions(query, **kwargs):
        """Extract open questions from matching summaries."""
        limit = kwargs.pop("limit", 20)
        results = _summary_search_core(query, limit=limit, **kwargs)
        if not results:
            return f"No results found for: {query}"

        output = [f"## Open Questions about: {query}\n"]
        question_count = 0
        for r in results:
            questions = parse_json_field(r.get("open_questions"))
            real_questions = [q for q in questions if q and "none identified" not in str(q).lower()]
            if not real_questions:
                continue
            title = r.get("title", "Untitled") or "Untitled"
            domain_p = r.get("domain_primary", "?")
            importance = r.get("importance", "?")
            conv_id = r.get("conversation_id", "?")
            summary = (r.get("summary") or "")[:150]
            output.append(f"### {title}")
            output.append(f"_Domain: {domain_p} | Importance: {importance} | ID: {conv_id[:20]}..._")
            output.append(f"_Context: {summary}..._\n")
            for q in real_questions:
                output.append(f"  ❓ {q[:250]}")
                question_count += 1
            output.append("")
            if question_count >= limit:
                break
        if question_count == 0:
            output.append("_No open questions found matching this topic._")
        else:
            output.append(f"\n_Total: {question_count} open questions found_")
        return "\n".join(output)

    def _extract_decisions(query, **kwargs):
        """Extract decisions from matching summaries."""
        limit = kwargs.pop("limit", 20)
        results = _summary_search_core(query, limit=limit, **kwargs)
        if not results:
            return f"No results found for: {query}"

        output = [f"## Decisions about: {query}\n"]
        decision_count = 0
        for r in results:
            decisions = parse_json_field(r.get("decisions"))
            real_decisions = [d for d in decisions if d and "none identified" not in str(d).lower()]
            if not real_decisions:
                continue
            title = r.get("title", "Untitled") or "Untitled"
            domain_p = r.get("domain_primary", "?")
            imp = r.get("importance", "?")
            stage = r.get("thinking_stage", "?")
            conv_id = r.get("conversation_id", "?")
            source = r.get("source", "?")
            imp_icon = {"breakthrough": "🔥", "significant": "⭐", "routine": "📝"}.get(imp, "📝")
            output.append(f"### {imp_icon} {title}")
            output.append(f"_Domain: {domain_p} | Stage: {stage} | Source: {source}_")
            for d in real_decisions:
                output.append(f"  ✅ {d[:250]}")
                decision_count += 1
            output.append(f"_ID: {conv_id[:20]}..._\n")
            if decision_count >= limit:
                break
        if decision_count == 0:
            output.append("_No decisions found matching this topic._")
        else:
            output.append(f"\n_Total: {decision_count} decisions found_")
        return "\n".join(output)

    def _extract_quotes(query, **kwargs):
        """Extract quotable phrases from matching summaries."""
        limit = kwargs.pop("limit", 10)
        results = _summary_search_core(query, limit=limit, **kwargs)
        if not results:
            return f"No results found for: {query}"

        output = [f"## Quotes on: {query}\n"]
        quote_count = 0
        for r in results:
            quotes = parse_json_field(r.get("quotable"))
            real_quotes = [q for q in quotes if q and "none identified" not in str(q).lower()]
            if not real_quotes:
                continue
            title = r.get("title", "Untitled") or "Untitled"
            domain_p = r.get("domain_primary", "?")
            source = r.get("source", "?")
            for q in real_quotes:
                output.append(f"> \"{q[:300]}\"")
                output.append(f"> — _{title}_ ({domain_p}, {source})\n")
                quote_count += 1
                if quote_count >= limit:
                    break
            if quote_count >= limit:
                break
        if quote_count == 0:
            output.append("_No quotable phrases found matching this topic._")
        else:
            output.append(f"_Total: {quote_count} quotes found_")
        return "\n".join(output)

    @mcp.tool()
    def unified_search(query: str, limit: int = 15) -> str:
        """
        Search across ALL sources: conversations, GitHub, markdown.
        Returns integrated timeline of thinking on a topic.
        """
        cfg = get_config()
        results = []

        # 1. Conversation embeddings (semantic)
        try:
            embedding = get_embedding(query)
            if embedding and cfg.lance_path.exists():
                lance_results = lance_search(embedding, limit=5)
                for title, content, year, month, sim in lance_results:
                    date = f"{year}-{month:02d}"
                    results.append(("conversation", title or "Untitled", content, date, sim))
        except Exception:
            pass

        # 2. GitHub commits (keyword)
        try:
            gh_db = get_github_db()
            if gh_db and cfg.github_commits_parquet.exists():
                gh_results = gh_db.execute("""
                    SELECT 'github' as source,
                           repo_name || ': ' || LEFT(message, 80) as title,
                           message as content,
                           CAST(timestamp AS VARCHAR) as date,
                           0.4 as score
                    FROM github_commits
                    WHERE message ILIKE ? OR repo_name ILIKE ?
                    ORDER BY timestamp DESC
                    LIMIT 3
                """, [f"%{query}%", f"%{query}%"]).fetchall()
                results.extend(gh_results)
        except Exception:
            pass

        # 3. Markdown docs (keyword)
        try:
            md_db = get_markdown_db()
            if md_db:
                md_results = md_db.execute("""
                    SELECT 'markdown' as source,
                           COALESCE(title, filename) as title,
                           LEFT(content, 500) as content,
                           CAST(modified_at AS VARCHAR) as date,
                           0.45 as score
                    FROM markdown_docs
                    WHERE content ILIKE ? OR title ILIKE ? OR filename ILIKE ?
                    ORDER BY depth_score DESC
                    LIMIT 3
                """, [f"%{query}%", f"%{query}%", f"%{query}%"]).fetchall()
                results.extend(md_results)
        except Exception:
            pass

        if not results:
            return f"No results found across any source for: {query}"

        results.sort(key=lambda x: -x[4])

        output = [f"## Unified Search: \"{query}\"\n"]
        output.append(f"Found {len(results)} results across sources:\n")

        current_source = None
        for source, title, content, date, _ in results[:limit]:
            if source != current_source:
                output.append(f"\n### {source.upper()}")
                current_source = source

            date_str = date[:10] if date else "unknown"
            preview = (content[:150] + "...") if content and len(content) > 150 else (content or "")
            output.append(f"**[{date_str}]** {title}")
            if preview:
                output.append(f"> {preview}")
            output.append("")

        return "\n".join(output)

    @mcp.tool()
    def search_docs(query: str = "", filter: str = None, project: str = None,
                    limit: int = 15, min_depth: int = 70) -> str:
        """
        Search markdown corpus and IP documents with various filters.

        Args:
            query: Search query (keyword for markdown, semantic for IP docs)
            filter: What to search/filter:
                - None (default): Keyword search on markdown corpus
                - "ip": Vector search on curated IP documents
                - "breakthrough": Documents with BREAKTHROUGH energy
                - "deep": High depth-score documents
                - "project": Documents for a specific project
                - "todos": Documents with open TODOs
            project: Project name (used with filter="project" or filter="todos")
            limit: Max results (default 15)
            min_depth: Minimum depth score (used with filter="deep", default 70)
        """
        cfg = get_config()

        if filter == "ip":
            return _search_ip_docs(query, limit)
        elif filter == "breakthrough":
            return _get_breakthrough_docs(limit)
        elif filter == "deep":
            return _get_deep_docs(min_depth, limit)
        elif filter == "project":
            return _get_project_docs(project or query, limit)
        elif filter == "todos":
            return _get_open_todos(project, limit)

        # Default: keyword search
        db = get_markdown_db()
        if not db:
            return "Markdown corpus not found. Ingest markdown docs first."

        pattern = f"%{query}%"
        results = db.execute("""
            SELECT filename, project, voice, energy, depth_score, harvest_score,
                   decision_count, word_count, first_line
            FROM markdown_docs
            WHERE content ILIKE ? OR title ILIKE ? OR filename ILIKE ?
            ORDER BY depth_score DESC, harvest_score DESC
            LIMIT ?
        """, [pattern, pattern, pattern, limit]).fetchall()

        if not results:
            return f"No markdown documents found for '{query}'"

        output = [f"## Markdown Search: '{query}'\n", f"_Found {len(results)} documents_\n"]
        for r in results:
            fname, proj, voice, energy, depth, harvest, decisions, words, preview = r
            output.append(f"**{fname}**")
            output.append(f"  Project: {proj or 'unassigned'} | Voice: {voice} | Energy: {energy}")
            output.append(f"  Depth: {depth} | Harvest: {harvest} | Decisions: {decisions} | Words: {words:,}")
            output.append(f"  > {preview[:100]}...\n")

        return "\n".join(output)

    def _search_ip_docs(query: str, limit: int = 10) -> str:
        """Vector search on curated IP documents."""
        cfg = get_config()
        if not cfg.lance_path.exists():
            return "Vector database not found."

        embedding = get_embedding(query)
        if not embedding:
            return "Could not generate embedding."

        db = get_lance_db()
        if not db:
            return "Could not connect to vector database."

        try:
            tbl = db.open_table("markdown")
            results = tbl.search(embedding).limit(limit).to_pandas()
        except Exception as e:
            return f"Search error: {e}"

        if results.empty:
            return "No IP documents found."

        output = [f"## IP Document Search: '{query}'\n"]
        for i, row in results.iterrows():
            filename = row.get("filename", "Unknown")
            ip_type = row.get("ip_type", "unknown")
            depth = row.get("depth_score", 0)
            energy = row.get("energy", "unknown")
            words = row.get("word_count", 0)
            preview = row.get("content_preview", "")[:300]
            distance = row.get("_distance", 0)

            output.append(f"### {i+1}. {filename}")
            output.append(f"**Type**: {ip_type} | **Depth**: {depth} | **Energy**: {energy} | **Words**: {words:,}")
            output.append(f"**Similarity**: {distance:.4f}")
            output.append(f"> {preview}...\n")

        return "\n".join(output)

    def _get_breakthrough_docs(limit: int = 20) -> str:
        db = get_markdown_db()
        if not db:
            return "Markdown corpus not found."
        results = db.execute("""
            SELECT filename, project, depth_score, harvest_score, decision_count,
                   word_count, first_line
            FROM markdown_docs WHERE energy = 'BREAKTHROUGH'
            ORDER BY depth_score DESC, harvest_score DESC LIMIT ?
        """, [limit]).fetchall()
        if not results:
            return "No breakthrough documents found."
        output = [f"## Breakthrough Documents\n"]
        for fname, proj, depth, harvest, decisions, words, preview in results:
            output.append(f"**{fname}** (depth: {depth}, harvest: {harvest})")
            output.append(f"  Project: {proj or 'unassigned'} | Decisions: {decisions} | Words: {words:,}")
            output.append(f"  > {preview[:120]}...\n")
        return "\n".join(output)

    def _get_deep_docs(min_depth: int = 70, limit: int = 20) -> str:
        db = get_markdown_db()
        if not db:
            return "Markdown corpus not found."
        results = db.execute("""
            SELECT filename, project, voice, energy, depth_score, harvest_score,
                   decision_count, seed_concepts, word_count
            FROM markdown_docs WHERE depth_score >= ?
            ORDER BY depth_score DESC LIMIT ?
        """, [min_depth, limit]).fetchall()
        if not results:
            return f"No documents with depth >= {min_depth}"
        output = [f"## Deep Documents (depth >= {min_depth})\n"]
        for fname, proj, voice, energy, depth, _, decisions, seeds, words in results:
            output.append(f"**{fname}** (depth: {depth})")
            output.append(f"  Project: {proj or 'unassigned'} | Voice: {voice} | Energy: {energy}")
            output.append(f"  Decisions: {decisions} | Words: {words:,}\n")
        return "\n".join(output)

    def _get_project_docs(project: str, limit: int = 20) -> str:
        db = get_markdown_db()
        if not db:
            return "Markdown corpus not found."
        results = db.execute("""
            SELECT filename, voice, energy, depth_score, harvest_score,
                   decision_count, todos_open, word_count, first_line
            FROM markdown_docs WHERE project = ?
            ORDER BY depth_score DESC LIMIT ?
        """, [project.lower(), limit]).fetchall()
        if not results:
            return f"No documents found for project '{project}'"
        output = [f"## Project: {project}\n"]
        for fname, voice, energy, depth, harvest, decisions, todos, _, preview in results:
            output.append(f"**{fname}** (depth: {depth}, harvest: {harvest})")
            output.append(f"  Voice: {voice} | Energy: {energy} | Decisions: {decisions} | TODOs: {todos}")
            output.append(f"  > {preview[:100]}...\n")
        return "\n".join(output)

    def _get_open_todos(project: str = None, limit: int = 20) -> str:
        db = get_markdown_db()
        if not db:
            return "Markdown corpus not found."
        if project:
            results = db.execute("""
                SELECT filename, project, todos_open, todos_done, draft_status, depth_score
                FROM markdown_docs WHERE todos_open > 0 AND project = ?
                ORDER BY todos_open DESC LIMIT ?
            """, [project.lower(), limit]).fetchall()
        else:
            results = db.execute("""
                SELECT filename, project, todos_open, todos_done, draft_status, depth_score
                FROM markdown_docs WHERE todos_open > 5
                ORDER BY todos_open DESC LIMIT ?
            """, [limit]).fetchall()
        if not results:
            return "No documents with open TODOs found."
        output = [f"## Open TODOs{' (' + project + ')' if project else ''}\n"]
        for fname, proj, opens, done, status, depth in results:
            pct = round(100 * done / (opens + done)) if (opens + done) > 0 else 0
            output.append(f"**{fname}** ({opens} open, {done} done = {pct}% complete)")
            output.append(f"  Project: {proj or 'unassigned'} | Status: {status} | Depth: {depth}\n")
        return "\n".join(output)
