# app/services/site_scan.py

from typing import Dict, List, Optional
import requests
from bs4 import BeautifulSoup


def scan_site(url: str, timeout: int = 10) -> Dict:
    """
    Lightweight site scan.
    This is NOT crawling — just a single-page intelligence snapshot.
    """

    result = {
        "website_title": None,
        "website_description": None,
        "phones_found": [],
        "social_links": [],
        "services_found": [],
        "scraped": False,
        "error": None,
    }

    try:
        resp = requests.get(
            url, timeout=timeout, headers={"User-Agent": "DudingBot/1.0"}
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        if soup.title:
            result["website_title"] = soup.title.get_text(strip=True)

        # Meta description
        desc = soup.find("meta", attrs={"name": "description"})
        if desc and desc.get("content"):
            result["website_description"] = desc["content"].strip()

        # Phone numbers (very naive, refine later)
        text = soup.get_text(" ", strip=True)
        import re

        phones = re.findall(r"\(?\b\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b", text)
        result["phones_found"] = list(set(phones))[:5]

        # Social links
        socials = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if any(
                s in href for s in ["facebook.com", "instagram.com", "linkedin.com"]
            ):
                socials.append(href)
        result["social_links"] = list(set(socials))[:5]

        result["scraped"] = True

    except Exception as e:
        result["error"] = str(e)

    return result


if __name__ == "__main__":
    print(scan_site("https://example.com"))
