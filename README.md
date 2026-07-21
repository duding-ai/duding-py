# Duding Project (Python + Flask)

This is the **Duding Project**, built with Python and Flask.
The goal is to provide a foundation for future features, with clean structure for both frontend (HTML/CSS) and backend (Flask + SQLite).

---

## 🚀 Setup Instructions

### 1. Clone the Repo
```bash
git clone https://github.com/duding-ai/duding-py.git
cd duding-py
```

---

## Content Intelligence (Get CHKD)

`/dashboard/content-intel` — tracks every Get CHKD TikTok/Instagram video: posted stats,
computed engagement rates, hook/CTA comparisons, and waitlist-signup attribution.

- **Ingestion**: screenshot upload (TikTok Studio / Instagram Insights) is read by Claude
  vision (`services/content_intelligence.py::parse_stat_screenshots`, same
  `anthropic.Anthropic(api_key=...)` client pattern as the `/chkd/ai/coach` proxy). If
  extraction fails for any reason, the user lands on the manual-entry form with a
  friendly error banner — this never 500s.
- **Seed data**: `seed_content_videos()` runs on every app startup (`app.py`
  `_seed_content_videos`), keyed per-row on `(platform, series_number)` — safe to run on
  every restart, only backfills whichever seed rows are missing.
- **Nightly jobs** (`outreach_engine.py`): `job_content_waitlist_sync` (3:00am UTC) pulls
  CHKD waitlist signups from Supabase into `waitlist_attribution`; `job_content_platform_sync`
  (3:15am UTC) is Phase 2 scaffolding — see below.

### Content Intelligence — Phase 2 (official API auto-pull)

Not active yet. `platform_credentials` table + `sync_platform_stats()` in
`services/content_intelligence.py` are scaffolded and no-op until a row with a live
`access_token` exists.

**What each API can/can't provide, once connected:**

| | Meta Graph API (Instagram) | TikTok Display API |
|---|---|---|
| Basic counts (views, likes, comments, shares) | ✅ | ✅ |
| Retention curve / skip rate | ❌ | ❌ |
| Traffic sources | ❌ | ❌ |
| Audience demographics | Partial (business accounts only, via Insights) | ❌ |

Screenshot ingestion stays the source of truth for retention/skip-rate/traffic/audience
data indefinitely — neither public API exposes it.

