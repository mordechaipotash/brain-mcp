-- Migration 003: SHELET L0-L3 canonical data model for brain-mcp
--
-- Status: OPTIONAL LAYER, off by default
-- Applies if user enables Supabase canonical backend via `brain-mcp setup --supabase`.
-- Without this migration, brain-mcp continues to use local DuckDB/LanceDB as authoritative.
--
-- This migration creates the `brain` schema that mirrors viter-workspace's Migration B
-- (/Users/mordechai/viter-workspace/code/supabase/migrations/_drafts/20260422000000_l0_to_l3_unified_data_model.sql.draft)
-- adapted for brain-mcp's conversation corpus rather than client-engagement artifacts.
--
-- Governing rule (encoded at schema level):
--   L0 is immutable. Each layer is a pure function of the one below.
--   Every L2+ row MUST carry citations JSONB linking to the layer below.

BEGIN;

CREATE SCHEMA IF NOT EXISTS brain;

-- ============================================================================
-- L0: Raw, immutable conversation artifacts
-- ============================================================================
-- Each row is one ingested message. INSERT-only via ingestion daemon.
-- No UPDATE paths. No DELETE paths. Retention is append-only.
-- Sources: claude-code, clawdbot, chatgpt, chatgpt-export, cursor, gemini-cli, generic

CREATE TABLE IF NOT EXISTS brain.l0_artifacts (
  id               BIGSERIAL PRIMARY KEY,
  tenant_id        UUID        NOT NULL,
  owner_user_id    UUID        NOT NULL REFERENCES auth.users(id),

  -- Source provenance
  source           TEXT        NOT NULL,   -- claude-code | clawdbot | chatgpt | ...
  source_path      TEXT,                   -- original file path (for audit only)
  ingested_by      TEXT        NOT NULL,   -- name of ingester agent
  ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Conversation identity
  conversation_id  TEXT        NOT NULL,
  conversation_title TEXT,
  message_id       TEXT        NOT NULL,   -- globally unique within (tenant, source)
  parent_id        TEXT,
  msg_index        INT         NOT NULL,

  -- Content
  role             TEXT        NOT NULL CHECK (role IN ('user','assistant','system','tool')),
  content          TEXT        NOT NULL,
  content_type     TEXT        NOT NULL DEFAULT 'text',

  -- Temporal
  msg_timestamp    TIMESTAMPTZ NOT NULL,
  timestamp_is_fallback BOOLEAN NOT NULL DEFAULT false,
  temporal_precision TEXT NOT NULL DEFAULT 'exact' CHECK (temporal_precision IN ('exact','day','approximate')),

  -- Computed metadata (denormalized for query speed)
  word_count       INT,
  char_count       INT,
  has_code         BOOLEAN,
  has_url          BOOLEAN,
  has_question     BOOLEAN,

  -- Visibility: private (owner only) | tenant (all tenant users) | public (anon read)
  visibility       TEXT NOT NULL DEFAULT 'private'
                   CHECK (visibility IN ('private','tenant','public')),

  UNIQUE (tenant_id, source, message_id)
);

CREATE INDEX idx_l0_tenant_ts      ON brain.l0_artifacts (tenant_id, msg_timestamp DESC);
CREATE INDEX idx_l0_conv           ON brain.l0_artifacts (conversation_id);
CREATE INDEX idx_l0_source         ON brain.l0_artifacts (tenant_id, source);
CREATE INDEX idx_l0_owner_visibility ON brain.l0_artifacts (owner_user_id, visibility);

COMMENT ON TABLE brain.l0_artifacts IS
  'SHELET L0 — immutable raw conversation messages. INSERT-only. Every higher-layer row must cite rows here.';

-- ============================================================================
-- L1: Deterministic extractions (pure function of L0)
-- ============================================================================
-- Same L0 input + same extractor_version → identical L1 output, always.
-- Multiple L1 rows per L0 row are allowed (Yitzchak observation: same L0 → multiple L1s).
-- Distinguished by (extraction_type, extractor_version).

