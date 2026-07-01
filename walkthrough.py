"""
Full flow walkthrough for Duding.ai — takes screenshots of every key page.
"""
import os, time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OUT  = r"C:\Users\Tommy\Desktop\duding-py\screenshots"
os.makedirs(OUT, exist_ok=True)

def shot(page, name):
    path = os.path.join(OUT, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  [shot] {name}.png")

def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        # ── 1. LANDING PAGE ──────────────────────────────────────
        print("\n=== 1. Landing page ===")
        page.goto(BASE + "/", wait_until="networkidle")
        shot(page, "01_landing")
        hero_text = page.locator("h1.headline").inner_text()
        print(f"  Hero: {hero_text!r}")
        cta = page.locator(".hero a.btn-primary").inner_text()
        print(f"  Hero CTA: {cta!r}")

        # mobile view
        ctx2 = browser.new_context(viewport={"width": 390, "height": 844})
        mob = ctx2.new_page()
        mob.goto(BASE + "/", wait_until="networkidle")
        shot(mob, "01_landing_mobile")
        ctx2.close()

        # ── 2. LEAD CAPTURE FORM ─────────────────────────────────
        print("\n=== 2. Lead capture form ===")
        page.goto(BASE + "/#lead-form", wait_until="networkidle")
        shot(page, "02_lead_form")

        # Fill and submit
        page.fill('input[name="name"]', "Test Lead")
        page.fill('input[name="business"]', "Apex Plumbing")
        page.fill('input[name="website"]', "https://apexplumbing.com")
        page.fill('input[name="email"]', "test@apexplumbing.com")
        page.select_option('select[name="budget"]', "$5k–$20k")
        page.select_option('select[name="role"]', "Owner / Operator")
        page.select_option('select[name="cadence"]', "Under 5 minutes")
        page.select_option('select[name="recent"]', "Google Ads")
        shot(page, "02_lead_form_filled")
        page.click('button#submitBtn')
        page.wait_for_load_state("networkidle", timeout=10000)
        shot(page, "02_lead_form_success")
        success = page.locator('.banner.success')
        if success.count():
            print(f"  Success banner: {success.inner_text()!r}")
        else:
            print(f"  WARNING: no success banner found at {page.url}")

        # ── 3. BUILDER ───────────────────────────────────────────
        print("\n=== 3. Builder ===")
        page.goto(BASE + "/builder", wait_until="networkidle")
        shot(page, "03_builder_empty")

        # Fill builder form
        page.fill('input[name="contact_name"]', "Mike Torres")
        page.fill('input[name="email"]', "mike@torresplumbing.com")
        page.fill('input[name="business_name"]', "Torres Plumbing")
        page.select_option('select[name="business_type"]', "home_services")
        page.select_option('select[name="lead_volume_tier"]', "21_60")
        page.select_option('select[name="stripe_confirmed"]', "yes")
        page.select_option('select[name="package_tier"]', "growth")

        # Wait for JS to update the live summary
        time.sleep(0.3)
        shot(page, "03_builder_filled")

        # Read the JS-computed timeline
        timeline_text = page.locator("#sum_time").inner_text()
        pkg_text      = page.locator("#sum_pkg").inner_text()
        btn_disabled  = page.locator("#submitBtn").is_disabled()
        print(f"  Package preview: {pkg_text!r}")
        print(f"  Timeline preview: {timeline_text!r}  (expected '10 business days')")
        print(f"  Submit button disabled: {btn_disabled}  (expected False)")

        # Mobile view
        ctx3 = browser.new_context(viewport={"width": 390, "height": 844})
        mob3 = ctx3.new_page()
        mob3.goto(BASE + "/builder", wait_until="networkidle")
        shot(mob3, "03_builder_mobile")
        ctx3.close()

        # ── 4. BUILDER SUMMARY ───────────────────────────────────
        print("\n=== 4. Builder summary ===")
        page.click('#submitBtn')
        page.wait_for_url("**/builder/summary/**", timeout=8000)
        shot(page, "04_builder_summary")
        summary_h1 = page.locator("h1").inner_text()
        print(f"  Summary title: {summary_h1!r}")
        # Confirm stub text is gone
        stub_visible = page.locator("text=temporary").count()
        print(f"  Stub text present (should be 0): {stub_visible}")
        deposit_btn = page.locator("button:has-text('Stripe')").inner_text()
        print(f"  Deposit button text: {deposit_btn!r}")
        timeline_val = page.locator(".v").all_inner_texts()
        print(f"  Summary values: {timeline_val}")

        # ── 5. ADMIN LOGIN ───────────────────────────────────────
        print("\n=== 5. Admin login ===")
        page.goto(BASE + "/login", wait_until="networkidle")
        shot(page, "05_login")

        # Wrong password
        page.fill('input[name="email"]', "wrong@email.com")
        page.fill('input[name="password"]', "wrongpass")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        error_visible = page.locator("text=Invalid").count()
        print(f"  Bad creds error shown (should be >0): {error_visible}")
        shot(page, "05_login_error")

        # Correct login
        page.fill('input[name="email"]', "duding@duding.ai")
        page.fill('input[name="password"]', "Mckinnon25$")
        page.click('button[type="submit"]')
        page.wait_for_url("**/dashboard", timeout=5000)
        shot(page, "05_login_success_redirect")
        print(f"  Redirected to: {page.url}")

        # ── 6. DASHBOARD ─────────────────────────────────────────
        print("\n=== 6. Dashboard ===")
        shot(page, "06_dashboard")

        stat_vals = page.locator(".stat-value").all_inner_texts()
        print(f"  Stat values: {stat_vals}")
        hot_warm_cold = [page.locator(".stat-hot .stat-value").inner_text(),
                         page.locator(".stat-warm .stat-value").inner_text(),
                         page.locator(".stat-cold .stat-value").inner_text()]
        print(f"  Hot/Warm/Cold: {hot_warm_cold}")

        # Check date column is no longer empty
        first_date_cell = page.locator("table.table tbody td:nth-child(2)").first.inner_text()
        print(f"  First date cell: {first_date_cell!r}  (should not be empty)")

        # Check status pill
        first_status = page.locator(".status-pill").first.inner_text()
        print(f"  First status pill: {first_status!r}")

        # Dashboard mobile
        ctx4 = browser.new_context(viewport={"width": 390, "height": 844})
        mob4 = ctx4.new_page()
        mob4.goto(BASE + "/dashboard", wait_until="networkidle")
        shot(mob4, "06_dashboard_mobile")
        ctx4.close()

        # ── 7. LEADS LIST + SEARCH ───────────────────────────────
        print("\n=== 7. Leads list + search ===")
        page.goto(BASE + "/dashboard/leads", wait_until="networkidle")
        shot(page, "07_leads_all")
        total_rows = page.locator("tr.row-link").count()
        print(f"  Total rows (all): {total_rows}")

        # Search
        page.fill('input[name="q"]', "Apex")
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        shot(page, "07_leads_search_apex")
        filtered_rows = page.locator("tr.row-link").count()
        q_val = page.locator('input[name="q"]').get_attribute("value")
        print(f"  Search 'Apex' rows: {filtered_rows}  (q preserved: {q_val!r})")

        # Clear search
        page.click("a:has-text('Clear')")
        page.wait_for_load_state("networkidle")
        cleared_rows = page.locator("tr.row-link").count()
        print(f"  Cleared rows: {cleared_rows}  (should match total)")

        # ── 8. LEAD DETAIL ───────────────────────────────────────
        print("\n=== 8. Lead detail ===")
        page.goto(BASE + "/dashboard/leads", wait_until="networkidle")
        page.locator("tr.row-link").first.click()
        page.wait_for_url("**/dashboard/leads/**", timeout=5000)
        shot(page, "08_lead_detail")
        lead_h1 = page.locator("h1").inner_text()
        print(f"  Lead title: {lead_h1!r}")
        date_text = page.locator(".meta").first.inner_text()
        print(f"  Meta (date): {date_text!r}")
        has_timeline = page.locator(".timeline").count()
        print(f"  Timeline section present: {has_timeline}")

        # ── 9. PROFILE SCRAPER ───────────────────────────────────
        print("\n=== 9. Business profile ===")
        lead_url = page.url
        lead_id  = lead_url.rstrip("/").split("/")[-1]
        page.goto(BASE + f"/profile/{lead_id}", wait_until="networkidle", timeout=30000)
        shot(page, "09_profile")
        profile_title = page.locator("h1, h2").first.inner_text()
        print(f"  Profile title: {profile_title!r}")

        # ── 10. QUOTE PAGE ───────────────────────────────────────
        print("\n=== 10. Quote page (public) ===")
        page.goto(BASE + f"/quote/{lead_id}", wait_until="networkidle", timeout=30000)
        shot(page, "10_quote")
        quote_title = page.locator("h1").first.inner_text()
        print(f"  Quote title: {quote_title!r}")
        back_link = page.locator(".quote-back").inner_text()
        print(f"  Back link (should be duding.ai, not 'Back to Leads'): {back_link!r}")

        # Verify public access (no redirect to login)
        print(f"  URL after load (should NOT contain /login): {page.url}")

        # Mobile
        ctx5 = browser.new_context(viewport={"width": 390, "height": 844})
        mob5 = ctx5.new_page()
        mob5.goto(BASE + f"/quote/{lead_id}", wait_until="networkidle", timeout=30000)
        shot(mob5, "10_quote_mobile")
        ctx5.close()

        browser.close()
    print(f"\n✓ All screenshots saved to {OUT}")

if __name__ == "__main__":
    run()
