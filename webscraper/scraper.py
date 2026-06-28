#!/usr/bin/env python3
"""
Whole-site web scraper.

Give it a URL and it crawls the site (following internal links, up to a page
limit), extracting as much as possible from each page, then writes two outputs:

  - a structured JSON file  (machine-readable, everything it found)
  - a readable Markdown report (human-friendly summary)

Usage:
    python scraper.py https://example.com
    python scraper.py https://example.com --max-pages 100 --delay 0.5 --out output
    python scraper.py https://example.com --no-crawl        # single page only

Respects robots.txt by default (use --ignore-robots to override).
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit(
        "Missing dependencies. Install them first:\n"
        "    pip install -r requirements.txt"
    )

from .documents import extract_document
from .render import RenderSession, is_available as render_available

USER_AGENT = (
    "Mozilla/5.0 (compatible; SiteScraper/1.0; +https://example.com/bot)"
)

# File extensions we don't try to parse as HTML pages.
SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".pdf", ".zip", ".rar", ".gz", ".tar", ".7z",
    ".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".wav",
    ".css", ".js", ".json", ".xml", ".rss",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".exe", ".dmg", ".iso",
}

# Group downloadable resources by category for the catalog.
RESOURCE_TYPES = {
    "documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                  ".csv", ".txt", ".rtf", ".odt"},
    "images":    {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
                  ".bmp", ".tiff"},
    "media":     {".mp3", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm",
                  ".wav", ".m4a", ".ogg"},
    "archives":  {".zip", ".rar", ".gz", ".tar", ".7z", ".dmg", ".iso"},
    "data":      {".json", ".xml", ".rss", ".yaml", ".yml"},
    "code":      {".css", ".js"},
}

# Content types we can store as readable text even when they aren't HTML.
TEXTUAL_HINTS = ("text/", "xml", "json", "javascript", "csv")


def resource_category(url):
    """Return the catalog category for a URL, or None if it's a normal page."""
    ext = os.path.splitext(urlparse(url).path.lower())[1]
    for category, exts in RESOURCE_TYPES.items():
        if ext in exts:
            return category
    return None


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def normalize_url(url):
    """Strip fragments and trailing slashes so we don't visit dupes."""
    url, _ = urldefrag(url)
    if url.endswith("/") and len(urlparse(url).path) > 1:
        url = url.rstrip("/")
    return url


def same_domain(url, base_netloc):
    netloc = urlparse(url).netloc.lower()
    base = base_netloc.lower()
    # Treat www and non-www as the same site.
    return netloc.lstrip("www.") == base.lstrip("www.")


def looks_like_page(url):
    path = urlparse(url).path.lower()
    for ext in SKIP_EXTENSIONS:
        if path.endswith(ext):
            return False
    return True


def clean_text(text):
    return re.sub(r"\s+", " ", text or "").strip()


def build_robots(robots_txt, ignore):
    """Build a RobotFileParser from already-fetched text (no extra request).
    Returns None when robots are ignored or no robots.txt was found."""
    if ignore or not robots_txt:
        return None
    rp = RobotFileParser()
    rp.parse(robots_txt.splitlines())
    return rp


# Common English words to ignore when ranking keywords.
STOPWORDS = set("""a an and are as at be by for from has have he in is it its of on
that the to was were will with this his her she they you your our we us i or but not
can all any your their them then than so if no do does did had what which who when
where why how about more most other some such only own same very just also into out
up down over under again further once here there our ours""".split())

# Detect social profiles by the domains they live on.
SOCIAL_DOMAINS = {
    "twitter.com": "Twitter/X", "x.com": "Twitter/X",
    "facebook.com": "Facebook", "instagram.com": "Instagram",
    "linkedin.com": "LinkedIn", "youtube.com": "YouTube",
    "github.com": "GitHub", "tiktok.com": "TikTok",
    "pinterest.com": "Pinterest", "reddit.com": "Reddit",
    "t.me": "Telegram", "wa.me": "WhatsApp", "medium.com": "Medium",
    "discord.gg": "Discord", "discord.com": "Discord",
}