**Developer-app application steps (do this under the Get CHKD accounts, not Tommy's personal ones):**

- **Meta (Instagram)**: developers.facebook.com → Create App → type "Business" → add the
  "Instagram Graph API" product → connect the `@getchkd` Instagram professional account →
  submit for App Review requesting `instagram_basic` + `instagram_manage_insights`
  permissions (this requires a screencast + written use-case description; Meta review
  typically takes 3-7 business days).
- **TikTok**: developers.tiktok.com → register a developer account → Create App → apply
  for the "Display API" product with the `video.list` and `research.data.basic` scopes →
  TikTok's review is also manual and can take 1-2 weeks.

---

## Brand Deals Agent (Get CHKD)

Automated brand-partnership prospecting + outreach, reusing the outreach engine's
prospector → verify → personalize → send → track skeleton
(`services/brand_deals.py`, jobs in `outreach_engine.py`).

- **Prospector** (`job_brand_prospector`, every 2h 9am-9pm ET): works the seed list in
  `data/brand_seed_list.json` (58 brands across supplements / fitness apparel / grooming /
  faith / productivity / food — **append new brands directly to that JSON file**), finds a
  contact email via the same site-scraping pipeline the main outreach engine uses
  (`app.py::_scrape_contact_email`), validates MX records, and classifies direct vs.
  generic. Direct emails land as `verified`; generic (`partnerships@`, `collabs@`, etc.)
  land as `held_for_review` on the Brand Deals tab.
- **Sender** (`job_brand_pitch_sender`, hourly 9am-6pm ET): personalizes a pitch via
  Claude (pitch kit lives in `PITCH_KIT` at the top of `services/brand_deals.py` — edit
  it there to change Tommy's positioning/offers), sends one initial email + one automatic
  follow-up after ~5 business days, then stops. Daily cap: `BRAND_DEALS_DAILY_CAP`
  (default 10).

### Sending is OFF by default — here's exactly where

`services/brand_deals.py::_from_email()` is the single choke point every sender call goes
through, and it requires **three** things all at once before it returns a usable address —
missing any one of them makes it return `""`, which both `send_email(...)` call sites in
`run_pitch_sender()` treat as "do not send":

1. `BRAND_DEALS_SENDING_ENABLED=true` — explicit opt-in, defaults to unset/off.
2. `BRAND_DEALS_FROM_EMAIL` set to something.
3. That address's domain is **not** in `_PROTECTED_DOMAINS` (`getchkd.app`, `duding.ai`) —
   hardcoded, no override, no fallback. Even if `BRAND_DEALS_FROM_EMAIL` were accidentally
   set to a `getchkd.app`/`duding.ai` address, sending still refuses and logs why.

With the gate closed:

- The **prospector still runs** and queues verified/held_for_review prospects (safe —
  it never calls `send_email` at all).
- The **sender hard-skips every send**, flips eligible prospects to `queued`, and logs
  `[brand_deals] N prospect(s) queued — sending gate closed (...)`.
- The **Brand Deals tab** on `/dashboard/clients/1` shows a "⚠ DNS setup required" banner
  (`get_pipeline_counts()` → `dns_ready` flag, checked in `client_detail_internal.html`).

Never falls back to sending from `tommy@getchkd.app` or `duding@duding.ai` — those
domains' transactional deliverability stays protected regardless of Brand Deals state.

**2026-07-20 incident note:** the health monitor flagged a "brand_deals bounce rate"
critical that turned out to be a false positive in the *monitor*, not a sending-gate
failure — `brand_outreach_emails` had zero rows in production the whole time (confirmed
by direct query), and Brand Deals code has never been deployed at all (confirmed via
`git log origin/main` — last deployed commit predates this feature). The monitor's
bounce check was attributing 100% of CHKD's shared Resend-account traffic (welcome/
streak/re-engagement emails, all correctly sent from `tommy@getchkd.app`) to
`brand_deals` purely because both share `CHKD_RESEND_API_KEY`. Fixed in
`_check_bounce_rate()` — it now skips the Resend-wide pull entirely for any agent with
zero rows in its own local send-tracking table, since there's nothing to legitimately
attribute otherwise. The domain-blocklist + explicit-enable gate above were added as
defense-in-depth from the same investigation, not because either gap was found to have
actually been exploited.

---

## Needs Tommy's Hands

### 1. `partners.getchkd.app` DNS setup (~10 min, Namecheap + Resend)

Brand Deals cold email must NOT send from `tommy@getchkd.app` (protects that domain's
transactional deliverability). Set up a dedicated subdomain:

1. Log into Resend under the `getchkdapp@gmail.com` account → **Domains** → **Add Domain**
   → enter `partners.getchkd.app`.
2. Resend will show 3-4 DNS records (SPF `TXT`, DKIM `TXT`, and usually a `MX` record for
   the return-path). Copy them.
3. Log into Namecheap → Domain List → `getchkd.app` → **Advanced DNS** → **Add New
   Record** for each one Resend gave you (Host = the subdomain part Resend specifies,
   e.g. `resend._domainkey.partners`, Type = as shown, Value = as shown).
4. Back in Resend, click **Verify DNS Records** (can take a few minutes to a few hours
   to propagate).
5. Once verified, set `BRAND_DEALS_FROM_EMAIL=tommy@partners.getchkd.app` on Railway
   (see env vars below) — sending turns on automatically on the next scheduler run, no
   redeploy needed beyond setting the var.

### 2. New environment variables to set on Railway

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `BRAND_DEALS_SENDING_ENABLED` | For sending to activate | unset (sending stays off) | Must be exactly `true` — explicit opt-in, separate from having an address configured |
| `BRAND_DEALS_FROM_EMAIL` | For sending to activate | unset (sending stays off) | Set after step 1 above, e.g. `tommy@partners.getchkd.app` — refused outright if it's a `getchkd.app`/`duding.ai` address |
| `BRAND_DEALS_DAILY_CAP` | No | `10` | Max brand pitches (incl. follow-ups) per day |

No new env vars are required for Content Intelligence — it reuses `ANTHROPIC_API_KEY`,
`SUPABASE_URL`, and `SUPABASE_SERVICE_KEY`, all already set for the CHKD app + coach proxy.

### 3. Phase 2 — Meta / TikTok developer app applications

See "Content Intelligence — Phase 2" above for the exact steps. Not urgent — screenshot
ingestion is fully functional as the primary data source; Phase 2 only adds
convenience auto-pull of basic counts, and both platforms' review processes take days
to weeks, so it's worth starting whenever Tommy has a spare 30 minutes, not blocking
anything else.

### 4. Pre-existing bug found during verification (unrelated to this build)

`/dashboard/content` (the older "Content Ideas" page — Instagram captions / video hooks /
content angles generated by the outreach engine, `app.py::content_ideas_page`) throws
`TemplateNotFound: content.html` — that template doesn't exist in `templates/` and never
has, per git history predating this session. Not touched by Content Intelligence (which
now lives at the separate path `/dashboard/content-intel` specifically to avoid colliding
with this route) or Brand Deals. Flagging since it surfaced during this session's
verification pass, not something introduced by it.
