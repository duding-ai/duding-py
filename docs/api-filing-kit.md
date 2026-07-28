# API Filing Kit — Meta (Instagram) & TikTok Developer Apps

Reference content for the developer-app applications described in `README.md`
("Content Intelligence — Phase 2" and "Needs Tommy's Hands #3"). Nothing here
is submitted anywhere automatically — this is copy-paste content for the
actual Meta for Developers / TikTok for Developers forms.

---

## Meta (Instagram) — Instagram Graph API

| Field | Value |
|---|---|
| App type | Business |
| Product | Instagram Graph API |
| Redirect URI | `https://duding.ai/auth/instagram/callback` |
| Scopes requested | `instagram_basic`, `instagram_manage_insights` |
| Privacy Policy URL | `https://duding.ai/privacy` |
| Terms of Service URL | `https://duding.ai/terms` |

**⚠ Open decision — App name (unresolved, needs Tommy's call before filing):**
Meta ties app review to the account that owns the app, and the app name is
part of what's visually shown to Meta's reviewers and (if this ever goes
beyond dev-mode testing) to end users. Two options on the table:

1. **"Get CHKD Analytics"** — names it after the client business (Get CHKD)
   whose Instagram account this actually connects to and whose content
   strategy it serves.
2. **"Duding.ai"** — names it after the operator/agency building and running
   it (Duding.ai), consistent with `duding.ai` being the domain hosting the
   OAuth flow and the redirect URI.

Not a technical distinction — pick whichever entity should be the
Meta-facing owner of record for this app (matters for review correspondence,
future re-verification, and if the scope ever expands to other clients'
Instagram accounts under the same app). **Decide before submitting**, since
renaming after submission means re-review.

**Use-case description (paste into the App Review form):**

> This app reads content performance data (reach, engagement, watch time,
> and audience insights) from our own Instagram Business account. The data
> is used exclusively to inform our own content strategy. It is not shared
> with third parties, not used for advertising or targeting, and no other
> user's data is accessed — only the metrics of the account we own and
> operate.

---

## TikTok — Display API

| Field | Value |
|---|---|
| Product | Display API |
| Scopes requested | `video.list`, `research.data.basic` |
| Redirect URI | `https://duding.ai/auth/tiktok/callback` |

**Redirect URI status:** the route now exists — a stub added alongside this
doc (`app.py::tiktok_oauth_callback_stub`, `GET /auth/tiktok/callback`). It
resolves and responds (session-gated, same pattern as the Instagram
callback) but performs no real token exchange — there is no TikTok app,
client id, or secret yet. Confirmed live: importing `app.py` registers the
route (`['/auth/instagram/connect', '/auth/instagram/callback',
'/auth/tiktok/callback']`). Safe to submit the TikTok form now; the URI
should pass whatever reachability check TikTok's form does at submission
time, since it 200s for a logged-in session and redirects to `/login`
otherwise rather than 404ing.

**Use-case description:** same framing as Meta's above — reads own-account
video performance (view/engagement metrics) to inform content strategy, no
third-party data access.

---

## Not yet decided / not part of this filing

- Privacy Policy (`/privacy`) and Terms of Service (`/terms`) routes both
  exist in `app.py` (confirmed) — worth a quick read before submitting to
  make sure the actual copy still matches what this app does, since content
  wasn't re-checked as part of this doc.
- TikTok's actual developer-app *creation* (name, business entity, contact
  info) hasn't been decided here either — only the product/scopes/redirect
  URI are filled in above. Same "which entity owns this app" question from
  the Meta section likely applies here too once you get to that step.
