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

**Instagram OAuth + nightly sync built 2026-07-22, dormant behind `META_INSIGHTS_ENABLED`**
(default `false` — same hard-gate pattern as every other risk dial this session, refuses
outright rather than falling through). `platform_credentials` table +
`sync_platform_stats()` in `services/content_intelligence.py` no-op unless that env var is
exactly `"true"` AND a row has a live `access_token`.

**Honest limitation on this one specifically**: unlike everything else built this
session, this cannot be tested end-to-end against production data — it requires a real
Meta App that's passed developer review, which doesn't exist yet. The OAuth flow and
Insights fetch follow Meta's documented Graph API schema exactly, and the **dormant-gate
behavior itself is verified** (confirmed via TestClient: not-logged-in → redirect,
logged-in-but-disabled → clean 503, enabled-but-no-credentials → clean 503, no crashes
in any case) — but the actual OAuth token exchange and Insights call have not been
exercised against a live token. Metric names (`impressions`, `reach`, `video_views`,
etc.) may need adjusting once real access exists; Meta has renamed/deprecated Insights
metrics before.

- **OAuth flow**: `GET /auth/instagram/connect` (admin-only, redirects to Meta's consent
  screen) → `GET /auth/instagram/callback` (exchanges code → short-lived → long-lived
  token, resolves the linked Instagram Business Account via `/me/accounts`, stores
  everything in `platform_credentials`).
- **Nightly sync**: `job_content_platform_sync` (3:15am UTC, already registered) calls
  `sync_platform_stats()`, which pulls `GET /{ig-media-id}/insights` for every
  `ContentVideo` with a known `platform_video_id` once a connected credential exists.

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
  typically takes 3-7 business days). Add the App's OAuth redirect URI as
  `https://duding.ai/auth/instagram/callback` in the app settings — must match exactly.
  Once approved: set `META_APP_ID`, `META_APP_SECRET`, `META_INSIGHTS_ENABLED=true` on
  Railway, then visit `/auth/instagram/connect` while logged in to connect the account.
- **TikTok**: developers.tiktok.com → register a developer account → Create App → apply
  for the "Display API" product with the `video.list` and `research.data.basic` scopes →
  TikTok's review is also manual and can take 1-2 weeks. Not started — only Instagram's
  OAuth flow was built this session.

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `META_INSIGHTS_ENABLED` | For the whole feature to activate | `false` | Must be exactly `true` — hard gate on OAuth routes and the sync job |
| `META_APP_ID` | For OAuth to work | unset | From the Meta App, once it exists |
| `META_APP_SECRET` | For OAuth to work | unset | From the Meta App, once it exists |

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

## Main Outreach Sending Kill Switch

**2026-07-21 incident:** the outreach sender had no durable off switch. `_state["paused"]`
(toggled via `/dashboard/outreach/engine/pause`) is in-memory only — it resets to `False`
on every restart/deploy, so it can't survive the thing it's meant to guard against. Fixed
with the same pattern as the Brand Deals gate:

- `OUTREACH_SENDING_ENABLED` (default `false`, must be exactly `"true"`) is checked at the
  top of both cold-outreach send jobs — `job_send_next_queued` and `job_process_followups`
  in `outreach_engine.py` — before any scraping or DB mutation happens. A closed gate now
  produces zero side effects (no status change, no `OutreachActivity` row), not just a
  skipped API call. Read fresh from the environment on every call, so it's the hard floor
  underneath the soft in-memory pause, independent of it, and survives every restart/deploy.
- **Warmup ramp**: re-enabling requires flipping the env var by hand. `OutreachSendingState`
  (singleton row) records when that happens, and `_current_send_cap()` ramps the effective
  daily cap 10/day for the first week, +5/day per week after, capped at `DAILY_SEND_LIMIT`
  (50) — reached after 8 weeks. Disabling and re-enabling always restarts the ramp from
  10/day; it never resumes wherever it left off.

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `OUTREACH_SENDING_ENABLED` | For cold-outreach sending to run at all | `false` | Must be exactly `true` — hard floor, independent of the dashboard pause toggle |

## Contact Discovery Rebuild (2026-07-21/22)

Root-cause investigation + fix for the 22.6% outreach bounce rate and the 49%
no-contact-found rate, ordered by the same evidence standard as everything else in this
doc — every claim below is production data, a log line, or a commit hash, not an assumption.

### Bounce autopsy

Pulled all 140 bounced Resend records for duding.ai and cross-referenced each against its
`OutreachProspect` row. 6 were unrelated internal/test sends (CHKD waitlist tests, a
`test@example.com` welcome email, `duding@duding.ai` self-sends) — not outreach bounces at
all. Of the remaining **134 real outreach bounces**:

| Category | Count | % |
|---|---|---|
| Scraped generic role address (info@/support@/etc., actually found on the target site) | 128 | 95.5% |
| Scraped named-person address (still bounced) | 6 | 4.5% |
| Guessed/constructed address | **0** | 0% |
| Abstract API error/quota | **0** — no such integration exists in this codebase | 0% |

**The single biggest category, and really the whole story: 134/134 (100%) passed the only
check that existed — an MX-record lookup**, which proves a domain has *some* mail server,
not that *this specific mailbox* exists. The pipeline has never guessed/fabricated an
address (confirmed both by the `email_note` field on every bounce and by
`_scrape_contact_email`'s own "never fabricates a fallback" design), and there has never
been an Abstract API (or any third-party verification API) integration to swallow errors
from — that category is structurally impossible here, not a small number, zero.

Also found in the same pass: several addresses bounced 2-3 times across separate send
events weeks apart (e.g. `info@texasqualityplumbing.com` on both 07-02 and 07-14) — the
follow-up job had no re-check before hitting an address that already bounced. Fixed as
part of the verification gate below.

### Hard verification gate

`services/email_verification.py::verify_email_deliverable()` adds the missing layer: a
real SMTP RCPT-TO probe (connects to the domain's actual mail server, asks whether the
specific mailbox would accept mail, disconnects before DATA — no message is ever sent).
Fails closed on every ambiguity: no MX, connection refused, timeout, non-250 response, or
a **catch-all domain** (detected by also probing a random nonexistent local-part at the
same domain — if that gets accepted too, the domain accepts everything and a "pass" for
the real address proves nothing).

**Honest limitation, found via direct evidence, not assumed:** RCPT-TO verification is a
real improvement over MX-only, but it isn't a perfect predictor. Testing 3 real bounced
addresses from the autopsy directly: `info@texasqualityplumbing.com` correctly failed
(`smtp_unreachable` — connection actively refused), `info@colonyac.com` correctly failed
(`catch_all_domain`), but `support@zoomdrain.com` came back `verified=True`. Some
receiving mail servers accept RCPT TO for any recipient and only reject later (during or
after DATA, sometimes via content-based filtering) — a real gap no live RCPT probe closes
without a paid deliverability API. Still a large, real improvement: the gate would have
caught roughly 2 of the 3 categories of failure this specific bounce batch showed.

Wired into both `job_send_next_queued` and `job_process_followups` — verification (and a
fresh catch-all/reachability check) now runs before every initial send *and* every
follow-up, closing the re-bounce gap. Same fail-closed pattern as the
`OUTREACH_SENDING_ENABLED` kill switch: no code path turns an error or an inconclusive
result into a pass.

**Forced-failure proof** (temporary test prospect against production, cleaned up after):
```
[12:35:56] verification refused — Colonyac → info@texasqualityplumbing.com
  [smtp_unreachable: [WinError 10061] No connection could be made because the target
  machine actively refused it, confidence=low, min required=high]
```
Prospect status became `verification_failed`; `OutreachActivity` row count before/after
the run: unchanged (567 -> 567) — zero send record created, confirming no fallback path.

### Confidence scoring

Every prospect gets a 0-100 score (source quality + verification result + pattern risk)
and a tier — `confidence_tier`/`confidence_score`/`confidence_reasons` +
`verification_status`/`verification_checked_at` columns on `outreach_prospects`. Verified
+ named-person = high; failed verification always caps at low regardless of source
quality; a catch-all "pass" is treated as inconclusive, not a real pass.

`OUTREACH_MIN_CONFIDENCE_TIER` (default `high`) gates which tier is allowed to send.
Lower it by hand once the bounce rate at the current tier proves out over a real sending
window — never auto-detected, same manual-control philosophy as every other risk dial
this session.

### Discovery upgrade

`_scrape_contact_email` now tries more page patterns (added `/get-in-touch`,
`/reach-us`, `/locations`, `/service-areas`) and, if nothing on-site yields an email,
falls back to the business's linked Facebook page. Footers were already covered by the
existing whole-page text scan for server-rendered HTML (JS-rendered footers are a real,
unavoidable gap for a `requests`-based scraper). **Google Business listings are not
covered** — there's no ToS-compliant scraping path without a paid Google Places API key,
which isn't part of this codebase; see "Needs Tommy's Hands" if that channel is wanted.

Reran against the real no-contact-found prospect backlog in production (~194 of 310
attempted before hitting a time-boxed cutoff on this pass — real network scraping at
~12s/prospect; see final report for the honest per-run breakdown): **2 newly found** (1
high tier: `bugs@bugbustersusa.com`, verified + named-person; 1 low tier:
`info@kyzarairconditioning.com`, generic + unverified) — a genuinely low ~1% yield.
Manual inspection of a sample of the misses found several to be thin SEO/lead-gen
landing pages with only a phone number, not real business sites with contact/about
pages or a linked Facebook page — a real limitation of this prospect segment, not a
scraper bug. 310 remain in `pending_review`; the backfill can be resumed to completion
(same script, same idempotent per-row logic) whenever convenient — it isn't
time-sensitive, and every *new* prospect found from here forward already benefits from
the upgraded `_scrape_contact_email` automatically.

| Variable | Required? | Default | Notes |
|---|---|---|---|
| `OUTREACH_MIN_CONFIDENCE_TIER` | No | `high` | `low`\|`medium`\|`high` — minimum tier allowed to send |

### Targeting strategy rebuild (2026-07-22)

Given the autopsy finding (95.5% of bounces were generic role addresses), generic quality
is now a **hard exclusion**, not just a low score: `score_confidence()` returns
`tier="none"` for any `email_quality == "generic"` address unconditionally — verification
result doesn't matter, a generic address never queues. Prospects that hit this get a
dedicated status, `excluded_generic_address`, distinct from `verification_failed` (which
means a real attempt was made and failed) — this one was never a candidate at all. Wired
into both `job_send_next_queued` and `job_process_followups`.

**Named-person discovery — what's real, tested directly rather than assumed:**

| Channel | Status | Why |
|---|---|---|
| On-page owner-name extraction ("Owner: John Smith" near an ownership keyword) + pattern-constructed candidate addresses, SMTP-verified before use | **Built** | Real, no new dependency. Safe specifically because nothing downstream accepts an unverified address regardless of source — a constructed guess and a scraped address are held to the identical bar. |
| LinkedIn scraping | **Not built** | Tested directly: the actual `/people/` page (where leadership names live) redirects straight to a login wall for unauthenticated requests. The public company page is SEO boilerplate with zero leadership data. |
| Google Business "owner" field | **Not built — doesn't exist** | Google's public Place Details schema (name/address/phone/hours/reviews/website) has no owner/manager field. Business Profile ownership is private to the verified account holder, full stop — not an API-key limitation. |
| Facebook Page admin names | **Not built — doesn't exist** | Facebook has deliberately hidden Page admin identities from the public since 2018, specifically to prevent this kind of scraping. |
| State contractor licensing databases (e.g. Texas TDLR) | **Not built — real but not attempted** | Genuinely public, ToS-compliant data. TDLR's actual search is a legacy ASP form system (`SearchResultsListBrowse.asp`) that needs proper reverse-engineering per state to query reliably — a real follow-up project worth scoping proper, not something to half-build under time pressure. See "Needs Tommy's Hands." |

**Named-person hit rate** (production, current state): of all 570 original prospects,
**35 have a named-person (`direct`-quality) address** — the pre-existing 34 from all prior
scraping activity, +1 new from this session's discovery rerun (`bugs@bugbustersusa.com`).
184 have (only) a generic address — now hard-excluded, never queued, regardless of prior
status. 310 still have no contact found at all. The other discovery-rerun find from the
previous pass, `info@kyzarairconditioning.com`, was generic — it's now correctly
re-classified `tier=none` / `excluded_generic_address` under this rule, not counted as a
named-person hit.

### Agent Task Queue

New `agent_tasks` table (`services/agent_tasks.py`) — a durable checklist for work gated
on a human decision at a specific date, distinct from a scheduler job (runs
automatically) or a dashboard action (needs a click right now). Seeded once at startup,
idempotent.

**Filed**: "Re-enable outreach sending (post-rest-period gate)" — `status=human_gated`,
`due_date=2026-08-04`, three criteria:
1. Bounce rate <5% for 3 consecutive days
2. Warmup ramp confirmed (rest period intact, ramp not prematurely started)
3. Named-person-only filter confirmed active (no generic-quality prospect sitting in a
   sendable status)

`check_reenable_criteria()` evaluates all three against real data on demand and persists
the result onto the task row — it **never** flips `status` itself; re-enabling
`OUTREACH_SENDING_ENABLED` stays a human call by design, same philosophy as every other
risk dial this session.

### Resend domain health (duding.ai) — no sends performed for this check

Pulled directly from the Resend API — domain status, and a full daily bounce/complaint
timeline June 30 - July 21 correlated against our own send-volume log. Resend has no
suppression-list-size endpoint; estimated it instead as the count of unique addresses
with a `bounced` event (Resend auto-suppresses after a hard bounce): **78 unique
addresses**, out of 140 bounce *events* — confirming the same address got hit repeatedly
by follow-ups, consistent with the autopsy finding above.

**Domain auth**: SPF (both records) verified, DKIM verified, click/open tracking
verified and now *on* (it was off as of the previous session's diagnostic — something
changed it between then and now, worth knowing if that was intentional). The only failed
DNS record is inbound Receiving MX, which is unrelated to outbound sending — reply
detection runs over a separate IMAP mailbox, not this record. **Zero complaints, ever**,
across all 648 records pulled — a genuinely good sign; complaints damage sender
reputation more than hard bounces do.

**The bounce timeline is heavily front-loaded, not a steady ongoing problem:**

| Date | Sent (ours) | Bounce % | | Date | Sent (ours) | Bounce % |
|---|---|---|---|---|---|---|
| 06-30 | 3 | 14.3% | | 07-13 | 50 | 20.7% |
| 07-01 | 1 | 8.3% | | 07-14 | 50 | 19.6% |
| **07-02** | **50** | **49.1%** | | 07-15 | 106 | 28.0% |
| **07-03** | **50** | **66.0%** | | 07-16 | 29 | 3.3% |
| 07-04 | 22 | 32.1% | | 07-17 | 23 | 0.0% |
| 07-05–07-10 | 0 (gap) | — | | 07-18 | 20 | 4.8% |
| 07-11 | 0 | 50.0%¹ | | 07-19 | 50 | 0.0% |
| 07-12 | 50 | 16.7% | | 07-20 | 35 | 2.6% |
| | | | | 07-21 | 28 | 3.0% |

¹ 1 of 2 non-outreach sends that day.

3 catastrophic days (07-02 to 07-04) account for roughly half of all 134 bounces on
their own. Every day from 07-16 onward (excluding the 07-15 high-volume spike) sits at
or under 5% — in range of normal cold-outreach bounce rates. This reads as an acute
early event that's already partially self-corrected via prospect-mix variation, not a
domain in ongoing decline — but a 22.6% *aggregate* rate over 3 weeks is still a real
ding with major mailbox providers, who weight rolling history over weeks, not just today.

**Verdict: recoverable, not written off.** SPF/DKIM intact, zero complaints, and the
underlying cause (no mailbox-existence check before send) now has a real fix in place.
Recommend:
1. Keep `OUTREACH_SENDING_ENABLED=false` for a genuine rest period — **2 weeks minimum**
   with zero cold-outreach volume — before flipping it back on, to let the acute-period
   reputation signal age out with receiving mail servers.
2. Resume via the warmup ramp already built (10/day -> +5/week -> cap 50) rather than
   jumping back to full volume — this doubles as the gradual reputation-rebuild the rest
   period is for.
3. Do **not** stand up a fresh outreach subdomain. That's warranted for
   complaint-driven blacklisting or an ISP block signal, and there isn't one here — every
   bounce inspected is a hard "mailbox doesn't exist" pattern, not a spam-filter rejection.

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

### 5. Google Business listings — correction from the earlier note above

Previously flagged as "needs a Places API key." Checked again during the 2026-07-22
targeting rebuild: **the owner/manager name isn't in the public schema at all**, key or
no key — Place Details returns name/address/phone/hours/reviews/website, nothing about
who owns or manages the listing. A Places API key would still be useful for basic
listing data (confirming a business is real/active, phone, hours) but won't ever produce
a named-person contact — that's not a paid-tier gap, it's not public data anywhere in
Google's system.

### 6. State contractor licensing databases as a named-person discovery channel

Real, public, ToS-compliant — many states (Texas TDLR, Florida DBPR, California CSLB,
etc.) publish the licensed individual's name alongside the business name for exactly the
trades this pipeline targets. Texas TDLR's public search
(`tdlr.texas.gov/LicenseSearch/`) is confirmed to exist and take a POST search, but it's
a legacy ASP-based system without a clean business-name -> licensee-name API — properly
integrating it (and any other state) needs dedicated scoping (which trades' license types
map to which search form, pagination, rate limits) rather than a rushed add. Worth
prioritizing given how Texas-heavy the current prospect pool is, if named-person yield
needs to go materially higher than the current ~6% (35/570).

### 7. Outreach rest period before re-enabling

Per the domain-health verdict above: recommend holding `OUTREACH_SENDING_ENABLED=false`
for 2 weeks minimum from 2026-07-21 before flipping it (and setting
`OUTREACH_MIN_CONFIDENCE_TIER`, already defaulted to `high`) back on.
