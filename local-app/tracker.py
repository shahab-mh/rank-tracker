#!/usr/bin/env python3
"""
Rank Tracker - رتبه سایت در گوگل، برای هر پروژه، هر کلمه کلیدی و هر نقطه جغرافیایی.

اصول دقت که در این کد رعایت شده:
  1. هر جستجو با مختصات دقیق (lat,lng) یا نام canonical گوگل انجام می‌شود، نه فقط کشور.
  2. زبان (hl)، کشور (gl)، دستگاه (desktop/mobile) برای هر جستجو صریح ست می‌شود.
  3. صفحه‌به‌صفحه تا عمق مشخص (مثلا 100) خوانده می‌شود؛ پارامتر num=100 دیگر قابل اعتماد نیست.
  4. هر جستجو N بار نمونه‌گیری می‌شود و میانه (median) گزارش می‌شود + میزان نوسان.
  5. دو نوع رتبه ذخیره می‌شود: organic (فقط نتایج ارگانیک) و absolute (با احتساب Featured snippet،
     People also ask، Local pack و ...). اختلاف ابزارها اغلب از همین‌جاست.
  6. پاسخ خام هر جستجو در دیتابیس ذخیره می‌شود تا بعدا قابل بررسی و اثبات باشد.

استفاده:
  python tracker.py run   --config projects.yaml
  python tracker.py report --project myshop
"""
import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
import yaml

DB_PATH = os.environ.get("RANK_DB", "ranks.db")


# ----------------------------------------------------------------------------- helpers
def norm_domain(d: str) -> str:
    d = d.lower().strip()
    if "://" in d:
        d = urlparse(d).netloc
    return d.split(":")[0].removeprefix("www.")


def url_matches(url: str, domain: str, include_subdomains: bool = True) -> bool:
    host = norm_domain(url)
    domain = norm_domain(domain)
    return host == domain or (include_subdomains and host.endswith("." + domain))


# ----------------------------------------------------------------------------- providers
class Provider:
    """هر provider لیستی از نتایج نرمال‌شده برمی‌گرداند:
    [{"type": "organic"|<feature>, "organic_rank": int|None, "absolute_rank": int, "url": str}]"""

    def search(self, keyword, loc, lang, country, device, depth):
        raise NotImplementedError

    def close(self):
        pass


class DataForSEO(Provider):
    """پشتیبانی از مختصات دقیق GPS (location_coordinate) -> بهترین گزینه برای «هر نقطه جغرافیایی»."""

    URL = "https://api.dataforseo.com/v3/serp/google/organic/live/advanced"

    def __init__(self, login=None, password=None):
        self.auth = (login or os.environ["DATAFORSEO_LOGIN"],
                     password or os.environ["DATAFORSEO_PASSWORD"])

    def search(self, keyword, loc, lang, country, device, depth):
        task = {
            "keyword": keyword,
            "language_code": lang,
            "device": device,
            "os": "android" if device == "mobile" else "windows",
            "depth": depth,
        }
        if "lat" in loc:
            # فرمت: "lat,lng,radius" — واحد radius را با مستندات DataForSEO چک کنید
            lng = ((float(loc["lng"]) + 180) % 360 + 360) % 360 - 180
            task["location_coordinate"] = f'{loc["lat"]},{lng:.6f},{loc.get("radius", 200)}'
        else:
            task["location_name"] = loc["name"]
        r = requests.post(self.URL, auth=self.auth, json=[task], timeout=120)
        r.raise_for_status()
        data = r.json()
        t = data["tasks"][0]
        if t.get("status_code") != 20000:
            raise RuntimeError(f'DataForSEO: {t.get("status_message")}')
        res = (t.get("result") or [{}])[0]
        items = res.get("items") or []
        out = []
        for it in items:
            out.append({
                "type": it.get("type"),
                "organic_rank": it.get("rank_group") if it.get("type") == "organic" else None,
                "absolute_rank": it.get("rank_absolute"),
                "url": it.get("url") or "",
            })
        return out, data


class SerpApi(Provider):
    """پشتیبانی از نام canonical لوکیشن گوگل (مثلا "Tehran,Tehran Province,Iran") یا uule."""

    URL = "https://serpapi.com/search.json"

    def __init__(self, key=None):
        self.key = key or os.environ["SERPAPI_KEY"]

    def search(self, keyword, loc, lang, country, device, depth):
        out, raws, abs_rank = [], [], 0
        for start in range(0, depth, 10):  # صفحه‌به‌صفحه؛ num=100 دیگر قابل اتکا نیست
            params = {
                "engine": "google", "q": keyword, "hl": lang, "gl": country,
                "device": device, "start": start, "api_key": self.key, "no_cache": "true",
            }
            if "uule" in loc:
                params["uule"] = loc["uule"]
            else:
                params["location"] = loc["name"]
            r = requests.get(self.URL, params=params, timeout=120)
            r.raise_for_status()
            data = r.json()
            raws.append(data)
            organic = data.get("organic_results") or []
            if start == 0:
                for feat in ("answer_box", "local_results", "top_stories"):
                    if feat in data:
                        abs_rank += 1
                        out.append({"type": feat, "organic_rank": None,
                                    "absolute_rank": abs_rank, "url": ""})
            for o in organic:
                abs_rank += 1
                out.append({"type": "organic", "organic_rank": o.get("position"),
                            "absolute_rank": abs_rank, "url": o.get("link", "")})
            if not organic or "next" not in (data.get("serpapi_pagination") or {}):
                break
        return out, raws


