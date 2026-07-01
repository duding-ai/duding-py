import time
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from db import SessionLocal
from models.build import Build

BASE = "http://127.0.0.1:8000"


def wait_for_any_selector(page, selectors, timeout=120000):
    deadline = time.time() + timeout / 1000
    while True:
        for selector in selectors:
            for frame in page.frames:
                try:
                    locator = frame.locator(selector)
                    if locator.count() > 0:
                        return selector
                except Exception:
                    continue
        if time.time() > deadline:
            raise PlaywrightTimeoutError(
                f"Timed out waiting for selectors: {selectors}"
            )
        page.wait_for_timeout(250)


def find_selector(page, *selectors, timeout=120000):
    selector = wait_for_any_selector(page, selectors, timeout=timeout)
    if selector:
        return selector
    raise RuntimeError(f"No matching selector found among: {selectors}")


def fill_selector(page, selector, value):
    for frame in page.frames:
        try:
            locator = frame.locator(selector)
            if locator.count() > 0:
                locator.first.scroll_into_view_if_needed()
                locator.first.fill(value)
                return
        except Exception:
            continue
    raise RuntimeError(f"Selector not found for fill: {selector}")


def click_selector_if_exists(page, selector):
    for frame in page.frames:
        try:
            locator = frame.locator(selector)
            if locator.count() > 0:
                locator.first.scroll_into_view_if_needed()
                locator.first.click(force=True)
                return True
        except Exception:
            continue
    return False


def selector_exists(page, selector):
    for frame in page.frames:
        try:
            if frame.locator(selector).count() > 0:
                return True
        except Exception:
            continue
    return False


def optional_find_selector(page, *selectors):
    deadline = time.time() + 120
    while True:
        for selector in selectors:
            for frame in page.frames:
                try:
                    locator = frame.locator(selector)
                    if locator.count() > 0:
                        return selector
                except Exception:
                    continue
        if time.time() > deadline:
            return None
        page.wait_for_timeout(250)


def handle_stripe_link_page(page):
    if click_selector_if_exists(page, 'button:has-text("Pay without Link")'):
        print("Clicked Pay without Link to bypass Link login")
        page.wait_for_timeout(5000)
        return

    if selector_exists(page, "text=Enter 000000 to continue") or selector_exists(
        page, 'input[aria-label^="Security code character"]'
    ):
        print("Stripe Link verification detected; entering 000000")
        for i in range(1, 7):
            fill_selector(page, f"input[aria-label='Security code character {i}']", "0")
        if click_selector_if_exists(page, 'button:has-text("Continue")'):
            page.wait_for_timeout(5000)
            return
        page.keyboard.press("Enter")
        page.wait_for_timeout(5000)


def create_build():
    data = {
        "contact_name": "Stripe Test",
        "email": "stripe-test@example.com",
        "business_name": "Stripe Test LLC",
        "business_type": "home_services",
        "lead_volume_tier": "21_60",
        "stripe_confirmed": "yes",
        "package_tier": "growth",
    }
    resp = requests.post(f"{BASE}/builder", data=data, allow_redirects=False)
    if resp.status_code != 303:
        raise RuntimeError(f"Build creation failed: {resp.status_code} {resp.text}")
    location = resp.headers.get("location")
    if not location:
        raise RuntimeError("Build creation redirect location missing")
    build_id = location.rstrip("/").split("/")[-1]
    return build_id, f"{BASE}{location}"


def get_checkout_url(build_id):
    resp = requests.post(f"{BASE}/builder/deposit/{build_id}", allow_redirects=False)
    if resp.status_code != 303:
        raise RuntimeError(f"Deposit start failed: {resp.status_code} {resp.text}")
    location = resp.headers.get("location")
    if not location:
        raise RuntimeError("Stripe checkout redirect location missing")
    return location


