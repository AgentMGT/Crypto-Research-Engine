"""Developer activity for each subnet, the Taostats "GitHub activity" view without a Taostats key.

Subnet owners publish a GitHub link in their on-chain identity. This counts the
commits on that repository over the last 30 days through GitHub's public API
(the workflow's own GITHUB_TOKEN lifts the limit to 1,000 calls an hour).
Results are cached in data/tao_dev.json so each run only refreshes stale repos.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

CACHE = Path(__file__).resolve().parent.parent / "data" / "tao_dev.json"
API = "https://api.github.com"
CACHE_HOURS = 12
MAX_CALLS = 180
RX = re.compile(r"github\.com/([A-Za-z0-9_.-]+)(?:/([A-Za-z0-9_.-]+))?", re.I)


def parse_repo(url):
    """'https://github.com/org/repo(.git)' -> ('org', 'repo'); an org link -> ('org', None)."""
    m = RX.search(url or "")
    if not m:
        return None
    owner, repo = m.group(1), m.group(2)
    if repo:
        repo = re.sub(r"\.git$", "", repo)
    return owner, repo


class _GitHub:
    def __init__(self):
        self.calls = 0
        tok = os.environ.get("GITHUB_TOKEN")
        self.h = {"Accept": "application/vnd.github+json", "User-Agent": "crypto-research-engine"}
        if tok:
            self.h["Authorization"] = f"Bearer {tok}"

    def get(self, path, params=None):
        if self.calls >= MAX_CALLS:
            raise RuntimeError("call budget used")
        self.calls += 1
        r = requests.get(API + path, params=params, headers=self.h, timeout=20)
        if r.status_code in (404, 409, 451):  # missing, empty or blocked repo
            return None
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
            raise RuntimeError("GitHub rate limit reached")
        r.raise_for_status()
        return r.json()


def _activity(gh, owner, repo):
    if not repo:  # org or user link: use the repo pushed most recently
        rows = gh.get(f"/users/{owner}/repos", {"sort": "pushed", "per_page": 1})
        if not rows:
            return {"repo": owner, "missing": True}
        repo = rows[0]["name"]
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    commits = gh.get(f"/repos/{owner}/{repo}/commits", {"since": since, "per_page": 100})
    if commits is None:
        return {"repo": f"{owner}/{repo}", "missing": True}
    last = commits[0]["commit"]["committer"]["date"] if commits else None
    if not last:
        info = gh.get(f"/repos/{owner}/{repo}") or {}
        last = info.get("pushed_at")
    return {"repo": f"{owner}/{repo}", "commits_30d": len(commits), "capped": len(commits) >= 100, "last_commit": last}


def fetch(subnets, errors):
    """{netuid: activity} for every subnet that lists a GitHub link on chain."""
    try:
        cache = json.loads(CACHE.read_text())
    except (OSError, ValueError):
        cache = {}
    now = time.time()
    gh = _GitHub()
    out, stop = {}, False
    # Biggest subnets first, so a short budget still covers the ones people trade.
    for s in sorted(subnets, key=lambda s: -(s.get("mcap_tao") or 0)):
        link = (s.get("identity") or {}).get("github_repo")
        parsed = parse_repo(link)
        if not parsed:
            continue
        key = "/".join(p for p in parsed if p).lower()
        hit = cache.get(key)
        if not stop and (not hit or now - hit.get("t", 0) > CACHE_HOURS * 3600):
            try:
                hit = {**_activity(gh, *parsed), "t": now}
                cache[key] = hit
            except Exception as e:  # noqa: BLE001
                if "budget" not in str(e):
                    errors.append(f"github {key}: {e}")
                # Out of calls: keep using whatever is cached, stale or not.
                stop = "rate limit" in str(e) or "budget" in str(e)
        if hit:
            out[s["netuid"]] = {**hit, "url": link}
    # Drop repos no subnet has pointed at for a week.
    cache = {k: v for k, v in cache.items() if now - v.get("t", 0) < 7 * 86400}
    try:
        CACHE.write_text(json.dumps(cache, indent=0, sort_keys=True))
    except OSError:
        pass
    print(f"  github activity: {len(out)} subnets, {gh.calls} API calls")
    return out
