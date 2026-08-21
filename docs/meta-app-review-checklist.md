# Meta App Review Checklist — Instagram Publishing

**Start this before anything else.** Every source checked (Feb–July 2026) agrees:
approval takes 2–6 weeks and first submissions get rejected often. This is the
one dependency in the whole build that's purely calendar time, not engineering
effort — so it should run in parallel with Phase 1–3 development, not after.

## Prerequisites (do these first, in order)

- [ ] **Convert Instagram account to Professional (Business).** Personal accounts
      cannot use the publishing API at all — this isn't a permission issue, it's
      a hard account-type requirement.
- [ ] **Create/use a Facebook Page** and link it to the Instagram Business account.
      No Page = no publishing, regardless of permissions.
- [ ] **Create a Facebook Business Account** (business.facebook.com) if you don't
      have one — the Page and app both need to sit under it.
- [ ] **Register a Meta Developer App** at developers.facebook.com. Add the
      Instagram Graph API product to it.

## Permissions to request

Request these together in one submission where possible — each is reviewed
individually, but bundling avoids multiple review cycles:

- [ ] `instagram_business_basic` — required before other permissions can be used
- [ ] `instagram_business_content_publish` — the core publishing permission
      (this is the current permission name; older guides reference
      `instagram_content_publish` — **verify the exact current name in Meta's
      App Review dashboard before submitting**, naming has changed before)
- [ ] `instagram_business_manage_insights` (or equivalent) — needed for
      Workflow 6 / analytics pull, request now rather than in a second round

## What the review actually checks (per multiple 2026 sources)

- [ ] **Screencast showing real usage**, not a mockup — walk through the exact
      flow your app uses: generating content → creating the media container →
      publishing → the result appearing on Instagram. Reviewers reject
      screencasts that look like bulk/automated posting without a visible
      user-initiated action per post — for this project, showing the Telegram
      approval step before a post goes out is a strong, honest way to satisfy this.
- [ ] **Long-lived token refresh logic**, working and demonstrated. Tokens expire
      ~60 days — apps that don't show a refresh flow get rejected for not
      meeting API standards. Build this into the repo as its own scheduled
      Actions workflow (not included in the initial scaffold — add before the
      first token would expire, not after).
- [ ] **Privacy policy and terms of service at a public URL.** A simple hosted
      markdown page is enough at this stage — this is a hard requirement, not
      optional paperwork.
- [ ] **Clear, specific use-case description** in the submission form — describe
      exactly what the app does (automated Kannada story video generation and
      publishing to your own Business account) rather than a vague description.
      Reviewers bounce vague submissions.

## After approval

- [ ] Store `IG_BUSINESS_ACCOUNT_ID` and the long-lived `IG_ACCESS_TOKEN` as
      GitHub Actions Secrets (see `.env.example`) — never in code.
- [ ] Set a calendar reminder at ~45 days post-token-issue to refresh it, don't
      rely on remembering — this is the single most common "worked for weeks
      then silently stopped" failure mode in IG automation projects.
- [ ] Confirm rate limits for your account tier (Meta uses a Business Use Case
      formula based on impressions, not a flat calls-per-hour cap) — irrelevant
      at 1 post/day, but check before ever increasing volume or adding accounts.

## If rejected

Rejection on the first pass is common, not a sign something is fundamentally
wrong. Typical reasons seen across current reports: incomplete screencast,
missing token-refresh demonstration, vague use-case description, screencast
recorded against a personal (not Business) account. Address the specific
reviewer feedback and resubmit — most teams get through by the second or
third round.
