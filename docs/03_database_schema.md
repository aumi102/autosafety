# 03 — PostgreSQL Database Schema

## Schema design goals

1. Preserve raw source traceability.
2. Normalize vehicles and components for analytics.
3. Keep app/session/agent run logs separate from domain data.
4. Support read-only analytical SQL generation.
5. Support citation resolution from source records and text chunks.

## Extensions

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
```

## App tables

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'analyst',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chat_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES users(id),
  title TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chat_messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE agent_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id UUID REFERENCES chat_messages(id) ON DELETE SET NULL,
  intent TEXT,
  status TEXT NOT NULL DEFAULT 'created',
  latency_ms INTEGER,
  total_tokens INTEGER,
  warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE TABLE tool_calls (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  tool_name TEXT NOT NULL,
  input_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  latency_ms INTEGER,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Phase 9 execution audit columns

`agent_runs` and `tool_calls` existed from the initial migration but were never
populated. Migration `2025_01_01_0005` adds the safe metadata columns Phase 9
records. Additive only.

```sql
ALTER TABLE agent_runs
  ADD COLUMN conversation_id UUID REFERENCES chat_sessions(id) ON DELETE CASCADE,
  ADD COLUMN turn_id UUID REFERENCES chat_turns(id) ON DELETE CASCADE,
  ADD COLUMN phase TEXT,
  ADD COLUMN surface TEXT,
  ADD COLUMN provider TEXT,
  ADD COLUMN model TEXT,
  ADD COLUMN synthesis_mode TEXT,
  ADD COLUMN started_at TIMESTAMPTZ,
  ADD COLUMN tool_call_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN fallback_used BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN abstained BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN abstention_reason TEXT,
  ADD COLUMN confidence_score INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN confidence_level TEXT,
  ADD COLUMN validation_outcome TEXT,
  ADD COLUMN error_code TEXT;

ALTER TABLE tool_calls
  ADD COLUMN call_id TEXT,
  ADD COLUMN operation TEXT,
  ADD COLUMN started_at TIMESTAMPTZ,
  ADD COLUMN completed_at TIMESTAMPTZ,
  ADD COLUMN success BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN error_code TEXT,
  ADD COLUMN evidence_item_count INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN truncated BOOLEAN NOT NULL DEFAULT false,
  ADD CONSTRAINT uq_tool_call_run_call_id UNIQUE (agent_run_id, call_id);
```

`confidence_score` is the deterministic confidence multiplied by 10000.
`surface` records the entry point (`api_conversation`, `api_guarded`, ...).
`call_id` is application-owned and never LLM-supplied. `operation` holds only
the allowlisted tool operation name.

Audit privacy rules:

```text
never stored: prompts, provider raw requests/responses, API keys, credentials,
              connection strings, raw SQL, raw Cypher, unrestricted tool
              arguments, evidence text, tracebacks
input_json / output_json are deliberately written empty
tool arguments are dropped; only the allowlisted operation name is kept
audit rows are observability only and are never read back into an answer
```

## Source tables

```sql
CREATE TABLE source_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_name TEXT NOT NULL,
  source_url TEXT,
  source_type TEXT NOT NULL,
  file_name TEXT,
  file_sha256 TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  row_count INTEGER DEFAULT 0,
  error_message TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE raw_source_rows (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_run_id UUID NOT NULL REFERENCES source_runs(id) ON DELETE CASCADE,
  source_name TEXT NOT NULL,
  source_record_key TEXT NOT NULL,
  row_number INTEGER,
  raw_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(source_name, source_record_key)
);
```

## Domain tables

```sql
CREATE TABLE vehicles (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  make TEXT NOT NULL,
  model TEXT NOT NULL,
  model_year INTEGER NOT NULL,
  normalized_make TEXT NOT NULL,
  normalized_model TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(normalized_make, normalized_model, model_year)
);

