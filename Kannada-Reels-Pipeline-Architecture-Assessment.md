# Kannada Instagram Reels Automation — Feasibility & Architecture Assessment

**Status:** Assessment only, per your brief. Nothing built or scaffolded yet.
**Prepared as:** Architecture review before Phase 1 implementation.

**Confirmed direction (as of this revision):**
- **Path B** — full 5-minute master videos, published as regular Instagram video posts (not the Reels tab). See §0.
- **Zero-budget constraint** — no paid subscriptions on any AI tool. This forced a real architecture change, not just a cheaper vendor swap: the video-generation layer (originally Kling/Veo/Runway, §3 and §6) is replaced with an **image-based animatic** approach — AI-generated still images animated via FFmpeg pan/zoom/crossfade — because free tiers on all major AI video generators are watermarked, capped at a handful of 5-second clips/day, and licensed for non-commercial use only, which makes them unusable for a business Instagram account at any volume. See §14 for the full zero-budget stack.
- **No GPU access confirmed** — self-hosted open-source video/image models (Wan 2.2, Stable Diffusion locally) are off the table. The image layer instead uses Pollinations.ai's free, unlimited, no-signup Flux endpoint, called over HTTP like any other API — no local compute required.
- **No local or persistent-server dependency (confirmed this revision)** — the entire pipeline runs on **GitHub Actions** (public repo, unlimited free Linux minutes, no card required) as a scheduled, ephemeral compute layer. n8n workflows are built visually as usual but executed headlessly via n8n's CLI inside each scheduled Actions run, not hosted on an always-on server. All state lives in Supabase so nothing is lost between ephemeral runs. See §15.
- **Human Approval Mode: ON for MVP**, with script review explicitly checking Kannada-English code-mixing naturalness (see §14), not just grammar/safety — this is the specific quality risk that automated scoring is least equipped to catch.

---

## 0. The One Finding That Reshapes the Spec — Resolved: Path B Confirmed

Your brief asks for Reels **up to 5 minutes**. As of mid-2026, the Instagram Graph API (the only officially supported publishing path) accepts video uploads up to 15 minutes, but **only clips between 5 and 90 seconds are eligible to actually appear in the Reels tab and get Reels-style distribution**. Anything longer than 90 seconds published through the API lands as a plain video post — different feed placement, different algorithm treatment, no Reels reach.

The native Instagram app now lets creators post longer Reels (up to 3 minutes, sometimes more), but **that's an app-only capability, not something the Graph API exposes**. There is no officially supported way to programmatically publish a 5-minute video as a Reel.

**Decision: Path B.** Build for full 5-minute masters, publish as regular video posts via the standard media-container endpoint (not the Reels-specific flow). Confirmed implications:

- Publishing workflow targets the standard video-post container, not Reels-tab eligibility rules — simpler validation, but no Reels-tab discovery boost.
- Distribution leans more on captions, hashtags, and follower base than Instagram's short-form discovery engine, since regular video posts don't get Reels-style algorithmic push. Factor this into the growth expectations in §22.
- The Master Story → 30/60/90s cascade from your original §10 stays on the roadmap as a Phase 6/7 add-on: auto-clipping short highlight Reels from the 5-min master later, to also pick up Reels-tab reach alongside the long-form post, once the core pipeline is proven.

---

## 1. Recommended High-Level Architecture

```
                     ┌─────────────────────┐
                     │   n8n Orchestrator    │  (4 linked workflows, not 1 monolith)
                     └─────────────────────┘
                                │
        ┌───────────┬──────────┼──────────┬────────────┐
        ▼           ▼          ▼          ▼            ▼
   Content DB   LLM Provider  TTS       Image/Video   Instagram
  (Postgres/   Abstraction  Provider    Provider      Graph API
   Supabase)   (Claude/GPT) (Sarvam/    Abstraction
                             ElevenLabs) (Kling/Veo/
                                          Runway)
```