def run():
    build_id, summary_url = create_build()
    print(f"Created build {build_id}")
    print(f"Summary URL: {summary_url}")
    checkout_url = get_checkout_url(build_id)
    print(f"Checkout URL: {checkout_url}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        print("Opening Stripe checkout page...")
        page.goto(checkout_url, wait_until="load", timeout=120000)
        page.wait_for_timeout(15000)

        handle_stripe_link_page(page)

        print("after goto page.url", page.url)
        print("title", page.title())
        print("frames", len(page.frames))
        print("counts #cardNumber", page.locator("#cardNumber").count())
        print(
            "counts input[name='cardnumber']",
            page.locator("input[name='cardnumber']").count(),
        )
        print("counts #billingName", page.locator("#billingName").count())

        wait_for_any_selector(
            page,
            [
                "#cardNumber",
                "input[name='cardnumber']",
                "input[autocomplete='cc-number']",
                "input[aria-label*='card']",
            ],
            timeout=120000,
        )
        wait_for_any_selector(
            page,
            [
                "#billingName",
                "input[name='billingName']",
                "input[name='name']",
                "input[autocomplete='cc-name']",
            ],
            timeout=120000,
        )
        wait_for_any_selector(
            page,
            ['button:has-text("Pay")', 'button:has-text("Continue")'],
            timeout=120000,
        )
        print(f"Stripe checkout page loaded: {page.url}")

        page.screenshot(path="stripe_checkout_loaded.png", full_page=True)

        card_number = find_selector(
            page,
            "#cardNumber",
            "input[name='cardnumber']",
            "input[autocomplete='cc-number']",
            "input[aria-label*='card']",
        )
        card_expiry = find_selector(
            page,
            "#cardExpiry",
            "input[name='exp-date']",
            "input[autocomplete='cc-exp']",
            "input[aria-label*='MM']",
            "input[placeholder*='MM']",
        )
        card_cvc = find_selector(
            page,
            "#cardCvc",
            "input[name='cvc']",
            "input[autocomplete='cc-csc']",
            "input[aria-label*='CVC']",
            "input[placeholder*='CVC']",
        )
        billing_name = find_selector(
            page,
            "#billingName",
            "input[name='billingName']",
            "input[name='name']",
            "input[autocomplete='cc-name']",
        )
        billing_postal = find_selector(
            page,
            "#billingPostalCode",
            "input[name='postal']",
            "input[name='postal_code']",
            "input[name='postalCode']",
            "input[autocomplete='postal-code']",
        )
        phone_number = optional_find_selector(
            page,
            "#phoneNumber",
            "input[name='phone']",
            "input[name='phoneNumber']",
            "input[autocomplete='tel']",
        )

        fill_selector(page, card_number, "4242424242424242")
        fill_selector(page, card_expiry, "12 / 35")
        fill_selector(page, card_cvc, "123")
        fill_selector(page, billing_name, "Stripe Test")
        fill_selector(page, billing_postal, "12345")
        if phone_number:
            fill_selector(page, phone_number, "+12015550123")
        page.wait_for_timeout(2000)
        page.screenshot(path="stripe_checkout_filled.png", full_page=True)

        print("Submitting Stripe payment...")
        pay_button = find_locator(
            page, 'button:has-text("Pay")', 'button:has-text("Continue")'
        )
        pay_button.scroll_into_view_if_needed()
        pay_button.click(force=True)

        try:
            page.wait_for_url("**/builder/summary/**", timeout=120000)
        except Exception as exc:
            page.screenshot(path="stripe_checkout_after_submit.png", full_page=True)
            raise
        print(f"Completed checkout, returned to summary: {page.url}")

        success = page.locator("text=DEPOSIT PAID").count()
        if success:
            print("Deposit paid confirmed on summary page")
        else:
            raise RuntimeError("Deposit paid indicator missing after checkout")

        # Wait for webhook processing to update the database
        time.sleep(5)
        with SessionLocal() as db:
            build = db.query(Build).filter(Build.build_id == build_id).first()
            if not build:
                raise RuntimeError(f"Build not found in DB for build_id={build_id}")
            if not build.deposit_paid:
                raise RuntimeError(
                    f"Webhook did not update deposit_paid for build {build_id}: {build.deposit_paid}"
                )
            print(f"Webhook confirmed build.deposit_paid=True for build {build_id}")
            print(f"deposit_payment_intent_id={build.deposit_payment_intent_id}")

        page.screenshot(path="stripe_deposit_complete.png", full_page=True)
        print("Saved screenshot stripe_deposit_complete.png")

        browser.close()


if __name__ == "__main__":
    run()
