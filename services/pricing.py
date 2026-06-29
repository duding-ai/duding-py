# services/pricing.py

PACKAGE = {
    "starter": {"price_cents": 250000, "days": 5},
    "growth": {"price_cents": 450000, "days": 8},
    "scale": {"price_cents": 750000, "days": 12},
}

LEAD_TIER_DAYS_MOD = {
    "0_20": 0,
    "21_60": 2,
}


def compute_price_and_timeline(
    package_tier: str, lead_volume_tier: str
) -> tuple[int, int]:
    package_tier = (package_tier or "").lower().strip()
    lead_volume_tier = (lead_volume_tier or "").lower().strip()

    if package_tier not in PACKAGE:
        raise ValueError("Invalid package tier")

    if lead_volume_tier == "61_plus":
        raise ValueError("Lead volume not supported at launch")

    if lead_volume_tier not in LEAD_TIER_DAYS_MOD:
        raise ValueError("Invalid lead volume tier")

    base = PACKAGE[package_tier]
    timeline_days = base["days"] + LEAD_TIER_DAYS_MOD[lead_volume_tier]
    return base["price_cents"], timeline_days
