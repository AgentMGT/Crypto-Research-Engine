"""Small HTTP helper: JSON GET with retries and a polite user agent."""

import time

import requests

UA = {"User-Agent": "crypto-research-engine/0.1 (+https://github.com)", "Accept": "application/json"}


def get_json(url, params=None, headers=None, retries=3, timeout=30):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, headers={**UA, **(headers or {})}, timeout=timeout)
            if r.status_code == 429:
                # Free tiers rate-limit hard; back off and retry.
                time.sleep(int(r.headers.get("retry-after", 20 * (attempt + 1))))
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"GET {url} failed: {last}")
