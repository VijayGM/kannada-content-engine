# Kannada Content Engine

Automated daily Kannada Instagram story-video pipeline. This repo is the
implementation of the architecture confirmed in
`Kannada-Reels-Pipeline-Architecture-Assessment.md` — read that first if
you haven't; this README assumes its decisions as given:

- **Path B**: full 5-minute masters, published as regular video posts (not
  the Reels tab — see the assessment doc §0 for why).
- **Zero budget, no GPU, no reliable local machine.** Every provider in this
  repo is a genuinely free tier. Full breakdown in the assessment doc §14.
- **GitHub Actions is the entire compute layer.** Nothing runs on your
  machine or any persistent server — see assessment doc §15.
- **Human Approval Mode is on**, via Telegram.

## How it actually runs

There is no always-on server. Four scheduled GitHub Actions workflows
(`.github/workflows/`) each spin up a fresh container, run a Python script,
and shut down. All state lives in Supabase between runs.

| Workflow file | Runs | What it does |
|---|---|---|
| `01-plan-and-script.yml` | Daily, 03:00 UTC | Picks today's category, generates a concept + Kannada script, scores retention, saves to Supabase with status `pending_review`, notifies you on Telegram |
| `02-check-approval-and-produce.yml` | Every 30 min | Looks for a story with status `approved` (you set this after reviewing the Telegram message — see below). Runs scene breakdown, image generation, voice generation, FFmpeg assembly, QC. Exits cleanly if nothing's approved yet |
| `03-publish.yml` | Every 30 min | Looks for a story with status `produced`. Generates caption/hashtags, publishes to Instagram, notifies you on Telegram |
| `04-analytics.yml` | Daily, evening | Pulls Instagram Insights for recently published stories, writes to `performance_snapshots` |

### The approval step, concretely

1. You get a Telegram message with the day's story concept, hook, and script preview.
2. Open Supabase's Table Editor, find the story row (the message includes its `story_id`), change `status` from `pending_review` to `approved`.
3. Within 30 minutes, `02-check-approval-and-produce.yml` picks it up automatically.

This is intentionally simple for the MVP — a Telegram bot with a reply
handler that flips the status automatically is a reasonable Phase 2
upgrade, not required to ship.

## One-time setup

1. **Supabase project** — create one, then run `database/schema.sql` in the
   SQL editor. Create a public Storage bucket named `content-engine-media`
   (Storage tab → New bucket → toggle Public).
2. **Telegram bot** — message [@BotFather](https://t.me/BotFather), create a
   bot, get the token. Message your new bot once, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat_id`.
3. **Gemini API key** — from [Google AI Studio](https://aistudio.google.com),
   free tier, no card required.
4. **Sarvam API key** — sign up at [sarvam.ai](https://www.sarvam.ai) for the
   ₹1,000 signup credit. See the assessment doc's risk list for the
   estimated runway (~75–200 days at daily 5-min scripts) — plan the
   `indic_tts` fallback in `scripts/lib/tts.py` before it runs out.
5. **Meta App Review** — this is the long-lead-time item. Follow
   `docs/meta-app-review-checklist.md` and start it in parallel with
   everything else, not after.
6. **Add every secret** from `.env.example` to GitHub → Settings → Secrets
   and variables → Actions.

## Editing workflows in n8n

The scripts in `scripts/` run directly as plain Python inside each Actions
job — that's what makes the "no persistent server" hosting model work (see
assessment doc §15). If you'd rather design/edit the logic visually in n8n
before it becomes a script, run n8n temporarily (locally for a session, or
via a free cloud IDE like GitHub Codespaces) — it doesn't need to run
permanently. Exported workflow JSON can live in `n8n/workflows/` for
reference; it's not what actually executes in production.

## Known gaps in this scaffold (by design — see assessment doc for why)

- **Subtitle timing** uses simple per-scene audio sync, not word-level ASR
  alignment yet. The assessment doc (§7 / §9) flags this as the correct
  eventual approach (Sarvam STT or Whisper on the final audio) — not wired
  up in this first pass so the end-to-end pipeline can be validated before
  adding that complexity.
- **`indic_tts` fallback** is stubbed (`NotImplementedError`) in
  `scripts/lib/tts.py` — implement before the Sarvam credit is projected to
  run out.
- **Duplicate-plot detection** in `plan_and_script.py` avoids repeating exact
  hooks but doesn't do embedding-based similarity yet — fine at low volume,
  worth upgrading once you have enough stories for it to matter.
- **Token refresh workflow** for the Instagram long-lived access token isn't
  included yet — add a scheduled Actions workflow for this before the token
  (~60 day lifetime) expires. See `docs/meta-app-review-checklist.md`.

## Repo structure

```
kannada-content-engine/
├── .github/workflows/     # the actual production scheduler + compute
├── database/schema.sql    # run once in Supabase
├── scripts/                # plain Python, run by the Actions workflows
│   └── lib/                 # provider adapters (Gemini, Sarvam, Pollinations, Supabase, Telegram)
├── characters/             # Character Bible templates — populate before production
├── prompts/                 # prompt templates by category, for iterating outside code
├── docs/                    # Meta App Review checklist, etc.
└── n8n/workflows/           # exported n8n JSON, for visual editing reference only
```