Each provider category sits behind a thin **n8n sub-workflow acting as an adapter** (Execute Workflow node pattern) — not hard-coded HTTP nodes scattered across the main flow. That's what gives you the "swap providers without rebuilding" property you asked for in Section 6. Concretely: one "Generate Voice" sub-workflow with a `provider` parameter that switches between Sarvam/ElevenLabs/Google internally; callers never know which vendor served the request.

---

## 2. n8n Workflow Breakdown (elaborating your Section 11)

### Workflow 1 — Content Planner (daily trigger)
Schedule Trigger → Read Content Calendar (Airtable/Postgres) → Select Category (weighted rotation + performance-based override) → Fetch last N stories in category (dedupe check) → LLM: generate 3 story concepts → Similarity check (embedding cosine-sim against past hooks/plots) → Auto-select best concept OR queue for human approval (config flag) → Write `story_id` row with status `concept_approved`.

### Workflow 2 — Script Generator
Triggered by Workflow 1's output → Character Bible lookup (if recurring characters) → LLM: full Kannada script generation (structured JSON: hook / body beats / ending) → LLM: Retention Score pass (self-critique prompt scoring hook strength, pacing, payoff) → If score < threshold: regenerate (max 2 retries) → Script QA (profanity/safety check, language-quality check) → Save final script, status `script_ready`.

### Workflow 3 — Production
Scene Breakdown (LLM splits script into shot list with visual prompts, using Character Bible for consistent prompts) → Image/Video Generation (provider adapter) → Voice Generation (provider adapter, per-character) → Duration sync (trim/pad video clips to match VO length) → Music/SFX overlay → Subtitle generation (from final audio via ASR/forced-alignment, not from source script — this matters, see §9) → Video Assembly (Remotion / FFmpeg render node or external render API) → status `assembled`.

### Workflow 4 — Quality Control
Automated checks: duration, resolution, aspect ratio, audio levels, subtitle sync offset, missing-scene detection, file integrity, basic content-safety classifier pass. Fail → identify failed stage → re-invoke only that sub-workflow (not the whole pipeline) → re-run QC, max 2 automatic retries → else route to Error Queue + notify.

### Workflow 5 — Publishing (I'd split this out of "Production" as its own workflow, since it has a different trigger/cadence and different failure modes)
Caption + hashtag generation → cover image selection → create Graph API media container → poll container status (`IN_PROGRESS` → `FINISHED`) → publish → store IG media ID + permalink → schedule next status poll for analytics.

### Workflow 6 — Analytics & Learning Loop
Scheduled (e.g. daily, 24–48h post-publish) → pull insights via Graph API → write to performance table → aggregate by category/hook-type/length → feed summarized "what worked" back into Workflow 1's concept-generation prompt as context.

Six modular workflows, not one — matches your instinct in Section 11, just split slightly further (Publishing and Analytics separated from Production/Planner) because they run on different schedules and need independent retry/error handling.

---

## 3. Required Services / APIs (superseded by §14 — Zero-Budget Stack)

The table below was the original paid-tier recommendation. Given the confirmed zero-budget constraint, jump to §14 for the actual provider set this build uses. Kept here for reference on what each layer looks like *if* budget ever opens up later.

| Layer | Recommended primary (paid) | Backup/alt | Why |
|---|---|---|---|
| LLM (script + reasoning) | Claude or GPT via API | Gemini | Kannada quality varies by model — test prompts against all three before committing; none is uniformly best for regional-language creative writing |
| Kannada TTS | **Sarvam AI (Bulbul v3)** | ElevenLabs | Sarvam is purpose-built for Indian languages, natively handles Kannada-English code-mixing (common in your "everyday life"/comedy categories), and is meaningfully cheaper than ElevenLabs at scale |
| Video generation | **Kling 3.0** for cost + character/multi-angle consistency | Veo 3.1 for native audio-sync scenes, Runway for hardest-to-generate shots | Kling currently leads on cost-per-second and subject consistency across shots. Veo 3.1 is the only major model with native synced dialogue audio, at ~5x the cost |
| Subtitle/ASR | Whisper (self-hosted or API) or Sarvam STT | Google STT | Needed to time subtitles off final audio, not source script |
| Storage | S3-compatible object storage (Cloudflare R2 or AWS S3) | — | Video assets are large; R2 has no egress fees |
| Publishing | Instagram Graph API (official) | — | Do not build on scraping/unofficial automation per your own §12 |
| Orchestration | n8n (self-hosted, Docker) | — | Per your brief |
| Database | Supabase (Postgres) | Airtable for early MVP only | See §16 below |

