#!/usr/bin/env python3
"""
نسخه تحت وب رنک ترکر.
اجرا:  python app.py   -> مرورگر خودکار روی http://127.0.0.1:5000 باز می‌شود
"""
import json
import os
import random
import sqlite3
import statistics
import threading
import time
import webbrowser
from datetime import date, datetime, timedelta, timezone

from flask import Flask, abort, jsonify, request, send_from_directory

import tracker

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("RANK_WEB_DB", os.path.join(BASE, "rank_web.db"))
SHOTS = os.environ.get("RANK_SHOTS", os.path.join(BASE, "screenshots"))
app = Flask(__name__, static_folder=os.path.join(BASE, "static"))

DEFAULT_SETTINGS = {
    "provider": "demo", "samples": "3", "depth": "100", "pause_seconds": "1",
    "dataforseo_login": "", "dataforseo_password": "", "serpapi_key": "", "gsc_key_json": "",
    "browser_headless": "1", "proxy_server": "", "proxy_username": "", "proxy_password": "",
}
SECRET_KEYS = {"dataforseo_password", "serpapi_key", "gsc_key_json", "proxy_password"}


# ----------------------------------------------------------------------------- db
def con():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with con() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY, name TEXT, domain TEXT, include_subdomains INTEGER DEFAULT 1,
            language TEXT DEFAULT 'fa', country TEXT DEFAULT 'ir', devices TEXT DEFAULT '["mobile"]',
            keywords TEXT DEFAULT '[]', locations TEXT DEFAULT '[]', gsc_property TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY, project_id INTEGER, started TEXT, finished TEXT, provider TEXT
        );
        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY, run_id INTEGER, project_id INTEGER, ts TEXT,
            keyword TEXT, location TEXT, device TEXT, sample INTEGER,
            organic_rank INTEGER, absolute_rank INTEGER, ranked_url TEXT,
            serp_features TEXT, raw TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_checks ON checks(project_id, run_id);
        CREATE TABLE IF NOT EXISTS gsc (
            day TEXT, project_id INTEGER, query TEXT, page TEXT, device TEXT,
            clicks INTEGER, impressions INTEGER, position REAL,
            PRIMARY KEY (day, project_id, query, page, device)
        );
        """)
        cols = [r[1] for r in c.execute("PRAGMA table_info(checks)")]
        if "screenshot" not in cols:
            c.execute("ALTER TABLE checks ADD COLUMN screenshot TEXT")
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (k, v))


def get_settings():
    with con() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}


def project_dict(r):
    d = dict(r)
    for k in ("devices", "keywords", "locations"):
        d[k] = json.loads(d[k] or "[]")
    d["include_subdomains"] = bool(d["include_subdomains"])
    return d


def get_project(pid):
    with con() as c:
        r = c.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    return project_dict(r) if r else None


# ----------------------------------------------------------------------------- demo provider
class Demo(tracker.Provider):
    """داده ساختگی فقط برای دیدن نحوه کار ابزار. هیچ ارتباطی با گوگل ندارد."""

    def __init__(self, domain):
        self.domain = domain

    def search(self, keyword, loc, lang, country, device, depth):
        rnd = random.Random(f"{keyword}|{loc}|{device}")
        base = rnd.randint(1, 35)
        pos = max(1, base + random.choice([-1, 0, 0, 1]))
        res = [{"type": "featured_snippet", "organic_rank": None, "absolute_rank": 1, "url": "https://wiki.example/x"}]
        for i in range(1, min(depth, 50) + 1):
            url = f"https://{self.domain}/p/{rnd.randint(1, 3)}" if i == pos else f"https://site{i}.example/"
            res.append({"type": "organic", "organic_rank": i, "absolute_rank": i + 1, "url": url})
        return res, {"demo": True}


def make_provider(s, project):
    p = s["provider"]
    if p == "dataforseo":
        if not s["dataforseo_login"] or not s["dataforseo_password"]:
            raise ValueError("اطلاعات DataForSEO در تنظیمات وارد نشده")
        return tracker.DataForSEO(s["dataforseo_login"], s["dataforseo_password"])
    if p == "browser":
        from browser_serp import BrowserSERP  # noqa: PLC0415
        proxy = {"server": s["proxy_server"], "username": s["proxy_username"], "password": s["proxy_password"]}
        return BrowserSERP(proxy=proxy, headless=s["browser_headless"] == "1", screenshot_dir=SHOTS)
    if p == "serpapi":
        if not s["serpapi_key"]:
            raise ValueError("کلید SerpApi در تنظیمات وارد نشده")
        return tracker.SerpApi(s["serpapi_key"])
    return Demo(tracker.norm_domain(project["domain"]))


# ----------------------------------------------------------------------------- background job
JOB = {"running": False, "project_id": None, "done": 0, "total": 0, "current": "", "errors": [], "message": ""}
LOCK = threading.Lock()
DEVN = {"mobile": "موبایل", "desktop": "دسکتاپ"}


def fix_loc(loc):
    if loc.get("lat") is not None:
        loc = dict(loc, lng=round(((float(loc["lng"]) + 180) % 360 + 360) % 360 - 180, 6))
    return loc


def loc_label(loc):
    return loc.get("label") or loc.get("name") or f'{loc["lat"]:.4f},{loc["lng"]:.4f}'


def run_job(pid):
    try:
        s = get_settings()
        proj = get_project(pid)
        provider = make_provider(s, proj)
        samples, depth, pause = int(s["samples"]), int(s["depth"]), float(s["pause_seconds"])
        if s["provider"] == "demo":
            pause = 0.05
        if s["provider"] == "browser":
            pause = max(pause, 4)  # فاصله کمتر = کپچا
        if s["provider"] == "serpapi" and any("lat" in l and "name" not in l for l in proj["locations"]):
            raise ValueError("SerpApi مختصات GPS قبول نمی‌کند؛ برای لوکیشن‌ها نام canonical وارد کنید یا DataForSEO را انتخاب کنید")
        proj["locations"] = [fix_loc(l) for l in proj["locations"]]
        combos = [(l, d, k) for l in proj["locations"] for d in proj["devices"] for k in proj["keywords"]]
        JOB.update(total=len(combos) * samples, done=0, errors=[], message="")
        with con() as c:
            run_id = c.execute("INSERT INTO runs (project_id, started, provider) VALUES (?,?,?)",
                               (pid, now(), s["provider"])).lastrowid
        for loc, dev, kw in combos:
            label = loc_label(loc)
            for smp in range(1, samples + 1):
                JOB["current"] = f"{kw} | {label} | {DEVN.get(dev, dev)} | نمونه {smp}"
                org = absr = url = err = shot = None
                feats, raw = [], None
                try:
                    results, raw = provider.search(kw, loc, proj["language"], proj["country"], dev, depth)
                    org, absr, url = tracker.find_rank(results, proj["domain"], proj["include_subdomains"])
                    feats = sorted({r["type"] for r in results if r["type"] != "organic"})
                    shot = raw.get("screenshot") if isinstance(raw, dict) else None
                except Exception as e:  # noqa: BLE001
                    err = str(e)[:500]
                    JOB["errors"].append(f"{kw} @ {label} [{dev}]: {err}")
                with con() as c:
                    c.execute("INSERT INTO checks (run_id,project_id,ts,keyword,location,device,sample,organic_rank,"
                              "absolute_rank,ranked_url,serp_features,raw,error,screenshot) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (run_id, pid, now(), kw, label, dev, smp, org, absr, url, json.dumps(feats),
                               json.dumps(raw, ensure_ascii=False) if raw is not None else None, err, shot))
                JOB["done"] += 1
                time.sleep(pause)
        with con() as c:
            c.execute("UPDATE runs SET finished=? WHERE id=?", (now(), run_id))
        JOB["message"] = "تمام شد"
    except Exception as e:  # noqa: BLE001
        JOB["message"] = f"خطا: {e}"
    finally:
        JOB["running"] = False
        try:
            provider.close()
        except Exception:  # noqa: BLE001
            pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ----------------------------------------------------------------------------- aggregation
def aggregate(rows):
    groups = {}
    for r in rows:
        groups.setdefault((r["keyword"], r["location"], r["device"]), []).append(r)
    out = {}
    for key, rs in groups.items():
        org = [r["organic_rank"] for r in rs if r["organic_rank"] is not None]
        ab = [r["absolute_rank"] for r in rs if r["absolute_rank"] is not None]
        urls = [r["ranked_url"] for r in rs if r["ranked_url"]]
        out[key] = {
            "median": statistics.median(org) if org else None,
            "min": min(org) if org else None, "max": max(org) if org else None,
            "absolute": statistics.median(ab) if ab else None,
            "found": len(org), "samples": len(rs),
            "errors": sum(1 for r in rs if r["error"]),
            "url": max(set(urls), key=urls.count) if urls else None,
            "features": json.loads(next((r["serp_features"] for r in rs if not r["error"]), "[]") or "[]"),
            "screenshots": [r["screenshot"] for r in rs if r["screenshot"]],
            "ts": max(r["ts"] for r in rs),
        }
    return out


# ----------------------------------------------------------------------------- routes
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/shots/<name>")
def shots(name):
    if "/" in name or "\\" in name or not name.endswith(".jpg"):
        abort(404)
    return send_from_directory(SHOTS, name)


@app.get("/api/settings")
def api_get_settings():
    s = get_settings()
    return jsonify({k: ("" if k in SECRET_KEYS else v) for k, v in s.items()} |
                   {f"has_{k}": bool(s.get(k)) for k in SECRET_KEYS})


@app.post("/api/settings")
def api_set_settings():
    data = request.get_json()
    if data.get("gsc_key_json"):
        try:
            info = json.loads(data["gsc_key_json"])
            assert "client_email" in info and "private_key" in info
        except Exception:  # noqa: BLE001
            return jsonify(error="فایل JSON کلید Service Account معتبر نیست"), 400
    with con() as c:
        for k in DEFAULT_SETTINGS:
            if k in data and not (k in SECRET_KEYS and data[k] == ""):
                c.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, str(data[k])))
    return jsonify(ok=True)


@app.get("/api/projects")
def api_projects():
    with con() as c:
        return jsonify([project_dict(r) for r in c.execute("SELECT * FROM projects ORDER BY id")])


@app.post("/api/projects")
@app.put("/api/projects/<int:pid>")
def api_save_project(pid=None):
    d = request.get_json()
    if not d.get("name") or not d.get("domain"):
        return jsonify(error="نام و دامنه الزامی است"), 400
    vals = (d["name"].strip(), tracker.norm_domain(d["domain"]), int(bool(d.get("include_subdomains", True))),
            d.get("language", "fa"), d.get("country", "ir"), json.dumps(d.get("devices") or ["mobile"]),
            json.dumps([k.strip() for k in d.get("keywords", []) if k.strip()], ensure_ascii=False),
            json.dumps([fix_loc(l) for l in d.get("locations", [])], ensure_ascii=False), d.get("gsc_property", "").strip())
    with con() as c:
        if pid:
            c.execute("UPDATE projects SET name=?,domain=?,include_subdomains=?,language=?,country=?,devices=?,"
                      "keywords=?,locations=?,gsc_property=? WHERE id=?", vals + (pid,))
        else:
            pid = c.execute("INSERT INTO projects (name,domain,include_subdomains,language,country,devices,"
                            "keywords,locations,gsc_property) VALUES (?,?,?,?,?,?,?,?,?)", vals).lastrowid
    return jsonify(get_project(pid))


@app.delete("/api/projects/<int:pid>")
def api_delete_project(pid):
    with con() as c:
        for t in ("checks", "runs", "gsc"):
            c.execute(f"DELETE FROM {t} WHERE project_id=?", (pid,))
        c.execute("DELETE FROM projects WHERE id=?", (pid,))
    return jsonify(ok=True)


@app.post("/api/projects/<int:pid>/run")
def api_run(pid):
    proj = get_project(pid)
    if not proj:
        return jsonify(error="پروژه پیدا نشد"), 404
    if not proj["keywords"] or not proj["locations"]:
        return jsonify(error="حداقل یک کلمه کلیدی و یک لوکیشن لازم است"), 400
    with LOCK:
        if JOB["running"]:
            return jsonify(error="یک بررسی در حال اجراست"), 409
        JOB.update(running=True, project_id=pid, done=0, total=0, current="شروع...", errors=[], message="")
    threading.Thread(target=run_job, args=(pid,), daemon=True).start()
    return jsonify(ok=True)


@app.get("/api/status")
def api_status():
    return jsonify(JOB)


@app.get("/api/projects/<int:pid>/results")
def api_results(pid):
    with con() as c:
        runs = c.execute("SELECT * FROM runs WHERE project_id=? AND finished IS NOT NULL ORDER BY id DESC LIMIT 2",
                         (pid,)).fetchall()
        if not runs:
            return jsonify(rows=[], run=None)
        cur = aggregate(c.execute("SELECT * FROM checks WHERE run_id=?", (runs[0]["id"],)).fetchall())
        prev = aggregate(c.execute("SELECT * FROM checks WHERE run_id=?", (runs[1]["id"],)).fetchall()) if len(runs) > 1 else {}
        since = (date.today() - timedelta(days=31)).isoformat()
        gsc_rows = c.execute("SELECT query, device, SUM(impressions) i, SUM(clicks) cl, SUM(position*impressions) p "
                             "FROM gsc WHERE project_id=? AND day>=? GROUP BY lower(query), device", (pid, since)).fetchall()
    g = {(r["query"].lower(), r["device"]): {"position": r["p"] / r["i"] if r["i"] else None,
                                             "impressions": r["i"], "clicks": r["cl"]} for r in gsc_rows}
    rows = []
    for (kw, loc, dev), v in sorted(cur.items()):
        p = prev.get((kw, loc, dev), {})
        rows.append({"keyword": kw, "location": loc, "device": dev, **v,
                     "previous": p.get("median"), "gsc": g.get((kw.lower(), dev))})
    return jsonify(rows=rows, run=dict(runs[0]))


@app.get("/api/projects/<int:pid>/history")
def api_history(pid):
    kw, loc, dev = request.args["keyword"], request.args["location"], request.args["device"]
    with con() as c:
        rs = c.execute("SELECT run_id, ts, organic_rank, absolute_rank FROM checks WHERE project_id=? AND keyword=? "
                       "AND location=? AND device=? ORDER BY ts", (pid, kw, loc, dev)).fetchall()
        gs = c.execute("SELECT day, SUM(position*impressions)/SUM(impressions) pos FROM gsc WHERE project_id=? "
                       "AND lower(query)=lower(?) AND device=? GROUP BY day ORDER BY day", (pid, kw, dev)).fetchall()
    runs = {}
    for r in rs:
        runs.setdefault(r["run_id"], {"ts": r["ts"], "ranks": []})["ranks"].append(r["organic_rank"])
    points = [{"ts": v["ts"], "median": statistics.median([x for x in v["ranks"] if x is not None])
               if any(x is not None for x in v["ranks"]) else None} for v in runs.values()]
    return jsonify(serp=points, gsc=[dict(r) for r in gs])


@app.post("/api/projects/<int:pid>/gsc")
def api_gsc(pid):
    s, proj = get_settings(), get_project(pid)
    if not s["gsc_key_json"]:
        return jsonify(error="کلید Service Account گوگل در تنظیمات وارد نشده"), 400
    import gsc  # noqa: PLC0415
    try:
        svc = gsc.service(json.loads(s["gsc_key_json"]))
        site = proj["gsc_property"] or f'sc-domain:{proj["domain"]}'
        country = gsc.COUNTRY3.get(proj["country"].lower())
        end = date.today() - timedelta(days=3)
        start = end - timedelta(days=89)
        total = 0
        with con() as c:
            for dev in proj["devices"]:
                rows = gsc.fetch(svc, site, start.isoformat(), end.isoformat(), country,
                                 gsc.DEVICE[dev], proj["keywords"])
                c.executemany("INSERT OR REPLACE INTO gsc VALUES (?,?,?,?,?,?,?,?)",
                              [(r["keys"][0], pid, r["keys"][1], r["keys"][2], dev, r["clicks"],
                                r["impressions"], r["position"]) for r in rows])
                total += len(rows)
        return jsonify(ok=True, rows=total, site=site)
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "403" in msg or "permission" in msg.lower():
            msg = (f"دسترسی رد شد. ایمیل Service Account را در Search Console به پراپرتی {site} اضافه کنید "
                   f"و مطمئن شوید نام پراپرتی درست است. ({msg[:200]})")
        return jsonify(error=msg), 400


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"\n  Rank Tracker:  http://127.0.0.1:{port}\n")
    app.run(host="127.0.0.1", port=port, debug=False)