CREATE TABLE IF NOT EXISTS brain.l1_extractions (
  id               BIGSERIAL PRIMARY KEY,
  tenant_id        UUID        NOT NULL,
  owner_user_id    UUID        NOT NULL REFERENCES auth.users(id),

  -- Source link (back to L0)
  l0_id            BIGINT      NOT NULL REFERENCES brain.l0_artifacts(id),

  -- Extraction identity
  extraction_type  TEXT        NOT NULL   -- 'embedding' | 'keyword-index' | 'entity' | ...
                   CHECK (extraction_type IN ('embedding','keyword-index','entity','noise-filter')),
  extractor_version TEXT       NOT NULL,

  -- Extraction payload
  embedding        vector(768),            -- NULL unless extraction_type = 'embedding'
  payload          JSONB       NOT NULL DEFAULT '{}'::jsonb,

  -- Provenance
  extracted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  extractor_agent  TEXT        NOT NULL,

  visibility       TEXT NOT NULL DEFAULT 'private'
                   CHECK (visibility IN ('private','tenant','public')),

  UNIQUE (l0_id, extraction_type, extractor_version)
);

CREATE INDEX idx_l1_tenant         ON brain.l1_extractions (tenant_id);
CREATE INDEX idx_l1_l0             ON brain.l1_extractions (l0_id);
CREATE INDEX idx_l1_type_version   ON brain.l1_extractions (extraction_type, extractor_version);

-- pgvector HNSW index for semantic search (only rows where embedding IS NOT NULL)
CREATE INDEX idx_l1_embedding_hnsw
  ON brain.l1_extractions
  USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

COMMENT ON TABLE brain.l1_extractions IS
  'SHELET L1 — deterministic extractions. Pure function of L0. Embeddings, keyword indexes, entity pulls.';

-- ============================================================================
-- L2: Synthesis (LLM-generated, temporal, cited)
-- ============================================================================
-- Each row is a synthesized view over one or more L1 extractions.
-- Citations JSONB is REQUIRED and must contain l1_ids[] or l0_ids[] pointing below.

CREATE TABLE IF NOT EXISTS brain.l2_syntheses (
  id               BIGSERIAL PRIMARY KEY,
  tenant_id        UUID        NOT NULL,
  owner_user_id    UUID        NOT NULL REFERENCES auth.users(id),

  -- Synthesis identity
  synthesis_type   TEXT        NOT NULL   -- 'enhanced-extraction-v5' | 'tunnel-state-cache' | 'trajectory-cache'
                   CHECK (synthesis_type IN (
                     'enhanced-extraction-v5',
                     'tunnel-state-cache',
                     'context-recovery-cache',
                     'thinking-trajectory-cache',
                     'what-do-i-think-cache'
                   )),
  synthesizer_version TEXT     NOT NULL,

  -- Conversation anchor (for extraction-v5 only; null for tool-output caches)
  conversation_id  TEXT,

  -- The structured summary (enhanced-extraction-v5 schema)
  title            TEXT,
  summary          TEXT,
  domain_primary   TEXT,
  domain_secondary TEXT,
  thinking_stage   TEXT  CHECK (thinking_stage IN ('exploring','crystallizing','refining','executing') OR thinking_stage IS NULL),
  importance       TEXT  CHECK (importance IN ('breakthrough','significant','routine') OR importance IS NULL),
  emotional_tone   TEXT,
  cognitive_pattern TEXT,
  resurface_when   TEXT,
  quotable         TEXT,

  -- Structured fanout payload (concepts, edges, decisions, open_questions, assets, corrections, temporal_facts)
  payload          JSONB       NOT NULL DEFAULT '{}'::jsonb,

  -- CITATIONS — REQUIRED (CHECK constraint enforces non-empty)
  citations        JSONB       NOT NULL
                   CHECK (
                     (citations ? 'l0_ids' AND jsonb_array_length(citations->'l0_ids') > 0)
                     OR
                     (citations ? 'l1_ids' AND jsonb_array_length(citations->'l1_ids') > 0)
                   ),

  synthesized_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  synthesizer_model TEXT,          -- e.g., 'google/gemini-2.5-flash-lite'
  synthesizer_cost_usd NUMERIC(10,6),

  visibility       TEXT NOT NULL DEFAULT 'private'
                   CHECK (visibility IN ('private','tenant','public')),

  UNIQUE (tenant_id, synthesis_type, synthesizer_version, conversation_id)
);

