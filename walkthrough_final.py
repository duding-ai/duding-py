"""Final confirmation screenshots for dashboard and lead detail fixes."""
import os
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OUT  = r"C:\Users\Tommy\Desktop\duding-py\screenshots"

def shot(page, name):
    path = os.path.join(OUT, f"{name}.png")
    page.screenshot(path=path, full_page=True)
    print(f"  [shot] {name}.png")

def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # Login
        pg = browser.new_context(viewport={"width":1280,"height":900}).new_page()
        pg.goto(BASE + "/login", wait_until="networkidle")
        pg.fill('input[name="email"]',    "duding@duding.ai")
        pg.fill('input[name="password"]', "Mckinnon25$")
        pg.click('button:has-text("Sign in")')
        pg.wait_for_url("**/dashboard", timeout=8000)

        # Dashboard — should now show only 20 rows
        print("\n=== Dashboard (capped at 20) ===")
        shot(pg, "FINAL_01_dashboard")
        row_count = pg.locator("table.table tbody tr").count()
        view_all  = pg.locator("text=View all").count()
        recent_h2 = pg.locator("h2:has-text('Recent')").count()
        print(f"  Table rows visible: {row_count} (should be <=20)")
        print(f"  'View all' link present: {view_all}")
        print(f"  'Recent Activity' heading: {recent_h2}")

        # Score + Tier columns now showing
        header_cells = [el.inner_text() for el in pg.locator("table.table thead th").all()]
        print(f"  Table headers: {header_cells}")

        # Lead detail — Generate & Send Quote button properly styled
        print("\n=== Lead detail (button style) ===")
        pg.goto(BASE + "/dashboard/leads/89", wait_until="networkidle")
        shot(pg, "FINAL_02_lead_detail")
        btn_class = pg.locator("form button").first.get_attribute("class")
        print(f"  Quote button class: {btn_class!r}  (should include 'btn btn-primary')")

        # Mobile dashboard
        print("\n=== Dashboard mobile ===")
        mob = browser.new_context(viewport={"width":390,"height":844}).new_page()
        mob.goto(BASE + "/login", wait_until="networkidle")
        mob.fill('input[name="email"]',    "duding@duding.ai")
        mob.fill('input[name="password"]', "Mckinnon25$")
        mob.click('button:has-text("Sign in")')
        mob.wait_for_url("**/dashboard", timeout=8000)
        shot(mob, "FINAL_03_dashboard_mobile")
        mob.close()

        pg.close()
        browser.close()

    print("\nDone — final screenshots written.")

if __name__ == "__main__":
    run()
