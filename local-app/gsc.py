#!/usr/bin/env python3
"""
دریافت رتبه واقعی از Google Search Console API (داده خود گوگل از کاربران واقعی).

این داده «میانگین رتبه» در جستجوهای واقعی است، به تفکیک کلمه، کشور، دستگاه و صفحه.
محدودیت‌ها: سطح شهر ندارد، ۲-۳ روز تأخیر دارد، کلماتی که ایمپرشن نداشته‌اند نمی‌آیند.

راه‌اندازی:
  1. در Google Cloud Console پروژه بسازید و "Google Search Console API" را فعال کنید.
  2. یک Service Account بسازید و فایل JSON کلیدش را دانلود کنید.
  3. ایمیل Service Account را در Search Console -> Settings -> Users and permissions
     به‌عنوان کاربر (Restricted کافی است) به سایت اضافه کنید.
  4. export GSC_KEY_FILE=/path/to/key.json

استفاده:
  python gsc.py --config projects.yaml --days 28
"""
import argparse
import os
import sqlite3
from datetime import date, timedelta

import yaml
from google.oauth2 import service_account
from googleapiclient.discovery import build

DB_PATH = os.environ.get("RANK_DB", "ranks.db")
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
# GSC کشور را با کد سه‌حرفی ISO می‌خواهد
COUNTRY3 = {"ir": "irn", "us": "usa", "de": "deu", "gb": "gbr", "tr": "tur", "ae": "are", "ca": "can"}
DEVICE = {"mobile": "MOBILE", "desktop": "DESKTOP", "tablet": "TABLET"}


def service(info=None):
    if info:  # dict محتوای فایل JSON کلید
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(
            os.environ["GSC_KEY_FILE"], scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def fetch(svc, site, start, end, country=None, device=None, keywords=None):
    """همه ردیف‌ها را با صفحه‌بندی (۲۵۰۰۰ تایی) می‌گیرد."""
    filters = []
    if country:
        filters.append({"dimension": "country", "operator": "equals", "expression": country})
    if device:
        filters.append({"dimension": "device", "operator": "equals", "expression": device})
    rows, start_row = [], 0
    while True:
        body = {
            "startDate": start, "endDate": end,
            "dimensions": ["date", "query", "page"],
            "rowLimit": 25000, "startRow": start_row, "dataState": "final",
        }
        if filters:
            body["dimensionFilterGroups"] = [{"filters": filters}]
        resp = svc.searchanalytics().query(siteUrl=site, body=body).execute()
        batch = resp.get("rows", [])
        rows.extend(batch)
        if len(batch) < 25000:
            break
        start_row += 25000
    if keywords:
        wanted = {k.strip().lower() for k in keywords}
        rows = [r for r in rows if r["keys"][1].strip().lower() in wanted]
    return rows


def save(rows, project, country, device):
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS gsc (
        day TEXT, project TEXT, query TEXT, page TEXT, country TEXT, device TEXT,
        clicks INTEGER, impressions INTEGER, ctr REAL, position REAL,
        PRIMARY KEY (day, project, query, page, country, device)
    );""")
    con.executemany(
        "INSERT OR REPLACE INTO gsc VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(r["keys"][0], project, r["keys"][1], r["keys"][2], country or "all", device or "all",
          r["clicks"], r["impressions"], r["ctr"], r["position"]) for r in rows])
    con.commit()


def summary(rows):
    """میانگین وزنی رتبه بر اساس ایمپرشن، برای هر کلمه."""
    agg = {}
    for r in rows:
        q = r["keys"][1]
        a = agg.setdefault(q, [0, 0.0, 0, set()])
        a[0] += r["impressions"]
        a[1] += r["position"] * r["impressions"]
        a[2] += r["clicks"]
        a[3].add(r["keys"][2])
    return sorted(((q, s / i if i else 0, i, c, len(p)) for q, (i, s, c, p) in agg.items()),
                  key=lambda x: x[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="projects.yaml")
    ap.add_argument("--days", type=int, default=28)
    ap.add_argument("--all-queries", action="store_true", help="همه کلمات، نه فقط لیست پروژه")
    a = ap.parse_args()

    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    svc = service()
    end = date.today() - timedelta(days=3)   # داده نهایی GSC حدود ۳ روز تأخیر دارد
    start = end - timedelta(days=a.days - 1)

    for proj in cfg["projects"]:
        site = proj.get("gsc_property") or f'sc-domain:{proj["domain"]}'
        country = COUNTRY3.get(proj.get("country", "").lower())
        kws = None if a.all_queries else proj["keywords"]
        for dev in proj.get("devices", ["mobile"]):
            rows = fetch(svc, site, start.isoformat(), end.isoformat(), country, DEVICE[dev], kws)
            save(rows, proj["name"], country, dev)
            print(f"\n[{proj['name']}] {site} | {country or 'all'} | {dev} | {start} .. {end}")
            print(f"{'query':35} {'avg pos':>8} {'impr':>7} {'clicks':>7} {'pages':>6}")
            found = set()
            for q, pos, impr, clicks, pages in summary(rows):
                found.add(q.lower())
                warn = "  ⚠ چند صفحه رقابت می‌کنند" if pages > 1 else ""
                print(f"{q:35} {pos:8.1f} {impr:7} {clicks:7} {pages:6}{warn}")
            if kws:
                for k in kws:
                    if k.strip().lower() not in found:
                        print(f"{k:35} {'-':>8}   (هیچ ایمپرشنی در این بازه نداشته)")


if __name__ == "__main__":
    main()