CREATE INDEX idx_l2_tenant_domain  ON brain.l2_syntheses (tenant_id, domain_primary);
CREATE INDEX idx_l2_importance     ON brain.l2_syntheses (tenant_id, importance) WHERE importance IS NOT NULL;
CREATE INDEX idx_l2_stage          ON brain.l2_syntheses (tenant_id, thinking_stage) WHERE thinking_stage IS NOT NULL;
CREATE INDEX idx_l2_conv           ON brain.l2_syntheses (conversation_id);
CREATE INDEX idx_l2_citations_gin  ON brain.l2_syntheses USING gin (citations);
CREATE INDEX idx_l2_payload_gin    ON brain.l2_syntheses USING gin (payload);

COMMENT ON TABLE brain.l2_syntheses IS
  'SHELET L2 — LLM synthesis with REQUIRED citations. Every claim traces back to L1 or L0.';

-- ============================================================================
-- L3: Fusion (route-to-right-person/time/format)
-- ============================================================================
-- Not compress-further — COMPOSE. Each row is a rendered "surface" pointing
-- to the L2 rows it fuses. Cheap to regenerate; expensive to interpret.

CREATE TABLE IF NOT EXISTS brain.l3_fusions (
  id               BIGSERIAL PRIMARY KEY,
  tenant_id        UUID        NOT NULL,
  owner_user_id    UUID        NOT NULL REFERENCES auth.users(id),

  fusion_type      TEXT        NOT NULL
                   CHECK (fusion_type IN (
                     'dormant-contexts',
                     'open-threads',
                     'switching-cost',
                     'alignment-check',
                     'tunnel-history'
                   )),
  fuser_version    TEXT        NOT NULL,

  -- The rendered fusion output (Markdown)
  rendered_md      TEXT        NOT NULL,

  -- Structured form (optional, for downstream consumers)
  payload          JSONB       NOT NULL DEFAULT '{}'::jsonb,

  -- CITATIONS — REQUIRED, must include l2_ids[]
  citations        JSONB       NOT NULL
                   CHECK (citations ? 'l2_ids' AND jsonb_array_length(citations->'l2_ids') > 0),

  -- Fusion parameters (e.g., {domain: "...", min_importance: "significant"})
  params           JSONB       NOT NULL DEFAULT '{}'::jsonb,

  fused_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  ttl_until        TIMESTAMPTZ,   -- fusions are regenerable; TTL marks cache staleness

  visibility       TEXT NOT NULL DEFAULT 'private'
                   CHECK (visibility IN ('private','tenant','public'))
);

CREATE INDEX idx_l3_tenant_type    ON brain.l3_fusions (tenant_id, fusion_type);
CREATE INDEX idx_l3_citations_gin  ON brain.l3_fusions USING gin (citations);
CREATE INDEX idx_l3_ttl            ON brain.l3_fusions (ttl_until) WHERE ttl_until IS NOT NULL;

COMMENT ON TABLE brain.l3_fusions IS
  'SHELET L3 — fusion/composition. Route L2 syntheses to the right surface. Regenerable, cacheable.';

-- ============================================================================
-- Row-Level Security: layer-bounded permission gates
-- ============================================================================

ALTER TABLE brain.l0_artifacts   ENABLE ROW LEVEL SECURITY;
ALTER TABLE brain.l1_extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE brain.l2_syntheses   ENABLE ROW LEVEL SECURITY;
ALTER TABLE brain.l3_fusions     ENABLE ROW LEVEL SECURITY;

-- L0: owner-write (via daemon/service-role), visibility-controlled read.
-- No UPDATE policy => rows are immutable at the RLS layer.
CREATE POLICY l0_read ON brain.l0_artifacts
  FOR SELECT TO authenticated
  USING (
    owner_user_id = auth.uid()
    OR visibility = 'tenant' AND tenant_id IN (
      SELECT tenant_id FROM brain.tenant_members WHERE user_id = auth.uid()
    )
    OR visibility = 'public'
  );

CREATE POLICY l0_insert_service ON brain.l0_artifacts
  FOR INSERT TO service_role
  WITH CHECK (true);

-- L1: extractor-write (service-role only), read mirrors L0 visibility rules.
CREATE POLICY l1_read ON brain.l1_extractions
  FOR SELECT TO authenticated
  USING (
    owner_user_id = auth.uid()
    OR visibility = 'tenant' AND tenant_id IN (
      SELECT tenant_id FROM brain.tenant_members WHERE user_id = auth.uid()
    )
    OR visibility = 'public'
  );

