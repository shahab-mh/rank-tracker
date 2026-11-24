"""
موتور «مرورگر واقعی»: جستجو در google.com با Chrome واقعی، دقیقا مثل یک کاربر ناشناس.

چرا دقیق‌تر از API هاست:
  - صفحه واقعی گوگل در یک مرورگر واقعی باز می‌شود (نه نسخه‌ای که API ها می‌گیرند).
  - لوکیشن با سه لایه تنظیم می‌شود: پراکسی residential در همان شهر (IP واقعی محلی)،
    پارامتر uule گوگل، و GPS مرورگر.
  - پروفایل تمیز (ناشناس، بدون کوکی و سابقه) و pws=0 -> بدون شخصی‌سازی.
  - از هر صفحه نتایج اسکرین‌شات ذخیره می‌شود تا هر عدد قابل اثبات باشد.
"""
import base64
import os
import random
import string
import time
import uuid
from urllib.parse import quote, urlencode

try:
    from tracker import url_matches
except ImportError:  # pragma: no cover
    url_matches = None

UULE_KEY = string.ascii_uppercase + string.ascii_lowercase + string.digits + "-_"


def uule_for_name(canonical_name: str) -> str:
    """uule برای نام canonical گوگل، مثل 'New York,New York,United States'."""
    b = canonical_name.encode("utf-8")
    return "w+CAIQICI" + UULE_KEY[len(b) % 64] + quote(base64.b64encode(b).decode())


def uule_for_coords(lat: float, lng: float, radius_m: int = 1000) -> str:
    """uule برای مختصات دقیق GPS."""
    s = (f"role: CURRENT_LOCATION\nproducer: DEVICE_LOCATION\nradius: {radius_m}\n"
         f"latlng <\n  latitude_e7: {round(lat * 1e7)}\n  longitude_e7: {round(lng * 1e7)}\n>")
    return "a+" + quote(base64.b64encode(s.encode()).decode())


def normalize_loc(loc):
    """طول جغرافیایی را به بازه -180..180 برمی‌گرداند (کلیک روی کپی‌های تکراری نقشه)."""
    if loc.get("lat") is None:
        return loc
    loc = dict(loc)
    loc["lng"] = round(((float(loc["lng"]) + 180) % 360 + 360) % 360 - 180, 6)
    loc["lat"] = max(-90.0, min(90.0, float(loc["lat"])))
    return loc


class CaptchaError(Exception):
    pass


