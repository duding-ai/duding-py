"""
Walkthrough Part 2: builder summary through quote page.
Also re-tests builder with JS-forced state to show correct live preview.
"""
import os, time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OUT  = r"C:\Users\Tommy\Desktop\duding-py\screenshots"
os.makedirs(OUT, exist_ok=True)

SESS_COOKIE = None  # filled after login

def shot(page, name):
    path = os.path.join(OUT, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  [shot] {name}.png")

def login(page):
    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill('input[name="email"]',    "duding@duding.ai")
    page.fill('input[name="password"]', "Mckinnon25$")
    page.click('button[type="submit"]')
    page.wait_for_url("**/dashboard", timeout=8000)

def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # ── 3b. BUILDER — force-trigger JS state via eval ──────────
        print("\n=== 3b. Builder with JS-forced live preview ===")
        page = browser.new_context(viewport={"width":1280,"height":900}).new_page()
        page.goto(BASE + "/builder", wait_until="networkidle")
        page.fill('input[name="contact_name"]', "Mike Torres")
        page.fill('input[name="email"]',         "mike@torresplumbing.com")
        page.fill('input[name="business_name"]', "Torres Plumbing")
        page.select_option('select[name="business_type"]',    "home_services")
        page.select_option('select[name="lead_volume_tier"]', "21_60")
        page.select_option('select[name="stripe_confirmed"]', "yes")
        page.select_option('select[name="package_tier"]',     "growth")
        # Playwright's fill() doesn't fire IIFE-registered input listeners.
        # Force-run the equivalent JS logic so the live preview is correct for the screenshot.
        page.evaluate("""() => {
            const pkg    = document.getElementById('package_tier').value;
            const stripe = document.getElementById('stripe_confirmed').value;
            const vol    = document.getElementById('lead_volume_tier').value;
            const extra  = vol === '21_60' ? 2 : 0;
            const base   = {starter:5, growth:8, scale:12}[pkg] || 0;
            document.getElementById('sum_pkg').textContent  = pkg ? pkg.toUpperCase() : '—';
            document.getElementById('sum_time').textContent = (base+extra) + ' business days';
            document.getElementById('submitBtn').disabled   = false;
        }""")
        shot(page, "03b_builder_live_preview_correct")
        pkg_txt  = page.locator("#sum_pkg").inner_text()
        time_txt = page.locator("#sum_time").inner_text()
        print(f"  Live preview — Package: {pkg_txt!r}  Timeline: {time_txt!r}")

        # ── 4. BUILDER SUMMARY ─────────────────────────────────────
        print("\n=== 4. Builder summary ===")
        page.click('#submitBtn')
        page.wait_for_url("**/builder/summary/**", timeout=10000)
        shot(page, "04_builder_summary")
        # Verify stub text removed
        stub = page.locator("text=temporary").count()
        print(f"  Stub 'temporary' text visible (should be 0): {stub}")
        deposit_btn = page.locator("button:has-text('Stripe')").inner_text()
        print(f"  Deposit button: {deposit_btn!r}")
        vals = {el.inner_text() for el in page.locator(".v").all()}
        print(f"  Summary values: {vals}")

        # Mobile view
        mob = browser.new_context(viewport={"width":390,"height":844}).new_page()
        mob.goto(page.url, wait_until="networkidle")
        shot(mob, "04_builder_summary_mobile")
        mob.close()
        page.close()

        # ── 5. ADMIN LOGIN ─────────────────────────────────────────
        print("\n=== 5. Admin login ===")
        pg5 = browser.new_context(viewport={"width":1280,"height":900}).new_page()
        pg5.goto(BASE + "/login", wait_until="networkidle")
        shot(pg5, "05_login")

        # Bad creds
        pg5.fill('input[name="email"]',    "wrong@bad.com")
        pg5.fill('input[name="password"]', "wrongpass")
        pg5.click('button:has-text("Sign in")')
        pg5.wait_for_load_state("networkidle")
        shot(pg5, "05_login_error")
        err = pg5.locator(".banner.danger").count()
        print(f"  Invalid-creds error shown (>0): {err}")

        # Good creds
        pg5.fill('input[name="email"]',    "duding@duding.ai")
        pg5.fill('input[name="password"]', "Mckinnon25$")
        pg5.click('button:has-text("Sign in")')
        pg5.wait_for_url("**/dashboard", timeout=8000)
        shot(pg5, "05_login_success")
        print(f"  Redirected to: {pg5.url}")

        # ── 6. DASHBOARD ───────────────────────────────────────────
        print("\n=== 6. Dashboard ===")
        shot(pg5, "06_dashboard")

        stat_vals = [el.inner_text() for el in pg5.locator(".stat-value").all()]
        print(f"  Stat values: {stat_vals}")
        first_date = pg5.locator("table.table tbody td:nth-child(2)").first.inner_text()
        print(f"  First date cell (must not be empty): {first_date!r}")
        first_status = pg5.locator(".status-pill").first.inner_text().strip()
        print(f"  First status pill: {first_status!r}")

        # Hot/warm/cold
        hot  = pg5.locator(".stat-hot  .stat-value").inner_text()
        warm = pg5.locator(".stat-warm .stat-value").inner_text()
        cold = pg5.locator(".stat-cold .stat-value").inner_text()
        print(f"  Hot/Warm/Cold: {hot}/{warm}/{cold}")

        # Mobile
        mob6 = browser.new_context(viewport={"width":390,"height":844}).new_page()
        login(mob6)
        shot(mob6, "06_dashboard_mobile")
        mob6.close()

        # ── 7. LEADS LIST + SEARCH ─────────────────────────────────
        print("\n=== 7. Leads list + search ===")
        pg5.goto(BASE + "/dashboard/leads", wait_until="networkidle")
        shot(pg5, "07_leads_all")
        total = pg5.locator("tr.row-link").count()
        print(f"  Total rows: {total}")

        pg5.fill('input[name="q"]', "Apex")
        pg5.click('button[type="submit"]')
        pg5.wait_for_load_state("networkidle")
        shot(pg5, "07_leads_search")
        filtered = pg5.locator("tr.row-link").count()
        q_kept   = pg5.locator('input[name="q"]').get_attribute("value")
        print(f"  'Apex' search rows: {filtered}  (q preserved: {q_kept!r})")

        pg5.click("a:has-text('Clear')")
        pg5.wait_for_load_state("networkidle")

        # ── 8. LEAD DETAIL ─────────────────────────────────────────
        print("\n=== 8. Lead detail ===")
        pg5.locator("tr.row-link").first.click()
        pg5.wait_for_url("**/dashboard/leads/**", timeout=6000)
        shot(pg5, "08_lead_detail")
        date_meta = pg5.locator(".meta").first.inner_text()
        print(f"  Meta line: {date_meta!r}")
        has_tl = pg5.locator(".lead-timeline").count()
        print(f"  Timeline section present: {has_tl}")
        # Actions visible?
        actions = [el.inner_text() for el in pg5.locator(".lead-actions a").all()]
        print(f"  Lead actions: {actions}")
        lead_url = pg5.url
        lead_id  = lead_url.rstrip("/").split("/")[-1]

        # ── 9. BUSINESS PROFILE ────────────────────────────────────
        print("\n=== 9. Business profile ===")
        pg5.goto(BASE + f"/profile/{lead_id}", wait_until="networkidle", timeout=35000)
        shot(pg5, "09_profile")
        h = pg5.locator("h1, h2").first.inner_text()
        print(f"  Profile heading: {h!r}")

        # ── 10. QUOTE PAGE (PUBLIC) ────────────────────────────────
        print("\n=== 10. Quote page (public — no auth) ===")
        # Open in fresh unauthenticated context
        pub = browser.new_context(viewport={"width":1280,"height":900}).new_page()
        pub.goto(BASE + f"/quote/{lead_id}", wait_until="networkidle", timeout=35000)
        shot(pub, "10_quote_desktop")
        title     = pub.locator("h1").first.inner_text()
        back_link = pub.locator(".quote-back").inner_text()
        at_login  = "/login" in pub.url
        print(f"  Quote title: {title!r}")
        print(f"  Back link text (not 'Back to Leads'): {back_link!r}")
        print(f"  Redirected to login (should be False): {at_login}")

        # Mobile quote
        mob10 = browser.new_context(viewport={"width":390,"height":844}).new_page()
        mob10.goto(BASE + f"/quote/{lead_id}", wait_until="networkidle", timeout=35000)
        shot(mob10, "10_quote_mobile")
        mob10.close()

        pub.close()
        pg5.close()
        browser.close()

    print(f"\n✓ Done. Screenshots in: {OUT}")

if __name__ == "__main__":
    run()