**Meta app requirements you'll need before any publishing works at all:** Instagram **Business** account (not Creator — Creator accounts can't publish via API), linked Facebook Page, a Meta Developer app, and App Review approval for `instagram_business_content_publish` (this replaced older deprecated scopes). App Review typically takes 2–4 weeks and requires a screencast of the full flow — budget this into your timeline now, it's usually the longest lead-time item in the whole project, longer than any of the AI-provider integration work.

---

## 4. Zero-Budget Component Choices (was "Free vs Paid" — now confirmed as the actual build target)

Superseded by the full breakdown in §14. Summary: everything in this build runs on genuinely free tiers or self-hosted free tools — no video-generation API cost, since AI video generation is removed from the architecture entirely (see §0 and §14 for why and what replaces it).

---

## 5. Recommended Database — Supabase (Postgres)

Weighing your Section 16 options against your actual needs:

- **Google Sheets / Airtable:** fine for a 2-week prototype, wrong choice past that. No relational integrity between stories/characters/performance, row-limit and API-rate friction at daily-video volume once you're tracking per-scene assets and retry logs, and n8n's Sheets/Airtable nodes aren't built for the write frequency a QC-retry loop generates.
- **Notion:** good for the human-approval/editorial side (reviewing scripts, browsing content calendar) but a poor system-of-record for the pipeline itself — not built for relational queries like "top-performing hook types by category over 90 days."
- **PostgreSQL (self-hosted):** technically ideal, but you're adding ops burden (backups, migrations, uptime) to a solo/small-team project.
- **Supabase:** Postgres underneath (full relational integrity for your Story/Character/Performance schema), generous free tier, built-in REST/API layer n8n's Postgres or HTTP node can hit directly, and you get a UI for eyeballing data without writing SQL. This is the right trade-off for reliability + low ops overhead + n8n integration.

Recommendation: **Supabase now**, with Notion optionally layered on top purely as a human-review surface if you keep Human Approval Mode on.

Core tables: `stories`, `characters`, `scenes`, `assets` (generated media with provider/cost metadata), `publications` (IG media ID, URL, publish timestamp), `performance_snapshots` (time-series, since views/engagement change post-publish), `errors_and_retries`, `api_costs`.

---

## 6. Character Consistency — Deep-Dive: the Kontext Workflow (confirmed approach)

You flagged character-consistency drift as a challenge you've already run into, so this deserved real research rather than a general caveat. The fix is a specific technique, not just a style choice — style is the second half of the answer, not the whole thing.

**The technique: two-stage generation via Flux Kontext.** Pollinations.ai (your free image provider, §14) offers a model called `kontext`, purpose-built for exactly this problem: it takes one reference image plus a text instruction and edits the scene — pose, background, lighting, action — while preserving the subject's face, clothing, and identity. This is meaningfully closer to the reference-image conditioning paid video tools (Kling, Veo) use than generating each scene independently from text alone, and it's free on the registered ("Seed") tier, roughly one request per 5 seconds.

Workflow 3's scene-image generation step should run as:
1. **Generate one canonical reference image per character**, once, from the detailed Character Bible text prompt (Flux, text-to-image). Store its URL against the character record.
2. **For every scene involving that character**, call `kontext` with that reference image URL + a scene-specific instruction ("same character, now standing in a village market, worried expression, midday light") — not a fresh text-to-image call. This is what actually holds identity across a 5-minute, 20–40-image story.