PROVIDERS = {"dataforseo": DataForSEO, "serpapi": SerpApi}


# ----------------------------------------------------------------------------- storage
def db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS checks (
        id INTEGER PRIMARY KEY,
        ts TEXT, project TEXT, domain TEXT, keyword TEXT, location TEXT,
        device TEXT, lang TEXT, sample INTEGER,
        organic_rank INTEGER, absolute_rank INTEGER, ranked_url TEXT,
        serp_features TEXT, raw TEXT
    );
    CREATE INDEX IF NOT EXISTS ix_checks ON checks(project, keyword, location, device, ts);
    """)
    return con


# ----------------------------------------------------------------------------- core
def find_rank(results, domain, include_subdomains=True):
    for r in results:
        if r["type"] == "organic" and url_matches(r["url"], domain, include_subdomains):
            return r["organic_rank"], r["absolute_rank"], r["url"]
    return None, None, None


def run(config_path):
    cfg = yaml.safe_load(open(config_path, encoding="utf-8"))
    settings = cfg.get("settings", {})
    provider = PROVIDERS[settings.get("provider", "dataforseo")]()
    samples = int(settings.get("samples", 3))
    depth = int(settings.get("depth", 100))
    pause = float(settings.get("pause_seconds", 2))
    con = db()

    for proj in cfg["projects"]:
        name, domain = proj["name"], proj["domain"]
        subs = proj.get("include_subdomains", True)
        for loc in proj["locations"]:
            loc_label = loc.get("label") or loc.get("name") or f'{loc["lat"]},{loc["lng"]}'
            for device in proj.get("devices", ["mobile"]):
                for kw in proj["keywords"]:
                    ranks = []
                    for s in range(samples):
                        try:
                            results, raw = provider.search(
                                kw, loc, proj.get("language", "fa"),
                                proj.get("country", "ir"), device, depth)
                        except Exception as e:  # noqa: BLE001
                            print(f"  ! {kw} @ {loc_label} [{device}] sample {s+1}: {e}", file=sys.stderr)
                            continue
                        org, absr, url = find_rank(results, domain, subs)
                        features = sorted({r["type"] for r in results if r["type"] != "organic"})
                        con.execute(
                            "INSERT INTO checks (ts,project,domain,keyword,location,device,lang,sample,"
                            "organic_rank,absolute_rank,ranked_url,serp_features,raw) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (datetime.now(timezone.utc).isoformat(timespec="seconds"), name, domain,
                             kw, loc_label, device, proj.get("language", "fa"), s + 1, org, absr, url,
                             json.dumps(features), json.dumps(raw, ensure_ascii=False)))
                        con.commit()
                        ranks.append(org)
                        time.sleep(pause)
                    print(f"[{name}] {kw!r:30} @ {loc_label:20} [{device:7}] -> {summarize(ranks, depth)}")


def summarize(ranks, depth):
    if not ranks:
        return "خطا / بدون داده"
    found = [r for r in ranks if r is not None]
    if not found:
        return f"خارج از {depth} نتیجه اول"
    med = statistics.median(found)
    spread = f" (نوسان {min(found)}-{max(found)})" if len(set(found)) > 1 else ""
    miss = f" | در {len(ranks)-len(found)} نمونه پیدا نشد" if len(found) < len(ranks) else ""
    return f"رتبه {med:g}{spread}{miss}"


def report(project):
    con = db()
    rows = con.execute("""
        WITH last AS (
          SELECT keyword, location, device, MAX(substr(ts,1,10)) AS day
          FROM checks WHERE project=? GROUP BY keyword, location, device)
        SELECT c.keyword, c.location, c.device, l.day,
               GROUP_CONCAT(IFNULL(c.organic_rank,'-')), GROUP_CONCAT(IFNULL(c.absolute_rank,'-')),
               MAX(c.ranked_url)
        FROM checks c JOIN last l USING(keyword, location, device)
        WHERE c.project=? AND substr(c.ts,1,10)=l.day
        GROUP BY c.keyword, c.location, c.device ORDER BY c.keyword, c.location, c.device
    """, (project, project)).fetchall()
    print(f"{'keyword':30} {'location':20} {'device':8} {'date':11} {'organic':12} {'absolute':12} url")
    for kw, loc, dev, day, org, absr, url in rows:
        print(f"{kw:30} {loc:20} {dev:8} {day:11} {org:12} {absr:12} {url or ''}")


def main():
    ap = argparse.ArgumentParser(description="Google rank tracker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--config", default="projects.yaml")
    p = sub.add_parser("report"); p.add_argument("--project", required=True)
    a = ap.parse_args()
    run(a.config) if a.cmd == "run" else report(a.project)


if __name__ == "__main__":
    main()