# کوکی رد کردن صفحه رضایت گوگل (در کشورهای اروپایی)
CONSENT_COOKIES = [
    {"name": "SOCS", "value": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg", "domain": ".google.com", "path": "/"},
    {"name": "CONSENT", "value": "PENDING+987", "domain": ".google.com", "path": "/"},
]

EXTRACT_JS = r"""
() => {
  const root = document.querySelector('#rso') || document.querySelector('#search') || document.querySelector('#main') || document.body;
  // المان‌های غیر ارگانیک (best effort - گوگل مرتب ظاهرش را عوض می‌کند)
  const featureDefs = [
    ['ads', '#tads, #tadsb, [data-text-ad]'],
    ['ai_overview', '[data-mcpr], div[jscontroller][data-al]'],
    ['featured_snippet', '.xpdopen .ifM9O, block-component, [data-attrid="wa:/description"]'],
    ['people_also_ask', '.related-question-pair, [jsname="Cpkphb"]'],
    ['local_pack', '.VkpGBb, [data-local-attribute], #local-results, g-more-link a[href*="tbm=lcl"]'],
    ['top_stories', 'g-section-with-header [role="heading"], [data-news-cluster-id]'],
    ['video', 'g-scrolling-carousel video-voyager, [data-vid]'],
    ['images', '#iur, [data-lpage]'],
  ];
  const headingWords = {
    ai_overview: /AI Overview|نمای کلی هوش مصنوعی|Übersicht mit KI|Aperçu IA/i,
    people_also_ask: /People also ask|Others want to know|سؤالات دیگر|Ähnliche Fragen|Autres questions/i,
    top_stories: /Top stories|اخبار برتر|Schlagzeilen|À la une/i,
    local_pack: /^Places$|^Businesses$|مکان‌ها|Orte|Lieux/i,
    video: /^Videos$|ویدیوها|^Vidéos$/i,
    images: /^Images$|تصاویر|^Bilder$/i,
  };
  const feats = [];
  const visible = el => (el.textContent || '').trim().length > 0 && el.getBoundingClientRect().height > 1;
  for (const [type, sel] of featureDefs) document.querySelectorAll(sel).forEach(el => { if (visible(el)) feats.push({type, el, box: true}); });
  document.querySelectorAll('[role="heading"], h2, .mgAbYb').forEach(h => {
    const t = (h.textContent || '').trim();
    for (const [type, re] of Object.entries(headingWords)) if (re.test(t) && visible(h)) feats.push({type, el: h});
  });
  // "https://www.site.com › products › x" -> "https://www.site.com/products/x"
  const citeUrl = a => {
    const block = a.closest('[data-rpos], [data-hveid], .MjjYud, .g') || a.parentElement;
    const cite = a.querySelector('cite') || block?.querySelector('cite');
    if (!cite) return null;
    let t = (cite.textContent || '').trim().replace(/\s+/g, ' ');
    if (!/^https?:\/\//.test(t)) { if (/^[\w.-]+\.[a-z]{2,}/i.test(t)) t = 'https://' + t; else return null; }
    const parts = t.split(' › ').map(x => x.trim()).filter(x => x && x !== '...' && x !== '…');
    return parts[0] + (parts.length > 1 ? '/' + parts.slice(1).join('/') : '');
  };
  // لینک‌های نتیجه: چند الگو، چون گوگل مدام markup را عوض می‌کند
  const candSel = 'a[jsname="UWckNb"], a[href]:has(h3), a[href]:has([role="heading"][aria-level="3"])';
  const cands = [...root.querySelectorAll(candSel)];
  // باکسی که بیش از ۳ لینک نتیجه را در بر بگیرد، wrapper است نه المان ویژه -> نادیده
  for (const f of feats) if (f.box && cands.filter(a => f.el.contains(a)).length > 3) f.wrapper = true;
  const real = feats.filter(f => !f.wrapper), boxes = real.filter(f => f.box);
  const skipInside = '.related-question-pair, g-scrolling-carousel, [data-initq], #tads, #tadsb, [data-text-ad]';
  const organic = [], seen = new Set();
  cands.forEach(a => {
    if (a.closest(skipInside) || boxes.some(f => f.el.contains(a))) return;
    const h3 = a.querySelector('h3, [role="heading"]') || a;
    let href = a.href;
    const key = a.getAttribute('href');
    try {
      const u = new URL(href);
      if (/(^|\.)google\.[a-z.]+$/.test(u.hostname)) {
        const q = u.pathname === '/url' && (u.searchParams.get('q') || u.searchParams.get('url'));
        if (q && /^https?:/.test(q)) href = q;
        else {
          // لینک رمزشده گوگل (/goto?url=...): آدرس را از خط سبز زیر نتیجه (cite) بخوان
          href = citeUrl(a);
          if (!href) return;
        }
      }
    } catch (e) { return; }
    if (!/^https?:/.test(href) || seen.has(key)) return;
    seen.add(key);
    // تعداد المان‌های غیر ارگانیکِ قبل از این نتیجه -> رتبه مطلق
    const before = new Set(real.filter(f => !f.el.contains(a) && (f.el.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING)).map(f => f.type + '|' + (f.el.closest('[data-hveid]')?.getAttribute('data-hveid') || '')));
    organic.push({url: href, title: (h3.textContent || '').trim().slice(0, 200), featuresBefore: before.size});
  });
  return {organic, features: [...new Set(real.map(f => f.type))],
          noResults: !!document.querySelector('#topstuff .card-section, .mnr-c') && organic.length === 0};
}
"""


class BrowserSERP:
    """
    proxy: {"server": "http://host:port", "username": "user-country-{country}-city-{city}-session-{session}",
            "password": "..."} یا None (اینترنت همین کامپیوتر)
    """

    def __init__(self, proxy=None, headless=True, screenshot_dir="screenshots", executable_path=None,
                 max_retries=3, page_delay=(1.0, 2.5)):
        self.proxy = proxy if proxy and proxy.get("server") else None
        self.headless = headless
        self.screenshot_dir = screenshot_dir
        self.executable_path = executable_path or os.environ.get("CHROME_PATH") or None
        self.max_retries = max_retries
        self.page_delay = page_delay
        self._pw = self._browser = None
        self.target = None  # (domain, include_subdomains): وقتی پیدا شد صفحات بعدی خوانده نمی‌شوند
        os.makedirs(screenshot_dir, exist_ok=True)

    # --------------------------------------------------------------- lifecycle
    def _start(self):
        if self._browser:
            return
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
        self._pw = sync_playwright().start()
        args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-default-browser-check"]
        opts = {"headless": self.headless, "args": args}
        if self.proxy:
            opts["proxy"] = {"server": "http://per-context"}
        attempts = ([{"executable_path": self.executable_path}] if self.executable_path else []) + \
                   [{"channel": "chrome"}, {}]  # اول Chrome نصب‌شده روی سیستم، بعد Chromium خود Playwright
        last = None
        for extra in attempts:
            try:
                self._browser = self._pw.chromium.launch(**opts, **extra)
                return
            except Exception as e:  # noqa: BLE001
                last = e
        raise RuntimeError(f"مرورگر اجرا نشد. Google Chrome را نصب کنید یا دستور "
                           f"'python -m playwright install chromium' را اجرا کنید. ({last})")

    def close(self):
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        finally:
            self._browser = self._pw = None

    # --------------------------------------------------------------- context
    def _context(self, loc, lang, country, device):
        major = (self._browser.version or "131").split(".")[0]
        if device == "mobile":
            ctx_opts = dict(self._pw.devices["Pixel 7"])
            ctx_opts["user_agent"] = (f"Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
                                      f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Mobile Safari/537.36")
        else:
            ctx_opts = {"viewport": {"width": 1366, "height": 900},
                        "user_agent": f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                      f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"}
        ctx_opts["locale"] = f"{lang}-{country.upper()}" if country else lang
        if loc.get("lat") is not None:
            ctx_opts["geolocation"] = {"latitude": loc["lat"], "longitude": loc["lng"], "accuracy": 50}
            ctx_opts["permissions"] = ["geolocation"]
        if loc.get("timezone"):
            ctx_opts["timezone_id"] = loc["timezone"]
        if self.proxy:
            session = uuid.uuid4().hex[:10]
            user = (self.proxy.get("username") or "").format(
                country=(loc.get("proxy_country") or country or "").lower(),
                city=(loc.get("proxy_city") or "").lower().replace(" ", ""),
                session=session)
            ctx_opts["proxy"] = {"server": self.proxy["server"], "username": user or None,
                                 "password": self.proxy.get("password") or None}
        ctx = self._browser.new_context(**ctx_opts)
        ctx.add_cookies(CONSENT_COOKIES)
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        return ctx

    # --------------------------------------------------------------- search
    def _url(self, keyword, loc, lang, country, start):
        params = {"q": keyword, "hl": lang, "pws": "0"}
        if country:
            params["gl"] = country
        if start:
            params["start"] = start
        url = "https://www.google.com/search?" + urlencode(params)
        if loc.get("lat") is not None:
            url += "&uule=" + uule_for_coords(loc["lat"], loc["lng"])
        elif loc.get("name"):
            url += "&uule=" + uule_for_name(loc["name"])
        return url

    def _handle_consent(self, page):
        if "consent." in page.url:
            for sel in ("#W0wltc", "button:has-text('Reject all')", "button:has-text('Alle ablehnen')",
                        "form[action*='consent'] button"):
                try:
                    page.click(sel, timeout=2500)
                    page.wait_for_load_state("domcontentloaded")
                    return
                except Exception:  # noqa: BLE001
                    continue

    def _search_once(self, keyword, loc, lang, country, device, depth):
        ctx = self._context(loc, lang, country, device)
        page = ctx.new_page()
        state = {"page": 0}

        def _block(route):
            rt = route.request.resource_type
            # فونت و ویدیو هیچ‌وقت لازم نیست؛ تصویر فقط در صفحه اول (برای اسکرین‌شات)
            if rt in ("font", "media") or (rt == "image" and state["page"] > 0):
                return route.abort()
            return route.fallback()
        page.route("**/*", _block)
        results, features, shot, abs_offset, found_urls = [], [], None, 0, []
        try:
            for start in range(0, depth, 10):
                state["page"] = start // 10
                page.goto(self._url(keyword, loc, lang, country, start), wait_until="domcontentloaded", timeout=45000)
                self._handle_consent(page)
                if "/sorry/" in page.url or page.query_selector("#captcha-form, form#captcha"):
                    raise CaptchaError("گوگل کپچا نشان داد")
                try:
                    page.wait_for_selector("#search, #rso, #main", timeout=15000)
                except Exception:  # noqa: BLE001
                    pass
                data = page.evaluate(EXTRACT_JS)
                if start == 0:
                    features = data["features"]
                    name = f"{uuid.uuid4().hex}.jpg"
                    page.screenshot(path=os.path.join(self.screenshot_dir, name), full_page=True, type="jpeg", quality=55)
                    shot = name
                    try:  # HTML صفحه برای عیب‌یابی
                        with open(os.path.join(self.screenshot_dir, name[:-4] + ".html"), "w", encoding="utf-8") as fh:
                            fh.write(page.content())
                    except Exception:  # noqa: BLE001
                        pass
                found_urls += [o["url"] for o in data["organic"]]
                for o in data["organic"][: depth - len(results)]:
                    rank = len(results) + 1
                    absolute = rank + (o["featuresBefore"] if start == 0 else abs_offset)
                    results.append({"type": "organic", "organic_rank": rank, "absolute_rank": absolute, "url": o["url"]})
                if start == 0 and results:
                    abs_offset = results[-1]["absolute_rank"] - results[-1]["organic_rank"]
                if self.target and url_matches and any(
                        r["type"] == "organic" and url_matches(r["url"], *self.target) for r in results):
                    break  # سایت پیدا شد؛ خواندن صفحات بعدی لازم نیست
                if not data["organic"] or len(results) >= depth:
                    break
                time.sleep(random.uniform(*self.page_delay))
        finally:
            ctx.close()
        for f in features:
            results.append({"type": f, "organic_rank": None, "absolute_rank": None, "url": ""})
        return results, {"screenshot": shot, "engine": "browser", "proxy": bool(self.proxy), "organic_urls": found_urls[:depth]}

    def search(self, keyword, loc, lang, country, device, depth):
        loc = normalize_loc(loc)
        self._start()
        last = None
        tries = self.max_retries if self.proxy else 1  # بدون پراکسی، IP عوض نمی‌شود و تکرار فایده ندارد
        for i in range(tries):
            try:
                return self._search_once(keyword, loc, lang, country, device, depth)
            except CaptchaError as e:
                last = e
                time.sleep(2 + i * 3)
        raise RuntimeError(f"{last} — " + ("با چند IP مختلف هم کپچا آمد؛ فاصله بین جستجوها را بیشتر کنید."
                                            if self.proxy else
                                            "از پراکسی residential استفاده کنید یا فاصله بین جستجوها را بیشتر کنید."))