CREATE TABLE components (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  normalized_name TEXT UNIQUE NOT NULL,
  category TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE complaints (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  odi_number TEXT UNIQUE,
  vehicle_id UUID NOT NULL REFERENCES vehicles(id),
  component_id UUID REFERENCES components(id),
  source_run_id UUID REFERENCES source_runs(id),
  source_record_key TEXT NOT NULL,
  received_date DATE,
  incident_date DATE,
  original_component TEXT,
  summary TEXT,
  narrative TEXT,
  crash_flag BOOLEAN DEFAULT false,
  fire_flag BOOLEAN DEFAULT false,
  injury_flag BOOLEAN DEFAULT false,
  death_flag BOOLEAN DEFAULT false,
  source_url TEXT,
  raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE recalls (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_number TEXT NOT NULL,
  component_id UUID REFERENCES components(id),
  source_run_id UUID REFERENCES source_runs(id),
  source_record_key TEXT NOT NULL,
  report_received_date DATE,
  original_component TEXT,
  summary TEXT,
  consequence TEXT,
  remedy TEXT,
  notes TEXT,
  units_affected INTEGER,
  source_url TEXT,
  raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(campaign_number, source_record_key)
);

CREATE TABLE recall_vehicle_links (
  recall_id UUID NOT NULL REFERENCES recalls(id) ON DELETE CASCADE,
  vehicle_id UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
  relation_source TEXT NOT NULL DEFAULT 'source_record',
  PRIMARY KEY (recall_id, vehicle_id)
);

CREATE TABLE investigations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  investigation_number TEXT UNIQUE NOT NULL,
  component_id UUID REFERENCES components(id),
  source_run_id UUID REFERENCES source_runs(id),
  source_record_key TEXT NOT NULL,
  open_date DATE,
  close_date DATE,
  status TEXT,
  investigation_type TEXT,
  original_component TEXT,
  summary TEXT,
  source_url TEXT,
  raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE investigation_vehicle_links (
  investigation_id UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
  vehicle_id UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
  relation_source TEXT NOT NULL DEFAULT 'source_record',
  PRIMARY KEY (investigation_id, vehicle_id)
);

CREATE TABLE manufacturer_communications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  communication_number TEXT NOT NULL,
  component_id UUID REFERENCES components(id),
  source_run_id UUID REFERENCES source_runs(id),
  source_record_key TEXT NOT NULL,
  communication_date DATE,
  communication_type TEXT,
  original_component TEXT,
  summary TEXT,
  source_url TEXT,
  raw_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(communication_number, source_record_key)
);

CREATE TABLE manufacturer_communication_vehicle_links (
  communication_id UUID NOT NULL REFERENCES manufacturer_communications(id) ON DELETE CASCADE,
  vehicle_id UUID NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
  relation_source TEXT NOT NULL DEFAULT 'source_record',
  PRIMARY KEY (communication_id, vehicle_id)
);
```

## Document chunks and citations

```sql
CREATE TABLE document_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type TEXT NOT NULL CHECK (source_type IN ('complaint', 'recall', 'investigation', 'manufacturer_communication')),
  source_id UUID NOT NULL,
  field_name TEXT NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  token_count INTEGER,
  embedding vector(1536),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(source_type, source_id, field_name, chunk_index)
);

CREATE TABLE citations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  agent_run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL,
  source_id UUID NOT NULL,
  field_name TEXT,
  chunk_id UUID REFERENCES document_chunks(id),
  text_span TEXT,
  confidence NUMERIC(4,3),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Analytics indexes

```sql
CREATE INDEX idx_vehicles_make_model_year ON vehicles(normalized_make, normalized_model, model_year);
CREATE INDEX idx_complaints_vehicle_component ON complaints(vehicle_id, component_id);
CREATE INDEX idx_complaints_received_date ON complaints(received_date);
CREATE INDEX idx_complaints_flags ON complaints(crash_flag, fire_flag, injury_flag, death_flag);
CREATE INDEX idx_recalls_campaign ON recalls(campaign_number);
CREATE INDEX idx_recalls_component_date ON recalls(component_id, report_received_date);
CREATE INDEX idx_chunks_source ON document_chunks(source_type, source_id);
```

## Read-only SQL user

The production SQL execution tool must use a database account that can only read selected views.

```sql
CREATE ROLE autosafety_readonly LOGIN PASSWORD 'replace-me';
GRANT CONNECT ON DATABASE autosafety TO autosafety_readonly;
GRANT USAGE ON SCHEMA public TO autosafety_readonly;
GRANT SELECT ON vehicles, components, complaints, recalls, recall_vehicle_links,
  investigations, investigation_vehicle_links,
  manufacturer_communications, manufacturer_communication_vehicle_links
TO autosafety_readonly;
```

## SQL generation views

Create narrow views for Text-to-SQL instead of exposing every internal table.

```sql
CREATE VIEW v_complaints_analytics AS
SELECT
  c.id,
  c.odi_number,
  v.make,
  v.model,
  v.model_year,
  comp.normalized_name AS component,
  c.received_date,
  c.incident_date,
  c.crash_flag,
  c.fire_flag,
  c.injury_flag,
  c.death_flag
FROM complaints c
JOIN vehicles v ON v.id = c.vehicle_id
LEFT JOIN components comp ON comp.id = c.component_id;
```

The SQL agent should primarily see views like `v_complaints_analytics`, not write-oriented internal tables.
