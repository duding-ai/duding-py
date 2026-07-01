"""Screenshot builder at mobile, tablet, desktop — empty and filled."""
import os, time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
OUT  = r"C:\Users\Tommy\Desktop\duding-py\screenshots"

def shot(page, name):
    page.screenshot(path=os.path.join(OUT, f"{name}.png"), full_page=True)
    print(f"  [shot] {name}.png")

def fill_form(page):
    page.fill('input[name="contact_name"]', "Mike Torres")
    page.fill('input[name="email"]',         "mike@torresplumbing.com")
    page.fill('input[name="business_name"]', "Torres Plumbing")
    page.select_option('select[name="business_type"]',    "home_services")
    page.select_option('select[name="lead_volume_tier"]', "21_60")
    page.select_option('select[name="stripe_confirmed"]', "yes")
    page.select_option('select[name="package_tier"]',     "growth")
    # Force JS refresh (Playwright fill doesn't fire IIFE listeners)
    page.evaluate("""() => {
        const pkg    = document.getElementById('package_tier').value;
        const vol    = document.getElementById('lead_volume_tier').value;
        const extra  = vol === '21_60' ? 2 : 0;
        const PRICES = {starter:'$2,500', growth:'$4,500', scale:'$7,500'};
        const DAYS   = {starter:5, growth:8, scale:12};
        const price  = PRICES[pkg] || '--';
        const days   = pkg ? (DAYS[pkg]+extra)+' business days' : '--';

        document.getElementById('sum_pkg').textContent   = pkg ? pkg.toUpperCase() : '--';
        document.getElementById('sum_price').textContent = price;
        document.getElementById('sum_time').textContent  = days;

        const stickyPkg = document.getElementById('sticky_pkg');
        const stickySub = document.getElementById('sticky_sub');
        if (stickyPkg) stickyPkg.textContent = pkg.toUpperCase()+' · '+price+' · '+days;
        if (stickySub) stickySub.textContent = '$500 deposit · ready to reserve';

        document.getElementById('submitBtn').disabled   = false;
        const sb = document.getElementById('stickyBtn');
        if (sb) sb.disabled = false;
    }""")

def run():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        viewports = [
            ("mobile",  {"width": 390,  "height": 844}),
            ("tablet",  {"width": 768,  "height": 1024}),
            ("desktop", {"width": 1280, "height": 900}),
        ]

        for label, vp in viewports:
            print(f"\n=== {label.upper()} ({vp['width']}px) ===")
            ctx  = browser.new_context(viewport=vp)
            page = ctx.new_page()

            # Empty state
            page.goto(BASE + "/builder", wait_until="networkidle")
            shot(page, f"builder_{label}_empty")

            # Filled state
            fill_form(page)
            time.sleep(0.15)
            shot(page, f"builder_{label}_filled")

            # Verify sticky bar presence on mobile
            if label == "mobile":
                sticky = page.locator("#stickyBar")
                visible = sticky.is_visible()
                pkg_txt = page.locator("#sticky_pkg").inner_text()
                sub_txt = page.locator("#sticky_sub").inner_text()
                btn_off = page.locator("#stickyBtn").is_disabled()
                print(f"  Sticky bar visible: {visible}")
                print(f"  Sticky pkg line:    {pkg_txt!r}")
                print(f"  Sticky sub line:    {sub_txt!r}")
                print(f"  Sticky btn disabled:{btn_off}  (should be False)")

            # Desktop: verify panel visible
            if label == "desktop":
                panel = page.locator(".builder-summary")
                print(f"  Desktop panel visible: {panel.is_visible()}")
                print(f"  sum_pkg:  {page.locator('#sum_pkg').inner_text()!r}")
                print(f"  sum_price:{page.locator('#sum_price').inner_text()!r}")
                print(f"  sum_time: {page.locator('#sum_time').inner_text()!r}")

            ctx.close()

        browser.close()
    print("\nDone.")

if __name__ == "__main__":
    run()