**Two supporting techniques, both free, both worth building into the prompt templates from day one:**
- **Lock a consistent `seed` parameter** per character across all their scene generations — adds a further layer of stability on top of Kontext's own consistency.
- **Multi-character scenes** (two people in frame together) need a different approach, since Kontext takes one reference image: either composite the two characters' reference images into a single input image first, or use a multi-image-reference model also free on Pollinations (`nanobanana` or `seedream`, which accept multiple reference images per call).

**Style choice is the second lever, and it compounds with the technique above rather than replacing it.** A flat 2D illustrated / stylized look is meaningfully more forgiving of whatever residual drift Kontext doesn't fully eliminate — small variance reads as natural stylistic looseness in illustration, but reads as "different person" in a semi-realistic style, where viewers' face-recognition instincts are much less forgiving. Recommendation: pair the Kontext workflow above with an illustrated/flat-color visual style, not one or the other. This combination is the actual answer to the drift problem you've already run into — a style choice alone, without the Kontext reference-editing technique, would still show drift; the technique alone, without a forgiving style, would still show it more than necessary.

**Known caveat to build retry logic around:** Pollinations' `kontext` endpoint has had intermittent bugs reported (occasional "image is required" errors even when the image parameter is correctly sent) — worth wrapping this specific call in retry-with-backoff in your n8n/Actions workflow rather than assuming a clean success every time, same principle as the "no SLA" caveat already noted for the image layer generally.

Keep the `reference_image_url` field in the Character Bible schema (as previously planned) — it's now load-bearing for this exact workflow, not just a future upgrade hook.

---

## 7. Subtitles — One Correction to Your Spec

Section 9 is right to generate subtitles from the final audio track rather than the source script, and it's worth being explicit about why: TTS output timing never matches script line-breaks exactly (pauses, emphasis, code-mixed pronunciation all shift timing). Practically this means Workflow 3 needs an ASR/forced-alignment step (Whisper with word-level timestamps, or Sarvam STT) *after* voice generation and *after* any audio-video duration sync — not before.

---

## 8. Error Handling & Cost Control Architecture

Both map cleanly onto n8n's native capabilities:

- **Retry/error:** n8n's built-in node-level retry (with backoff) covers transient API failures. For your Section 17 dead-letter pattern, add an explicit `error_queue` table + a Slack/email notification node — n8n doesn't have dead-letter queuing natively, so this needs to be modeled in your data layer, not just workflow logic.
- **Cost tracking:** Every provider-adapter sub-workflow should log `{story_id, provider, operation, tokens_or_seconds, cost_usd}` to the `api_costs` table on every call, success or failure (failed generations still cost money and your brief explicitly wants that tracked). A daily scheduled workflow sums cost against your configured monthly budget and halts the Content Planner trigger if exceeded — straightforward with an n8n IF node gating the schedule trigger's downstream path.

---

## 9. Security

Standard and fully achievable in n8n: use n8n's built-in credentials store (encrypted at rest) for every API key — Instagram token, LLM keys, TTS/video-gen keys, Supabase service key. Never in workflow JSON, never in `.env` committed to Git. `.env.example` with placeholder keys only, `.env` and `n8n/credentials/` in `.gitignore` from commit one. Instagram long-lived tokens expire (~60 days) and need a refresh workflow — build this in from the start, it's a common silent-failure point in IG automation projects.

---

## 14. Zero-Budget Stack (confirmed build target)

No GPU access, no paid AI subscriptions. Here's what each layer runs on and what breaks if usage grows:

| Layer | Tool | Cost model | Ceiling / caveat |
|---|---|---|---|
| LLM | **Gemini API free tier** (Flash / Flash-Lite via Google AI Studio) | Free, ongoing, no card, no expiry | Rate-limited (RPM/RPD caps) — comfortably enough for 1 video/day of script gen + QA + retention scoring; don't test against Pro tier and assume Flash matches it |
| Kannada TTS (primary) | **Sarvam AI** (Bulbul v3) | ₹1,000 one-time signup credit | Not renewing — will exhaust in weeks-to-months depending on script length. Native Kannada-English code-mixing support is the reason to use it while the credit lasts |
| Kannada TTS (sustainable fallback) | **AI4Bharat Indic-TTS** (self-hosted) or Google Cloud TTS free monthly quota | Self-hosted: zero cost, no card. Google Cloud: 4M chars/month free, renews monthly, but requires enabling billing (card on file, even though uncharged under quota) | Indic-TTS: lower voice naturalness, more setup effort, runs fine on CPU. Google Cloud: only take this route if you're comfortable adding a card even with no charges expected |
| Visual generation | **Pollinations.ai** — `flux` (text-to-image, character reference sheets) + `kontext` (image-to-image, per-scene consistency edits) | Free, no signup for basic use, free registration for higher limits/no watermark | No SLA/uptime guarantee — build retry/backoff, especially around `kontext`, which has had intermittent bugs reported. See §6 for the full consistency workflow |
| Video assembly | **FFmpeg**, self-hosted | Free, open source | CPU-only — a 5-minute assembly (pan/zoom on ~30 images + subtitle burn-in + audio mux) will take real wall-clock time on modest hardware; budget for this in your scheduling, don't assume near-instant render |
| Subtitle timing/ASR | Sarvam STT credit (shared pool with TTS credit) or self-hosted small Whisper model on CPU | Same caveats as TTS above | Small Whisper models (base/small) run on CPU but slower than GPU — acceptable for a once-daily batch job |
| Storage | Local disk or a free-tier object storage bucket (Cloudflare R2 has a genuinely free monthly quota) | Free at this volume | Fine for 1 video/day; revisit if you start archiving longer-term |
| Publishing | Instagram Graph API | Free | Cost-free regardless of tier — the only real gate is Meta App Review time, not money |
| Orchestration/compute | **GitHub Actions** (public repo, scheduled workflow) running n8n headlessly via CLI, plus FFmpeg and any other CPU steps, inside the same ephemeral job | Free, unlimited on public repos, no card | Job capped at 6 hours (irrelevant at this scale); no persistent server, so no local machine or VM to maintain — see §15 |
| State/DB | Supabase free tier | Free | Holds everything that would otherwise need to persist on a server between ephemeral runs |

**What this stack cannot do that the paid version could:** tight cross-scene character consistency, high-fidelity motion/live-action visuals, guaranteed uptime on the image layer, and TTS that doesn't eventually need a fallback swap once Sarvam's signup credit runs out. None of these block shipping — they shape what "good" looks like for the MVP. Worth revisiting this table if/when any budget becomes available; the highest-leverage single upgrade would be a paid image-gen tier with reference-image consistency, not video generation itself.

## 15. Hosting Model — GitHub Actions as the Entire Compute Layer (confirmed this revision)

Given no local machine to rely on and no budget for a VM, the pipeline runs entirely as a **scheduled GitHub Actions workflow on a public repository**. This is a genuine architectural shift from "n8n as an always-on server," so it's worth being explicit about how each piece maps:

- **Scheduling:** GitHub Actions' own `schedule` (cron) trigger replaces n8n's Schedule Trigger node as the top-level daily kickoff. The Actions job checks out the repo, spins up a container, and runs everything in sequence.
- **n8n's role:** You still build and edit workflows visually in n8n's normal editor (run temporarily whenever you're editing — locally for a session, or via a free cloud IDE like GitHub Codespaces — not as a permanent service). The exported workflow JSON lives in the repo. Each scheduled Actions run installs n8n via npx/Docker inside the ephemeral container and executes the relevant workflow headlessly via `n8n execute`, then the container is destroyed. No server bill, no uptime to manage.
- **State persistence:** Nothing can live in n8n's memory or local files between runs, since the container is thrown away every time. Supabase (already your recommended DB from §16) becomes the single source of truth for everything — story status, character bible, retry counts, cost tracking, performance data. Every workflow step reads its input from and writes its output to Supabase rather than passing state through a long-running process.
- **Secrets:** API keys (Sarvam, Gemini, Instagram, Supabase) go in **GitHub Actions Secrets** (encrypted, injected as environment variables at runtime), not in the repo or in n8n credentials on a server — this is actually a cleaner security story than a self-hosted n8n instance, not a compromise.
- **Human Approval Mode, reworked for ephemeral compute:** since nothing stays running to host a live approval webhook, the flow becomes two separate scheduled/triggered Actions runs instead of one workflow pausing mid-way. Run 1 (daily, scheduled) generates the concept/script and **posts it to a Telegram bot** (confirmed channel — simplest free option, and gives you a mobile-friendly review surface without needing to open Supabase directly). You reply/approve in Telegram, which flips the story's status in Supabase. Run 2 (triggered by `workflow_dispatch`, either manually or on a short polling schedule that checks for `approved` rows) picks up from there and continues production. Slightly different mechanism than a single paused workflow, same effect — and it's actually a more robust pattern for a system that publishes to a real Instagram account, since there's no half-finished in-memory state to lose if anything fails between the two runs.
- **Heavy compute (FFmpeg assembly):** runs inside the same Actions container as everything else. No separate render service needed — GitHub's runners have enough CPU/RAM for assembling a 5-minute video from ~30 images plus subtitle burn-in, just budget real wall-clock minutes for it (test this early, per the open item at the end of this document).

**What this costs you, to be upfront about it:** GitHub Actions containers are ephemeral and stateless by design, so debugging a failed run means reading logs after the fact, not poking at a live system. It's also a slightly less "point and click" experience than n8n's normal always-on webhook model — you're trading operational simplicity for zero cost and zero infrastructure to maintain, which is the right trade given your constraints, but worth knowing going in.

## 10. Major Technical Risks (in order of how likely they are to actually bite you)

1. **Meta App Review latency and rejection risk** — 2–4 weeks minimum, and apps get rejected for incomplete screencasts or unclear use-case descriptions on the first pass. Start this in parallel with early development, not after — it's free, but slow.
2. **Sarvam's ₹1,000 TTS credit is finite** — estimated runway is roughly 75–200 days at typical 5-minute-script character counts and Sarvam's published per-10k-character rate (a rough calculation, not a number pulled from your actual dashboard usage — worth confirming against real character-per-video cost once you're generating scripts, since it determines when the Indic-TTS fallback needs to be ready). Plan the fallback to be live by ~3 months in as a safe default rather than assuming a much longer runway.
3. **Visual consistency ceiling on the free image path** — character drift across a 5-minute story is a real, expected limitation of prompt-based-only consistency (no image conditioning), not a bug. Manage this with visual style choice and character-count limits (§6) rather than trying to engineer it away.
4. **Pollinations.ai has no SLA** — it's the load-bearing component of your entire visual layer and it's a free community service with no uptime guarantee. Build generous retry/backoff and a manual-regeneration fallback into Workflow 3, and don't schedule publishing so tightly after image generation that a slow day breaks your daily cadence.
5. **Kannada language quality drift** — LLMs are meaningfully weaker at natural, idiomatic Kannada (especially colloquial, code-mixed dialogue) than at English or Hindi. This is exactly why Human Approval Mode stays on for MVP (see banner at top) — don't trust automated "language quality" scoring alone here.
6. **CPU-bound FFmpeg assembly time** — with no GPU, rendering a 5-minute video from ~30 images plus subtitle burn-in takes real wall-clock time. Test actual render time on your hardware early so your daily schedule trigger has enough runway before the intended publish time.
7. **Ephemeral-compute debugging is harder than a live server** — a failed run means reading GitHub Actions logs after the fact, not inspecting a running n8n instance. Build good logging into every workflow step (write status/errors to Supabase, not just Actions' own log output) so failures are diagnosable without needing the container to still exist.
8. **Token/API expiry silently breaking the pipeline** — the classic "worked for weeks then just stopped" failure mode for IG automations, made worse here since you're juggling more free-tier credentials (Sarvam, Gemini, Pollinations) than a single-vendor paid stack would need. Needs proactive monitoring, not just error handling.

---

## 11. MVP Architecture (Phase 1–3 of your roadmap)

Single Postgres/Supabase instance, n8n self-hosted, 2 workflows only (Planner+Script combined, Production), no A/B testing, no analytics loop yet, Human Approval Mode **on** by default (review script + video before publish) — this de-risks language-quality and content-safety concerns while you validate the pipeline works at all. Manual publish initially, or semi-automated (workflow prepares the container, human clicks publish) until you're through Meta App Review.

## 12. Future-State Architecture (Phase 6–7)

Full closed-loop: 6 modular workflows as above, Fully Automatic Mode available as a toggle, multi-platform abstraction extended to YouTube Shorts, A/B testing on hooks/lengths with automated winner-selection feeding back into the concept-generation prompt, cost-gated auto-pause, dashboard (Section 23) as a lightweight internal tool (could be a simple Supabase-backed page, doesn't need to be elaborate).

---

## 13. Implementation Roadmap (concrete version of your Section 27 phases)

| Phase | Deliverable | Key risk to resolve during this phase |
|---|---|---|
| 0 | Meta Business/App Review submission started; Supabase schema live | App Review takes weeks — start immediately, don't gate on other work |
| 1 | Content idea → Kannada script → DB, Human Approval Mode | Validate Kannada script quality with real human review |
| 2 | Scene breakdown → visual prompts → Character Bible as reusable text-prompt block | Test Pollinations/Flux output against real character-consistency needs before committing to a visual style |
| 3 | Voice → visuals → assembly (FFmpeg) → subtitles (from final audio) | Confirm real render time on your hardware and real Sarvam credit burn rate against the 1,000 Rs budget |
| 4 | Automated QC | — |
| 5 | Instagram publishing (once App Review approved) | Confirm 60–90s cut is the published unit, not the 5-min master |
| 6 | Analytics loop | — |
| 7 | Closed-loop optimization, multi-platform | — |

---

## Resolved Decisions (confirmed this revision)

- ✅ **Path B** — full 5-minute masters, published as regular video posts.
- ✅ **Human Approval Mode ON for MVP**, script review includes code-mixing naturalness check.
- ✅ **Zero-budget stack confirmed** — Gemini free tier (LLM), Sarvam AI + Indic-TTS fallback (voice), Pollinations Flux + Kontext (visuals, image-based animatic with reference-edit consistency workflow), FFmpeg (assembly), Instagram Graph API (publishing). Full detail in §14.
- ✅ **Hosting model confirmed** — GitHub Actions (public repo) as the entire compute + scheduling layer, n8n executed headlessly via CLI per run, Supabase as the persistent state store. Full detail in §15.
- ✅ **Character-consistency approach confirmed** — Flux reference sheet + Kontext image-to-image editing per scene, paired with a flat/illustrated visual style. Full detail in §6.
- ✅ **Approval-notification channel: Telegram bot.** The two-run Human Approval handoff in §15 posts the generated concept/script to Telegram; you approve there, which flips the Supabase status a second Actions run watches for.

## What's Still Open Before Phase 1 Starts

- **Actions render time budget** — you'll test this as the build progresses, per your note; worth doing early with a short sample so the scheduled workflow's timing assumptions are grounded in reality rather than estimate.
- **Sarvam credit runway, re-verified** — worth checking actual character-per-video cost once real scripts exist, since the ₹1,000 credit likely covers ~75–200 days rather than 500 at typical script lengths (see §17 risk #2) — confirm which figure holds before relying on it for fallback timing.
- **Telegram bot setup specifics** — which n8n/Actions step sends the message, what the approval reply looks like (a fixed keyword, an inline button, etc.), and how the second Actions run detects "approved" in Supabase.

Happy to start scaffolding the repo (GitHub Actions workflow file, Supabase schema, Telegram bot wiring, Meta App Review submission) — these can move in parallel. Just say where you'd like to start.