# Map fingerprint hints (found in HTML/scripts) to technologies.
TECH_HINTS = {
    "wp-content": "WordPress", "wp-includes": "WordPress",
    "/_next/": "Next.js", "__NEXT_DATA__": "Next.js",
    "/_nuxt/": "Nuxt.js", "data-reactroot": "React", "react": "React",
    "ng-version": "Angular", "vue": "Vue.js", "svelte": "Svelte",
    "shopify": "Shopify", "wix.com": "Wix", "squarespace": "Squarespace",
    "drupal": "Drupal", "joomla": "Joomla", "gatsby": "Gatsby",
    "cloudflare": "Cloudflare", "jquery": "jQuery", "bootstrap": "Bootstrap",
    "tailwind": "Tailwind CSS", "googletagmanager": "Google Tag Manager",
    "google-analytics": "Google Analytics", "hubspot": "HubSpot",
}


try:
    import phonenumbers
except ImportError:
    phonenumbers = None


def find_phones(text):
    """Find real phone numbers in free text using libphonenumber, which
    validates them — so dates, IDs and number sequences aren't mistaken
    for phones. Only matches that carry a country code (e.g. +1 …) are
    accepted, for high precision. Returns formatted international numbers."""
    if phonenumbers is None:
        return []
    out = set()
    try:
        for m in phonenumbers.PhoneNumberMatcher(text, None):  # None = needs +cc
            if phonenumbers.is_valid_number(m.number):
                out.add(phonenumbers.format_number(
                    m.number, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
    except Exception:
        pass
    return sorted(out)


def normalise_tel(raw):
    """Format a tel: link number to international form when valid."""
    if phonenumbers is not None:
        try:
            n = phonenumbers.parse(raw, None)
            if phonenumbers.is_valid_number(n):
                return phonenumbers.format_number(
                    n, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        except Exception:
            pass
    return raw.strip()


def top_keywords(text, n=15):
    words = re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}", text.lower())
    counts = {}
    for w in words:
        if w in STOPWORDS:
            continue
        counts[w] = counts.get(w, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [{"word": w, "count": c} for w, c in ranked[:n]]


def detect_tech(html, scripts, meta, headers):
    found = set()
    hay = (html[:50000] + " " + " ".join(scripts) + " "
           + " ".join(f"{k}={v}" for k, v in headers.items())).lower()
    for hint, tech in TECH_HINTS.items():
        if hint in hay:
            found.add(tech)
    gen = meta.get("generator")
    if gen:
        found.add(gen)
    server = headers.get("Server") or headers.get("server")
    if server:
        found.add(f"Server: {server}")
    powered = headers.get("X-Powered-By") or headers.get("x-powered-by")
    if powered:
        found.add(f"X-Powered-By: {powered}")
    return sorted(found)


def extract_page(url, html, status_code, content_type, elapsed, headers=None):
    """Pull everything we can out of one HTML page into a dict."""
    headers = headers or {}
    soup = BeautifulSoup(html, "lxml")

    # --- Metadata ---
    title = clean_text(soup.title.string) if soup.title else None

    meta = {}
    for tag in soup.find_all("meta"):
        name = tag.get("name") or tag.get("property") or tag.get("http-equiv")
        content = tag.get("content")
        if name and content:
            meta[name.strip().lower()] = clean_text(content)

    canonical = None
    link_canonical = soup.find("link", rel="canonical")
    if link_canonical and link_canonical.get("href"):
        canonical = urljoin(url, link_canonical["href"])

    lang = None
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        lang = html_tag["lang"].strip()

    # --- <link> relations: hreflang, feeds, favicon, manifest ---
    hreflang = []
    feeds = []
    favicon = None
    manifest = None
    for link in soup.find_all("link", href=True):
        rels = [r.lower() for r in link.get("rel", [])]
        href = urljoin(url, link["href"].strip())
        ltype = (link.get("type") or "").lower()
        if link.get("hreflang"):
            hreflang.append({"lang": link["hreflang"], "url": href})
        if "alternate" in rels and ("rss" in ltype or "atom" in ltype):
            feeds.append({"title": link.get("title"), "url": href, "type": ltype})
        if "icon" in " ".join(rels) and not favicon:
            favicon = href
        if "manifest" in rels:
            manifest = href

    # --- Scripts (capture before we strip them) ---
    script_srcs = []
    for s in soup.find_all("script"):
        if s.get("src"):
            script_srcs.append(urljoin(url, s["src"].strip()))

    # --- Structured data (JSON-LD) — also before stripping scripts ---
    json_ld = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            json_ld.append(json.loads(script.string))
        except Exception:
            pass

    # --- HTML comments (often hold dev notes / hidden markup) ---
    from bs4 import Comment
    comments = [clean_text(c) for c in soup.find_all(string=lambda t: isinstance(t, Comment))]
    comments = [c for c in comments if c and len(c) > 2][:50]

    # --- Forms and their fields ---
    forms = []
    for form in soup.find_all("form"):
        fields = []
        for inp in form.find_all(["input", "select", "textarea"]):
            fields.append({
                "tag": inp.name,
                "type": inp.get("type"),
                "name": inp.get("name"),
                "value": inp.get("value") if inp.get("type") == "hidden" else None,
            })
        forms.append({
            "action": urljoin(url, form.get("action", "")) if form.get("action") else None,
            "method": (form.get("method") or "get").lower(),
            "fields": fields,
        })

    # --- Hidden form inputs across the page ---
    hidden_inputs = [
        {"name": i.get("name"), "value": i.get("value")}
        for i in soup.find_all("input", type="hidden") if i.get("name")
    ]

    # --- data-* attributes (sample of distinct names) ---
    data_attrs = {}
    for el in soup.find_all(True):
        for attr, val in el.attrs.items():
            if attr.startswith("data-") and attr not in data_attrs:
                v = val if isinstance(val, str) else " ".join(val)
                data_attrs[attr] = clean_text(v)[:120]
    data_attrs = dict(list(data_attrs.items())[:60])

    # --- Tech fingerprint (uses raw html + scripts + headers) ---
    tech = detect_tech(html, script_srcs, meta, headers)

    # --- Headings ---
    headings = {f"h{i}": [] for i in range(1, 7)}
    for i in range(1, 7):
        for h in soup.find_all(f"h{i}"):
            t = clean_text(h.get_text())
            if t:
                headings[f"h{i}"].append(t)

    # --- Body text (strip scripts/styles now) ---
    for junk in soup(["script", "style", "noscript"]):
        junk.extract()

    paragraphs = [clean_text(p.get_text()) for p in soup.find_all("p")]
    paragraphs = [p for p in paragraphs if p]

    list_items = [clean_text(li.get_text()) for li in soup.find_all("li")]
    list_items = [li for li in list_items if li]

    full_text = clean_text(soup.get_text(separator=" "))
    word_count = len(full_text.split())

    # --- Links (+ split internal/external, flag nofollow) ---
    links = []
    social = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"].strip())
        rel = " ".join(a.get("rel", [])) or None
        links.append({
            "url": href,
            "text": clean_text(a.get_text()),
            "rel": rel,
        })
        host = urlparse(href).netloc.lower().lstrip("www.")
        for dom, name in SOCIAL_DOMAINS.items():
            if host == dom or host.endswith("." + dom):
                social.setdefault(name, href)

    # --- Images ---
    images = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src:
            images.append({
                "src": urljoin(url, src.strip()),
                "alt": clean_text(img.get("alt")),
                "title": clean_text(img.get("title")),
            })

    # --- Tables ---
    tables = []
    for table in soup.find_all("table"):
        rows = []
        for tr in table.find_all("tr"):
            cells = [clean_text(td.get_text())
                     for td in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)
        if rows:
            tables.append(rows)

    # --- Emails & phone numbers found in text ---
    # Authoritative contacts declared in mailto:/tel: links.
    link_emails, tel_phones = set(), set()
    for l in links:
        low = l["url"].lower()
        if low.startswith("mailto:"):
            addr = l["url"][7:].split("?")[0].strip()
            if "@" in addr:
                link_emails.add(addr)
        elif low.startswith("tel:"):
            num = l["url"][4:].strip()
            if num:
                tel_phones.add(normalise_tel(num))

    emails = sorted(set(re.findall(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", full_text))
        | link_emails)
    phones = sorted(set(find_phones(full_text)) | tel_phones)

    return {
        "url": url,
        "status_code": status_code,
        "content_type": content_type,
        "fetch_seconds": round(elapsed, 3),
        "title": title,
        "lang": lang,
        "canonical": canonical,
        "meta": meta,
        "description": meta.get("description"),
        "headings": headings,
        "word_count": word_count,
        "keywords": top_keywords(full_text),
        "paragraphs": paragraphs,
        "list_items": list_items,
        "links": links,
        "images": images,
        "tables": tables,
        "forms": forms,
        "hidden_inputs": hidden_inputs,
        "data_attributes": data_attrs,
        "comments": comments,
        "script_srcs": script_srcs,
        "json_ld": json_ld,
        "hreflang": hreflang,
        "feeds": feeds,
        "favicon": favicon,
        "manifest": manifest,
        "tech": tech,
        "social": social,
        "response_headers": dict(headers),
        "emails": emails,
        "phones": phones,
        "text": full_text,
    }


def fetch_robots_and_sitemaps(session, base_url):
    """Grab robots.txt content and any sitemap URLs it declares."""
    parsed = urlparse(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    robots_txt = None
    sitemap_urls = []
    try:
        r = session.get(root + "/robots.txt", timeout=15)
        if r.status_code == 200 and r.text.strip():
            robots_txt = r.text
            for line in r.text.splitlines():
                if line.lower().strip().startswith("sitemap:"):
                    sitemap_urls.append(line.split(":", 1)[1].strip())
            log(f"[robots] fetched robots.txt ({len(robots_txt)} chars, "
                f"{len(sitemap_urls)} sitemaps declared)")
    except requests.RequestException as e:
        log(f"[robots] could not fetch robots.txt ({e})")
    if not sitemap_urls:
        sitemap_urls.append(root + "/sitemap.xml")  # try the conventional path
    return robots_txt, sitemap_urls


def parse_sitemap(session, url, seen=None, depth=0):
    """Return all page URLs from a sitemap, recursing into sitemap indexes."""
    if seen is None:
        seen = set()
    if url in seen or depth > 4:
        return []
    seen.add(url)
    urls = []
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            return urls
        soup = BeautifulSoup(r.text, "xml")
        # A sitemap index points to more sitemaps.
        for sm in soup.find_all("sitemap"):
            loc = sm.find("loc")
            if loc and loc.get_text().strip():
                urls += parse_sitemap(session, loc.get_text().strip(), seen, depth + 1)
        # A regular sitemap lists pages.
        for u in soup.find_all("url"):
            loc = u.find("loc")
            if loc and loc.get_text().strip():
                urls.append(loc.get_text().strip())
    except Exception as e:
        log(f"[sitemap] {url} -> {e}")
    return urls


class RateLimiter:
    """Per-host request spacing with adaptive backoff.

    Keeps a minimum interval between requests to each host (e.g. from
    robots.txt Crawl-delay). When a host returns 429/503 it widens that
    host's interval (exponential backoff); successes slowly relax it.
    Thread-safe: workers call wait() before each request.
    """

    def __init__(self, base_interval=0.0, max_penalty=30.0):
        self.base = base_interval
        self.max_penalty = max_penalty
        self._lock = threading.Lock()
        self._next = {}       # host -> earliest next allowed timestamp
        self._penalty = {}    # host -> extra seconds added by backoff

    def wait(self, host):
        with self._lock:
            now = time.time()
            interval = self.base + self._penalty.get(host, 0.0)
            start_at = max(now, self._next.get(host, 0.0))
            self._next[host] = start_at + interval
        delay = start_at - time.time()
        if delay > 0:
            time.sleep(delay)

    def penalize(self, host):
        with self._lock:
            cur = self._penalty.get(host, 0.0)
            self._penalty[host] = min(cur * 2 if cur else 0.5, self.max_penalty)
            return self._penalty[host]

    def relax(self, host):
        with self._lock:
            if host in self._penalty:
                self._penalty[host] *= 0.5
                if self._penalty[host] < 0.1:
                    del self._penalty[host]


def crawl(start_url, max_pages, delay, crawl_site, ignore_robots,
          use_sitemap=True, render=False, screenshots=False,
          extract_docs=True, max_docs=20, progress_cb=None, workers=8,
          stop_flag=None):
    def stopped():
        return stop_flag is not None and stop_flag.is_set()
    start_url = normalize_url(start_url)
    base_netloc = urlparse(start_url).netloc
    if not base_netloc:
        sys.exit(f"Invalid URL: {start_url!r}")

    workers = max(1, int(workers))

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    # Pool connections and retry transient failures for speed + resilience.
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=workers, pool_maxsize=max(workers * 2, 10),
        max_retries=2)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    queue = deque([start_url])
    seen = {start_url}
    pages = []
    errors = []
    resources = {}        # category -> set of URLs (every file we spotted)
    text_files = []       # readable non-HTML files we fetched (robots, .txt, .xml…)
    screenshots_out = []  # {url, image(base64 png)} when screenshots are on
    content_hashes = set()  # for de-duplicating identical page content

    # Optional headless browser for JavaScript rendering.
    renderer = None
    if render:
        if not render_available():
            log("[render] Playwright not installed; falling back to static HTML")
        else:
            renderer = RenderSession(screenshots=screenshots)
            renderer.__enter__()
            log("[render] headless browser started")

    # 1) Fetch robots.txt once, then reuse it for both the rules parser and
    #    sitemap discovery (no redundant requests).
    if progress_cb:
        progress_cb(0, 0, "Reading robots.txt & sitemaps…")
    robots_txt, sitemap_urls = fetch_robots_and_sitemaps(session, start_url)
    rp = build_robots(robots_txt, ignore_robots)

    # Per-host rate limiter. Honor robots.txt Crawl-delay (capped) if present;
    # otherwise no artificial spacing — adaptive backoff still kicks in on 429/503.
    crawl_delay = 0.0
    if rp is not None:
        try:
            cd = rp.crawl_delay(USER_AGENT)
            if cd:
                crawl_delay = min(float(cd), 10.0)
        except Exception:
            crawl_delay = 0.0
    limiter = RateLimiter(base_interval=crawl_delay)
    if crawl_delay:
        log(f"[rate] honoring robots Crawl-delay: {crawl_delay}s per host")

    # 2) Seed the queue with every page the sitemap publishes (even unlinked ones).
    sitemaps_info = []
    if crawl_site and use_sitemap:
        if progress_cb:
            progress_cb(0, 0, "Discovering pages from sitemap…")
        sm_seen = set()
        for sm in sitemap_urls:
            if stopped():
                break
            found = parse_sitemap(session, sm, sm_seen)
            if found:
                sitemaps_info.append({"url": sm, "url_count": len(found)})
                log(f"[sitemap] {sm} -> {len(found)} URLs")
            for u in found:
                nu = normalize_url(u)
                if (nu not in seen and same_domain(nu, base_netloc)
                        and urlparse(nu).scheme in ("http", "https")):
                    seen.add(nu)
                    if looks_like_page(nu):
                        queue.append(nu)
                    else:
                        cat = resource_category(nu)
                        if cat:
                            resources.setdefault(cat, set()).add(nu)

    def catalog(url):
        cat = resource_category(url)
        if cat:
            resources.setdefault(cat, set()).add(url)

    # Rendering uses Playwright's sync API, which isn't thread-safe — go serial.
    if renderer:
        workers = 1

    def fetch_one(url):
        """Fetch (and for static pages, parse) one URL. No shared state — safe
        to run in a worker thread. Returns a result dict for ingest()."""
        host = urlparse(url).netloc
        resp = None
        # Up to 3 attempts, honoring per-host spacing and backing off on 429/503.
        for attempt in range(3):
            limiter.wait(host)
            try:
                t0 = time.time()
                resp = session.get(url, timeout=20, allow_redirects=True)
                elapsed = time.time() - t0
            except requests.RequestException as e:
                return {"kind": "error", "url": url, "error": str(e)}
            if resp.status_code in (429, 503) and attempt < 2:
                pen = limiter.penalize(host)
                log(f"[rate] {resp.status_code} from {host}; backing off "
                    f"{pen:.1f}s (attempt {attempt + 1})")
                time.sleep(pen)
                continue
            break
        limiter.relax(host)

        ctype = resp.headers.get("Content-Type", "")
        if "html" not in ctype.lower():
            if any(h in ctype.lower() for h in TEXTUAL_HINTS):
                return {"kind": "text", "url": url, "content_type": ctype,
                        "size": len(resp.text), "text": resp.text[:20000]}
            return {"kind": "nonhtml", "url": url, "content_type": ctype}

        html_text = resp.text
        api_calls = []
        has_shot = False
        if renderer:  # serial path only
            try:
                rd = renderer.fetch(url)
                if rd["html"]:
                    html_text = rd["html"]
                api_calls = rd["api_calls"]
                if rd["screenshot"]:
                    screenshots_out.append({"url": url, "image": rd["screenshot"]})
                    has_shot = True
            except Exception as e:
                log(f"[render] {url} -> {e}")

        page = extract_page(url, html_text, resp.status_code, ctype,
                            elapsed, headers=resp.headers)
        page["rendered"] = bool(renderer)
        page["api_calls"] = api_calls
        page["has_screenshot"] = has_shot
        return {"kind": "page", "page": page}

    def ingest(r):
        """Merge a worker result into shared state (runs in the main thread)."""
        kind = r["kind"]
        if kind == "error":
            log(f"[error] {r['url']} -> {r['error']}")
            errors.append({"url": r["url"], "error": r["error"]})
            return
        if kind == "text":
            text_files.append({k: r[k] for k in
                               ("url", "content_type", "size", "text")})
            return
        if kind == "nonhtml":
            catalog(r["url"])
            errors.append({"url": r["url"],
                           "error": f"non-HTML ({r['content_type']})"})
            return
        page = r["page"]
        # De-duplicate identical page content reached via different URLs.
        # Only for substantial pages, so distinct thin pages aren't collapsed.
        if page["word_count"] > 50:
            h = hashlib.md5(page["text"].encode("utf-8", "ignore")).hexdigest()
            if h in content_hashes:
                log(f"[dup] identical content, skipped {page['url']}")
                return
            content_hashes.add(h)
        pages.append(page)
        log(f"[{len(pages)}/{max_pages}] {page['status_code']} {page['url']}")
        for link in page["links"]:
            catalog(link["url"])
        for img in page["images"]:
            resources.setdefault("images", set()).add(img["src"])
        if crawl_site:
            for link in page["links"]:
                nxt = normalize_url(link["url"])
                if (nxt not in seen
                        and same_domain(nxt, base_netloc)
                        and looks_like_page(nxt)
                        and urlparse(nxt).scheme in ("http", "https")):
                    seen.add(nxt)
                    queue.append(nxt)
        if progress_cb:
            known = min(max_pages, len(pages) + len(queue))
            progress_cb(len(pages), known, page["url"])

    # Report the real number of pages found before crawling begins.
    if progress_cb:
        found = min(max_pages, len(queue))
        progress_cb(0, found, f"Found {found} pages, crawling…")

    # Crawl with continuous dispatch: keep up to `workers` fetches in flight at
    # all times, and as each finishes, merge it and immediately submit more.
    from concurrent.futures import FIRST_COMPLETED, wait

    def next_url():
        while queue:
            u = queue.popleft()
            if rp is not None and not rp.can_fetch(USER_AGENT, u):
                log(f"[skip] robots disallows {u} (use ignore_robots)")
                continue
            return u
        return None

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            in_flight = set()

            def fill():
                while (len(in_flight) < workers
                       and len(pages) + len(in_flight) < max_pages):
                    u = next_url()
                    if u is None:
                        break
                    in_flight.add(ex.submit(fetch_one, u))

            fill()
            while in_flight and len(pages) < max_pages and not stopped():
                done, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
                in_flight = set(in_flight)
                for fut in done:
                    ingest(fut.result())
                    if delay and workers == 1:
                        time.sleep(delay)
                if stopped():
                    break
                fill()
            if stopped():
                log(f"[stop] stopped by user after {len(pages)} pages")
    finally:
        if renderer:
            renderer.__exit__()
            log("[render] headless browser closed")

    # 3) Pull readable text out of the documents we cataloged (PDF/Word/Excel),
    #    in parallel.
    documents = []
    if extract_docs and not stopped():
        docs = [d for d in (resources.get("documents") or [])
                if d.lower().endswith((".pdf", ".docx", ".xlsx"))][:max_docs]
        if docs and progress_cb:
            progress_cb(len(pages), len(pages),
                        f"Extracting text from {len(docs)} documents…")

        def grab_doc(durl):
            try:
                dr = session.get(durl, timeout=25)
                text = extract_document(durl, dr.content,
                                        dr.headers.get("Content-Type", ""))
                if text:
                    log(f"[doc] extracted {len(text)} chars from {durl}")
                    return {"url": durl, "chars": len(text), "text": text[:20000]}
            except Exception as e:
                log(f"[doc] {durl} -> {e}")
            return None

        if docs:
            with ThreadPoolExecutor(max_workers=min(workers, len(docs))) as dex:
                documents = [d for d in dex.map(grab_doc, docs) if d]

    extras = {
        "robots_txt": robots_txt,
        "sitemaps": sitemaps_info,
        "text_files": text_files,
        "documents": documents,
        "screenshots": screenshots_out,
        "resources": {k: sorted(v) for k, v in sorted(resources.items())},
    }
    return pages, errors, base_netloc, extras


def contacts_csv(pages, domain):
    """Build a detailed contacts spreadsheet (CSV) from the crawled pages.

    One row per contact occurrence, with the page it was found on, so it's
    unambiguous where every email / phone / social profile came from.
    Returns (csv_text, row_count).
    """
    headers = ["Type", "Value", "Platform", "Found on page",
               "Page title", "Site", "Link text / context"]
    rows = []
    seen = set()

    for p in pages:
        purl = p.get("url", "")
        ptitle = p.get("title") or ""
        # Map a link URL -> its anchor text, for social-profile context.
        link_text = {}
        for l in p.get("links") or []:
            link_text.setdefault(l.get("url"), l.get("text") or "")

        for email in p.get("emails") or []:
            key = ("Email", email, purl)
            if key in seen:
                continue
            seen.add(key)
            rows.append(["Email", email, "", purl, ptitle, domain, ""])

        for phone in p.get("phones") or []:
            key = ("Phone", phone, purl)
            if key in seen:
                continue
            seen.add(key)
            rows.append(["Phone", phone, "", purl, ptitle, domain, ""])

        for platform, link in (p.get("social") or {}).items():
            key = ("Social", link, purl)
            if key in seen:
                continue
            seen.add(key)
            rows.append(["Social profile", link, platform, purl, ptitle,
                         domain, link_text.get(link, "")])

    # Sort for a tidy sheet: by type, then value.
    rows.sort(key=lambda r: (r[0], r[1].lower()))

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    w.writerows(rows)
    return buf.getvalue(), len(rows)


def build_report(pages, errors, base_netloc, start_url, extras=None):
    """Build a human-readable Markdown report from the crawl results."""
    extras = extras or {}
    total_words = sum(p["word_count"] for p in pages)
    all_links = set()
    external_links = set()
    all_images = set()
    all_emails = set()
    for p in pages:
        for l in p["links"]:
            all_links.add(l["url"])
            if not same_domain(l["url"], base_netloc):
                external_links.add(l["url"])
        for img in p["images"]:
            all_images.add(img["src"])
        all_emails.update(p["emails"])

    lines = []
    lines.append(f"# Scrape Report: {base_netloc}\n")
    lines.append(f"- **Start URL:** {start_url}")
    lines.append(f"- **Generated:** {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"- **Pages scraped:** {len(pages)}")
    lines.append(f"- **Errors:** {len(errors)}")
    lines.append(f"- **Total words:** {total_words:,}")
    lines.append(f"- **Unique links found:** {len(all_links)} "
                 f"({len(external_links)} external)")
    lines.append(f"- **Unique images:** {len(all_images)}")
    if all_emails:
        lines.append(f"- **Emails found:** {', '.join(sorted(all_emails))}")
    lines.append("")

    lines.append("## Pages\n")
    for i, p in enumerate(pages, 1):
        lines.append(f"### {i}. {p['title'] or '(no title)'}")
        lines.append(f"- URL: {p['url']}")
        lines.append(f"- Status: {p['status_code']} | "
                     f"Words: {p['word_count']:,} | "
                     f"Links: {len(p['links'])} | "
                     f"Images: {len(p['images'])}")
        if p["description"]:
            lines.append(f"- Description: {p['description']}")
        if p["headings"]["h1"]:
            lines.append(f"- H1: {'; '.join(p['headings']['h1'])}")
        if p["headings"]["h2"]:
            h2 = p["headings"]["h2"][:10]
            lines.append(f"- H2: {'; '.join(h2)}")
        if p["tables"]:
            lines.append(f"- Tables: {len(p['tables'])}")
        if p.get("forms"):
            lines.append(f"- Forms: {len(p['forms'])}")
        if p.get("tech"):
            lines.append(f"- Tech: {', '.join(p['tech'])}")
        if p.get("social"):
            lines.append("- Social: " + ", ".join(
                f"{k} ({v})" for k, v in p["social"].items()))
        if p.get("keywords"):
            lines.append("- Top keywords: " + ", ".join(
                f"{k['word']}×{k['count']}" for k in p["keywords"][:10]))
        if p.get("api_calls"):
            lines.append(f"- Background API calls captured: {len(p['api_calls'])}")
        if p["emails"]:
            lines.append(f"- Emails: {', '.join(p['emails'])}")
        # A short text preview.
        preview = p["text"][:300]
        if preview:
            lines.append(f"\n> {preview}{'...' if len(p['text']) > 300 else ''}\n")
        lines.append("")

    # --- Resource catalog ---
    resources = extras.get("resources") or {}
    if resources:
        total = sum(len(v) for v in resources.values())
        lines.append(f"## Files & resources found ({total})\n")
        for cat, urls in resources.items():
            lines.append(f"### {cat.title()} ({len(urls)})")
            for u in urls[:50]:
                lines.append(f"- {u}")
            if len(urls) > 50:
                lines.append(f"- …and {len(urls) - 50} more")
            lines.append("")

    # --- Document text (PDF/Word/Excel) ---
    documents = extras.get("documents") or []
    if documents:
        lines.append(f"## Document text extracted ({len(documents)})\n")
        for doc in documents:
            lines.append(f"### {doc['url']} ({doc['chars']:,} chars)")
            lines.append(f"\n> {doc['text'][:500]}…\n")
        lines.append("")

    # --- Text files (robots.txt, .txt, .xml, …) ---
    text_files = extras.get("text_files") or []
    if text_files:
        lines.append(f"## Text files captured ({len(text_files)})\n")
        for tf in text_files:
            lines.append(f"- {tf['url']} ({tf['content_type']}, {tf['size']} bytes)")
        lines.append("")

    # --- robots.txt ---
    if extras.get("robots_txt"):
        lines.append("## robots.txt\n")
        lines.append("```")
        lines.append(extras["robots_txt"].strip())
        lines.append("```\n")

    # --- Sitemaps ---
    if extras.get("sitemaps"):
        lines.append("## Sitemaps\n")
        for sm in extras["sitemaps"]:
            lines.append(f"- {sm['url']} — {sm['url_count']} URLs")
        lines.append("")

    if errors:
        lines.append("## Errors\n")
        for e in errors:
            lines.append(f"- {e['url']} — {e['error']}")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Crawl a website and scrape everything into JSON + a report."
    )
    parser.add_argument("url", help="Starting URL, e.g. https://example.com")
    parser.add_argument("--max-pages", type=int, default=50,
                        help="Max pages to crawl (default: 50)")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Seconds between requests; only applies with --workers 1")
    parser.add_argument("--workers", type=int, default=8,
                        help="Parallel fetchers (default: 8; forced to 1 with --render)")
    parser.add_argument("--out", default="output",
                        help="Output filename prefix (default: output)")
    parser.add_argument("--no-crawl", action="store_true",
                        help="Scrape only the given URL, don't follow links")
    parser.add_argument("--ignore-robots", action="store_true",
                        help="Ignore robots.txt rules")
    parser.add_argument("--render", action="store_true",
                        help="Render pages with a headless browser (JS sites)")
    parser.add_argument("--screenshots", action="store_true",
                        help="Capture a screenshot of each rendered page")
    parser.add_argument("--no-docs", action="store_true",
                        help="Don't extract text from PDF/Word/Excel files")
    args = parser.parse_args()

    url = args.url
    if not urlparse(url).scheme:
        url = "https://" + url

    log(f"Starting crawl at {url} (max {args.max_pages} pages)\n")
    start = time.time()
    pages, errors, base_netloc, extras = crawl(
        url, args.max_pages, args.delay,
        crawl_site=not args.no_crawl,
        ignore_robots=args.ignore_robots,
        render=args.render,
        screenshots=args.screenshots,
        extract_docs=not args.no_docs,
        workers=args.workers,
    )
    duration = time.time() - start

    if not pages:
        log("\nNo pages scraped. Check the URL or try --ignore-robots.")
        sys.exit(1)

    result = {
        "start_url": url,
        "domain": base_netloc,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "crawl_seconds": round(duration, 2),
        "pages_scraped": len(pages),
        "errors_count": len(errors),
        "robots_txt": extras["robots_txt"],
        "sitemaps": extras["sitemaps"],
        "resources": extras["resources"],
        "text_files": extras["text_files"],
        "documents": extras["documents"],
        "screenshots": extras["screenshots"],
        "pages": pages,
        "errors": errors,
    }

    json_path = f"{args.out}.json"
    report_path = f"{args.out}.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(build_report(pages, errors, base_netloc, url, extras))

    contacts_path = f"{args.out}-contacts.csv"
    csv_text, contact_rows = contacts_csv(pages, base_netloc)
    with open(contacts_path, "w", encoding="utf-8", newline="") as f:
        f.write(csv_text)

    log(f"\nDone in {duration:.1f}s.")
    log(f"  JSON data:   {json_path}")
    log(f"  Report:      {report_path}")
    log(f"  Contacts:    {contacts_path} ({contact_rows} rows)")
    log(f"  Pages:       {len(pages)} | Errors: {len(errors)}")


if __name__ == "__main__":
    main()