CREATE POLICY l1_insert_service ON brain.l1_extractions
  FOR INSERT TO service_role
  WITH CHECK (true);

-- L2: owner-write (owner can trigger their own syntheses), visibility-gated read.
CREATE POLICY l2_read ON brain.l2_syntheses
  FOR SELECT TO authenticated
  USING (
    owner_user_id = auth.uid()
    OR visibility = 'tenant' AND tenant_id IN (
      SELECT tenant_id FROM brain.tenant_members WHERE user_id = auth.uid()
    )
    OR visibility = 'public'
  );

CREATE POLICY l2_write_owner ON brain.l2_syntheses
  FOR INSERT TO authenticated
  WITH CHECK (owner_user_id = auth.uid());

-- L3: admin/owner-write only. Read is tenant-scoped for shared fusions.
CREATE POLICY l3_read ON brain.l3_fusions
  FOR SELECT TO authenticated
  USING (
    owner_user_id = auth.uid()
    OR visibility = 'tenant' AND tenant_id IN (
      SELECT tenant_id FROM brain.tenant_members WHERE user_id = auth.uid()
    )
  );

CREATE POLICY l3_write_owner_or_admin ON brain.l3_fusions
  FOR INSERT TO authenticated
  WITH CHECK (
    owner_user_id = auth.uid()
    OR EXISTS (
      SELECT 1 FROM brain.tenant_members
      WHERE user_id = auth.uid() AND tenant_id = brain.l3_fusions.tenant_id
        AND role IN ('admin','owner')
    )
  );

-- ============================================================================
-- Tenant membership (minimal — full tenant model is out of scope for this migration)
-- ============================================================================

CREATE TABLE IF NOT EXISTS brain.tenant_members (
  tenant_id UUID NOT NULL,
  user_id   UUID NOT NULL REFERENCES auth.users(id),
  role      TEXT NOT NULL DEFAULT 'member'
            CHECK (role IN ('owner','admin','member','viewer')),
  joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, user_id)
);

ALTER TABLE brain.tenant_members ENABLE ROW LEVEL SECURITY;

CREATE POLICY tm_self_read ON brain.tenant_members
  FOR SELECT TO authenticated
  USING (user_id = auth.uid());

-- ============================================================================
-- Helper: citation-chain resolver
-- ============================================================================
-- Given an L3 row, walk down through L2 → L1 → L0 and return every source message.

CREATE OR REPLACE FUNCTION brain.resolve_citations(l3_id BIGINT)
RETURNS TABLE (
  layer        TEXT,
  row_id       BIGINT,
  preview      TEXT,
  msg_timestamp TIMESTAMPTZ
) LANGUAGE sql STABLE AS $$
  WITH RECURSIVE chain AS (
    SELECT 'L3'::TEXT AS layer, id AS row_id, NULL::TEXT AS preview, fused_at AS msg_timestamp,
           citations->'l2_ids' AS next_ids
    FROM brain.l3_fusions WHERE id = l3_id

    UNION ALL

    SELECT 'L2'::TEXT, l2.id, LEFT(l2.summary, 200), l2.synthesized_at,
           l2.citations->'l1_ids'
    FROM brain.l2_syntheses l2, chain c
    WHERE c.layer = 'L3' AND l2.id::TEXT IN (
      SELECT jsonb_array_elements_text(c.next_ids)
    )

    UNION ALL

    SELECT 'L1'::TEXT, l1.id, NULL, l1.extracted_at, jsonb_build_array(l1.l0_id)
    FROM brain.l1_extractions l1, chain c
    WHERE c.layer = 'L2' AND l1.id::TEXT IN (
      SELECT jsonb_array_elements_text(c.next_ids)
    )

    UNION ALL

    SELECT 'L0'::TEXT, l0.id, LEFT(l0.content, 200), l0.msg_timestamp, NULL
    FROM brain.l0_artifacts l0, chain c
    WHERE c.layer = 'L1' AND l0.id = (c.next_ids->>0)::BIGINT
  )
  SELECT layer, row_id, preview, msg_timestamp FROM chain;
$$;

COMMENT ON FUNCTION brain.resolve_citations IS
  'Walk citation chain from L3 → L2 → L1 → L0. Every claim in brain-mcp is traceable via this function.';

COMMIT;
