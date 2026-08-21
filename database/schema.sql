-- Kannada Content Engine — Supabase schema
-- Run this in the Supabase SQL editor once, on a fresh project.
-- This is the single source of truth for pipeline state across ephemeral
-- GitHub Actions runs (see docs/architecture assessment §15).

create extension if not exists "pgcrypto"; -- for gen_random_uuid()

-- ── Characters ──────────────────────────────────────────────────────────
create table characters (
  character_id      uuid primary key default gen_random_uuid(),
  name              text not null,
  age               text,
  gender            text,
  appearance        text,        -- feeds the Flux reference-image prompt
  clothing          text,
  personality       text,
  voice_style       text,        -- maps to a Sarvam speaker id
  speech_style      text,
  relationships     jsonb,       -- {"character_id": "relationship description"}
  reference_image_url text,      -- canonical Flux-generated reference sheet
  seed              integer,     -- locked seed for consistency across scenes
  prompt_template   text,        -- reusable text block appended to every scene prompt
  created_at        timestamptz default now()
);

-- ── Stories ─────────────────────────────────────────────────────────────
create table stories (
  story_id          uuid primary key default gen_random_uuid(),
  category          text not null,
  title             text,
  hook              text,
  script            jsonb,       -- { "opening": "...", "body_beats": [...], "ending": "..." }
  character_ids     uuid[] default '{}',
  retention_score   numeric,
  status            text not null default 'concept_pending',
  -- lifecycle: concept_pending -> concept_approved -> script_ready ->
  --            pending_review -> approved -> in_production ->
  --            qc_failed -> produced -> published -> error
  final_video_url   text,
  caption           text,
  hashtags          text[],
  instagram_media_id text,
  instagram_url     text,
  creation_date     timestamptz default now(),
  publication_date  timestamptz,
  -- performance snapshot (latest; full history in performance_snapshots)
  views             integer,
  likes             integer,
  comments          integer,
  shares            integer,
  saves             integer,
  watch_time_seconds integer,
  completion_rate   numeric,
  performance_score numeric,
  updated_at        timestamptz default now()
);

create index idx_stories_status on stories(status);
create index idx_stories_category on stories(category);

-- ── Scenes ──────────────────────────────────────────────────────────────
create table scenes (
  scene_id          uuid primary key default gen_random_uuid(),
  story_id          uuid references stories(story_id) on delete cascade,
  scene_number      integer not null,
  description       text,
  visual_prompt     text,
  character_ids     uuid[] default '{}',
  image_url         text,        -- Kontext-edited scene image, hosted in Supabase Storage
  audio_url         text,        -- TTS output for this scene's dialogue/narration
  duration_seconds  numeric,
  status            text default 'pending', -- pending, image_ready, audio_ready, assembled, failed
  created_at        timestamptz default now()
);

create index idx_scenes_story on scenes(story_id);

-- ── Assets (generic media + cost ledger per generated artifact) ──────────
create table assets (
  asset_id          uuid primary key default gen_random_uuid(),
  story_id          uuid references stories(story_id) on delete cascade,
  scene_id          uuid references scenes(scene_id) on delete set null,
  asset_type        text not null, -- reference_image, scene_image, tts_audio, subtitle, final_video
  provider          text not null, -- pollinations, sarvam, ffmpeg, gemini
  url               text,
  cost_inr          numeric default 0,
  meta              jsonb,
  created_at        timestamptz default now()
);

-- ── Publications (kept separate from stories for a clean publish audit trail) ──
create table publications (
  publication_id    uuid primary key default gen_random_uuid(),
  story_id          uuid references stories(story_id) on delete cascade,
  instagram_media_id text,
  instagram_url     text,
  container_id      text,        -- IG media container id, useful while debugging a stuck publish
  published_at      timestamptz,
  status            text default 'container_created', -- container_created, finished, published, error
  created_at        timestamptz default now()
);

-- ── Performance snapshots (time series — views/engagement change post-publish) ──
create table performance_snapshots (
  snapshot_id       uuid primary key default gen_random_uuid(),
  story_id          uuid references stories(story_id) on delete cascade,
  captured_at       timestamptz default now(),
  views             integer,
  likes             integer,
  comments          integer,
  shares            integer,
  saves             integer,
  watch_time_seconds integer,
  completion_rate   numeric
);

create index idx_perf_story on performance_snapshots(story_id);

-- ── Errors / retries (the dead-letter pattern from the architecture doc §17/§8) ──
create table errors_and_retries (
  error_id          uuid primary key default gen_random_uuid(),
  story_id          uuid references stories(story_id) on delete set null,
  scene_id          uuid references scenes(scene_id) on delete set null,
  workflow_step     text not null, -- e.g. 'scene_image_generation', 'tts', 'publish'
  error_message     text,
  retry_count       integer default 0,
  resolved          boolean default false,
  created_at        timestamptz default now()
);

create index idx_errors_unresolved on errors_and_retries(resolved) where resolved = false;

-- ── API cost tracking (budget gate from architecture doc §8/§18) ─────────
create table api_costs (
  cost_id           uuid primary key default gen_random_uuid(),
  story_id          uuid references stories(story_id) on delete set null,
  provider          text not null,
  operation         text not null,
  units             numeric,     -- characters, images, seconds — whatever the provider bills on
  cost_inr          numeric not null default 0,
  succeeded         boolean default true,
  created_at        timestamptz default now()
);

create index idx_costs_created on api_costs(created_at);

-- ── Convenience view: current-month spend, for the budget-gate check ─────
create view monthly_spend as
  select date_trunc('month', created_at) as month, sum(cost_inr) as total_inr
  from api_costs
  group by 1;
