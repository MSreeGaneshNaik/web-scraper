# Web Scraper — Project Documentation

A complete reference for the Web Scraper project: what it is, how it's built,
every feature, how to run it, performance, and where it can go next.

---

## 1. Overview

Give the tool a website URL and it crawls the whole site — fast and in parallel —
and extracts as much as it possibly can: visible content *and* the things normally
hidden from a casual scrape. It then presents the results two ways:

- a **structured JSON file** (machine-readable, contains everything found), and
- a **human-readable report** (Markdown / on-screen summary).

It runs two ways:

- **Web UI** — a minimalist single-page site (Claude-themed, animated background)
  at `http://127.0.0.1:8000`, with a live progress bar and a Stop button.
- **Command line** — `python scraper.py <url>` for scripting and automation.

Everything in the project is **free and open-source**. No API keys, no accounts,
no paid services or tiers.

---

## 2. The plan (how it came together)

1. **Core scraper** — one engine that crawls a site and produces structured data
   + a readable report.
2. **Scope & output** — crawl the *whole site* (follow internal links, with a page
   cap) and emit *both* JSON and a readable report.
3. **Web UI** — a Flask app + one HTML page to scrape from the browser.
4. **Refinement** — moved off port 5000 (macOS AirPlay conflict) to **8000**;
   redesigned in Claude's palette with a smooth animated background; made output
   far more readable (stat cards, per-page cards, chips).
5. **"Scrape everything"** — robots.txt capture, sitemap discovery (find unlinked
   pages), a categorized file catalog, capture of text-based files.
6. **Deeper scraping** — JavaScript rendering, background API capture, screenshots,
   PDF/Word/Excel text extraction, and a large set of "hidden in the HTML"
   extractors.
7. **Speed & control** — parallel fetching, connection pooling, retries; a live
   progress bar; an "All pages" option; a Stop button that keeps partial results;
   and a single-fetch robots.txt optimization.

---

## 3. Architecture & files

```
Web Scraper Project/
├── scraper.py          # crawl engine + extraction + report builder + CLI
├── app.py              # Flask web server (UI + JSON API + progress/stop + downloads)
├── templates/
│   └── index.html      # the single-page UI (Claude theme, animated bg, progress, stop)
├── render.py           # headless-browser rendering (Playwright)
├── documents.py        # PDF / Word / Excel text extraction
├── requirements.txt    # dependencies
├── README.md           # quick-start usage
└── document.md         # this file
```

**Data flow**

```
URL ─▶ crawl()                                   (scraper.py)
        ├─ fetch robots.txt ONCE → build rules + discover sitemaps
        ├─ seed queue from sitemap (even unlinked pages)
        ├─ CONCURRENT wave loop (ThreadPoolExecutor, continuous dispatch):
        │     keep N fetches in flight ─▶ fetch_one(url)
        │         requests.get → [optional] Playwright render → extract_page()
        │     ingest(result): merge page, catalog files, enqueue new links,
        │                     report progress; check stop flag
        ├─ extract text from cataloged PDFs/Docs in parallel (documents.py)
        └─▶ (pages, errors, domain, extras)
                ├─ build_report()  → Markdown report
                └─ JSON result
```

The CLI (`scraper.py main()`) and the web API (`app.py`) both call the same
`crawl()` and `build_report()` — one engine, two front doors. In the web app the
crawl runs in a **background thread** so the UI can poll progress and send a stop
signal while it runs.

---

## 4. Features (everything it extracts)

### Per page
- **Metadata** — title, language, canonical URL, every `<meta>` tag, description.
- **Headings** — h1 through h6.
- **Body text** — paragraphs, list items, full text, word count.
- **Top keywords** — word-frequency ranking (stopwords removed).
- **Links** — every link with anchor text and `rel` (collected from header, nav,
  footer, body — everywhere on the page).
- **Images** — src, alt, title.
- **Tables** — extracted as rows of cells.
- **Forms** — action, method, and all fields (including hidden field values).
- **Structured data** — JSON-LD blocks.
- **Hidden-in-HTML extras** — HTML comments, `data-*` attributes, `hreflang`
  alternate-language links, RSS/Atom feeds, favicon, web app manifest, external
  script sources.
- **Tech fingerprint** — detects frameworks/CMS/tools (WordPress, React, Next.js,
  Shopify, jQuery, Cloudflare, Google Analytics, etc.) from HTML, scripts, headers.
- **Social profiles** — Twitter/X, Facebook, Instagram, LinkedIn, YouTube, GitHub,
  TikTok, Reddit, Telegram, WhatsApp, Medium, Discord.
- **Contacts** — emails (from text + `mailto:` links) and phone numbers
  (validated with libphonenumber + `tel:` links, so dates/IDs aren't mistaken for
  phones).
- **HTTP response headers** — the full set per page.

### Contacts spreadsheet
A downloadable **CSV** (opens in Excel/Google Sheets) listing every contact with
full context so there's no ambiguity. Columns: **Type** (Email / Phone / Social
profile), **Value**, **Platform**, **Found on page**, **Page title**, **Site**,
**Link text / context** — one row per occurrence, so you always know which page a
contact came from.

### Site-wide
- **robots.txt** — fetched once and shown in full.
- **Sitemap discovery** — reads sitemaps declared in robots.txt and the
  conventional `/sitemap.xml`, recursing into sitemap *indexes*, to find every
  published page — including ones nothing links to.
- **File catalog** — every document, image, media, archive, data, and code file
  found, grouped by category.
- **Text files** — `.txt`, `.xml`, `.json`, etc. captured as readable content.
- **Document text** — actual text pulled out of PDF, Word, and Excel files.

### Deep / browser-powered (opt-in)
- **JavaScript rendering** — loads each page in a headless Chromium so content
  built by JS (React/Vue apps, infinite scroll, lazy loading) is captured.
  *(Verified: a JS-only page went 17 words static → 259 words rendered.)*
- **Background API capture** — logs the JSON/XHR/fetch calls a page makes.
- **Page screenshots** — full-page PNG of each rendered page, viewable in the UI.

### Speed, progress & control
- **Parallel fetching** — many pages fetched at once (continuous dispatch keeps all
  workers busy). ~3–4× faster than sequential.
- **Connection pooling + retries** — reused keep-alive connections; transient
  failures retried automatically.
- **Live progress bar** — shows the discovery phase, then real page counts, fills to
  100%, then disappears.
- **"All pages"** — crawl the entire site (no fixed page cap); the total resolves to
  the real discovered count rather than a placeholder.
- **Stop button** — halt mid-crawl and keep everything gathered so far (partial
  output + working downloads).
- **Politeness** — respects robots.txt by default; configurable delay (used in
  single-worker mode) and an Ignore-robots override.

---

## 5. Usage

### Web UI
```bash
pip install -r requirements.txt
python -m playwright install chromium     # only needed for JS rendering
python app.py
```
Open **http://127.0.0.1:8000**. Enter a URL, set options, click **Scrape**. Watch
the progress bar; click **Stop** any time to keep partial results. Download the
JSON and report with the buttons.

UI options: Max pages · All pages · Parallel (workers) · Delay · Crawl site ·
Ignore robots.txt · Render JS (browser) · Screenshots.

### Command line
```bash
python scraper.py https://example.com                       # crawl, default 50 pages
python scraper.py https://example.com --max-pages 500 --workers 16
python scraper.py https://example.com --no-crawl            # single page only
python scraper.py https://example.com --ignore-robots       # crawl everything
python scraper.py https://example.com --render --screenshots
python scraper.py https://example.com --workers 1 --delay 0.5   # slow & polite
python scraper.py https://example.com --no-docs             # skip PDF/Office text
python scraper.py https://example.com --out mysite          # mysite.json + mysite.md
```

### CLI options
| Flag | Default | Description |
|------|---------|-------------|
| `--max-pages` | 50 | Max pages to crawl |
| `--workers` | 8 | Parallel fetchers (forced to 1 with `--render`) |
| `--delay` | 0.0 | Seconds between requests; only applies with `--workers 1` |
| `--out` | output | Output filename prefix |
| `--no-crawl` | off | Only the given URL, don't follow links |
| `--ignore-robots` | off | Ignore robots.txt rules |
| `--render` | off | Render pages with a headless browser |
| `--screenshots` | off | Capture a screenshot of each rendered page |
| `--no-docs` | off | Don't extract text from PDF/Word/Excel |

---

## 6. Web API (for integrators)

`app.py` exposes:

- `GET /` — the UI page.
- `POST /api/scrape` — starts a crawl in the background. JSON body: `{url, max_pages,
  delay, workers, crawl, ignore_robots, render, screenshots}`. Returns `{started:true}`
  (or `409` if one is already running).
- `GET /api/progress` — `{running, done, total, current, error, stopped, summary}`.
  `total` is `0` while the page count is still being discovered; `summary` is filled
  in once finished.
- `POST /api/stop` — asks the running crawl to stop and keep partial results.
- `GET /download/json` — full JSON of the last crawl.
- `GET /download/report` — Markdown report of the last crawl.
- `GET /download/contacts` — the contacts spreadsheet (CSV).
- `GET /screenshot/<index>` — a captured screenshot as a PNG.

---

## 7. Performance

Parallel fetching with continuous dispatch is the main speed lever.

Benchmark — 40 pages of `books.toscrape.com`:

| Workers | Time | Speedup |
|---------|------|---------|
| 1 (sequential) | 24.0 s | — |
| 8 (default) | 7.8 s | **3.1×** |
| 16 | 6.3 s | **3.8×** |

**How many workers?** Output quality is independent of worker count — each page is
fetched and parsed in isolation and merged single-threaded (no races), so more
workers never corrupt a page's data. The practical limit is the *target server*
(too many requests → throttling/bans → dropped pages), not your machine.

- **Best, most-complete results:** 8–16 workers.
- **Max practical for plain crawling:** ~24–32 (UI cap is 32).
- **JS rendering:** forced to 1 (Playwright's engine isn't thread-safe, and each
  headless browser uses ~150–300 MB RAM).

Other speedups: pooled keep-alive connections, automatic retries, parallel document
extraction, and fetching robots.txt only once (shared by the rules parser and
sitemap discovery).

---

## 8. Dependencies (all free / OSS)

| Package | Purpose |
|---------|---------|
| `requests` | HTTP fetching (pooled session, retries) |
| `beautifulsoup4` + `lxml` | HTML & XML parsing |
| `flask` | web server / UI / API |
| `pypdf` | PDF text extraction |
| `python-docx` | Word (.docx) text extraction |
| `openpyxl` | Excel (.xlsx) text extraction |
| `playwright` (+ Chromium) | JavaScript rendering, API capture, screenshots |
| `phonenumbers` | Validate phone numbers (avoids dates/IDs as false positives) |

Standard-library otherwise: `concurrent.futures` (thread pool), `threading`
(background job + stop signal), `argparse`, `urllib`, `re`, `json`, …

---

## 9. Known cleanup (ponytail audit)

A lean-code audit flagged redundancy; status:

- ✅ **Done** — robots.txt was fetched 2–3×; now fetched once and reused (rules +
  sitemap), shaving ~1s off discovery.
- Pending (features unaffected, ~optional):
  - `paragraphs` / `list_items` duplicate `full_text` and are unread → could drop.
  - `SKIP_EXTENSIONS` can be derived from `RESOURCE_TYPES` instead of hand-listed.
  - `hidden_inputs` duplicates `forms[].fields` hidden values → could drop.
  - `phones` regex is noisy/low-value → could drop.
  - `page["has_screenshot"]` is set but never read → could drop.

---

## 10. Limitations

- **Auth-gated content** — pages behind a login aren't reached; credentials must be
  entered by the user, not the tool.
- **CAPTCHAs / bot protection** — not bypassed.
- **GIL on parsing** — HTML parsing is CPU work; beyond ~16–24 workers the gains
  taper off for parse-heavy sites.
- **Single-machine** — fine for local use; not a distributed crawler.
- **Flask dev server** — intended for local use, not production hosting.
- **Single active job** — one crawl at a time (a second start returns `409`).

---

## 11. Efficiency & real-world-readiness assessment

### Current efficiency
| Dimension | State | Notes |
|---|---|---|
| Crawl speed | Good | Concurrent (8 workers), continuous dispatch, ~3–4× vs sequential |
| Network | Good | Pooled keep-alive connections, auto-retries, robots fetched once |
| Extraction accuracy | Good | Per-page isolation; validated phones; `tel:`/`mailto:` links |
| Control / UX | Good | Live progress, stop-with-partial-output, all-pages, screenshots |
| CPU | Fair | HTML parsing is GIL-bound; gains taper past ~16–24 workers |

### Known gaps for production use
1. **Memory & scale** — every page's full data is held in RAM, then serialized to
   one large JSON. Big crawls (thousands of pages) can balloon memory; there is no
   streaming to disk.
2. **No persistence** — results live in memory (`LAST`); a restart loses them and
   only the most recent crawl is downloadable.
3. **Single job / single user** — one crawl at a time (`409` otherwise).
4. **Security (SSRF)** — it will fetch any URL, including `localhost`, internal IPs,
   and cloud metadata endpoints. Must be locked down before exposing to others.
5. **No per-domain rate limiting** — high concurrency can trigger throttling/bans
   that silently drop pages.
6. **Not a production server** — Flask dev server, single process, no auth.
7. **No tests** — no regression safety net.
8. **Robustness edges** — no max-response-size guard, no content-hash dedup of
   near-duplicate pages, basic encoding handling.

---

## 12. Future scope (prioritized roadmap)

### Tier 1 — robust & safe (highest impact)
- **Stream results to disk** (NDJSON per page + a SQLite index) instead of holding
  everything in RAM → crawl huge sites without running out of memory; enables resume.
- **SSRF guard** — block private/loopback/link-local IPs, cloud-metadata hosts, and
  non-`http(s)` schemes by default.
- **Per-domain rate limiter + adaptive backoff** on `429`/`503`; honor `Crawl-delay`.
- **Response-size cap** and **content-hash de-duplication** of near-identical pages.

### Tier 2 — make it a real service
- **Job queue with IDs** — multiple/concurrent crawls, each tracked and downloadable.
- **Persistent storage (DB)** so results survive restarts and past crawls can be
  browsed.
- **Production serving** (e.g. gunicorn) + optional API key / auth.

### Tier 3 — more useful
- **More exports** — CSV for links/images/files, NDJSON; push to Google Sheets,
  Notion, S3.
- **Scheduling + diff mode** — recurring crawls that report what changed.
- **Broken-link report** — verify every discovered link's status.
- **Sitemap-only fast mode**; **stealth rendering** for sites that block headless
  browsers; **per-site config profiles** (workers, delay, limits per domain).
- **Authenticated sessions** — accept a user-provided cookie/session for logged-in
  areas (user supplies the session, never their password).

### Tier 4 — quality & scale
- **Automated tests + CI**, type hints, structured logging/metrics.
- **Apply remaining ponytail cleanup** (§9).
- **Content analysis** — language detection, summarization, entity extraction.
- **Distributed crawling** — queue-backed workers for very large sites.

See §13 for a detailed breakdown of Tier 4.

---

## 13. Tier 4 in detail (quality & scale)

Notes on each Tier 4 item: what it means for this project, why it matters, rough
effort, and a recommendation.

### 13.1 Automated tests + CI — *highest-value Tier 4 item*
- **What:** a `pytest` suite over the pure functions most likely to break —
  `find_phones`, `contacts_csv`, `resource_category`, `same_domain` /
  `normalize_url`, `parse_sitemap`, and `extract_page` against **saved HTML
  fixtures** (not live sites, for speed and determinism). Plus a GitHub Actions
  workflow to run them on every push.
- **Why:** the engine has changed many times; each change can silently break
  something (e.g. the phone matcher that tagged dates as phone numbers — a test
  would have caught it immediately). Tests protect every later refactor.
- **Effort:** low–medium (~150 lines of tests + ~20-line CI file). `pytest` is a
  dev-only dependency.

### 13.2 Type hints
- **What:** annotate signatures (`crawl(...) -> tuple[list, list, str, dict]`),
  add `TypedDict`s for the page dict and `extras`, run `mypy`.
- **Why:** catches type/None bugs and documents the loosely-typed page/extras
  shapes.
- **Effort:** low but mechanical. Lower priority than tests; pairs well with them.

### 13.3 Structured logging / metrics
- **What:** replace `log()`-to-stderr with the stdlib `logging` module (levels,
  timestamps); emit per-crawl metrics — pages/sec, bytes downloaded, error
  breakdown by type, slowest pages.
- **Why:** turns "it's slow" into actionable detail (which hosts/pages were slow,
  what failed and why) instead of grepping `server.log`.
- **Effort:** low; all stdlib.

### 13.4 Apply remaining ponytail cleanup (§9)
- **What:** drop `paragraphs`/`list_items` (duplicate `full_text`), derive
  `SKIP_EXTENSIONS` from `RESOURCE_TYPES`, drop `hidden_inputs` and
  `has_screenshot` (~40 lines).
- **Why:** less code to maintain, smaller JSON output.
- **Effort:** low. Best done **after** tests exist, so refactors are safe.

### 13.5 Content analysis
- **What:** language detection, summarization, entity extraction (people/orgs/
  places).
- **Free-constraint note (this project uses no paid services / API keys):**
  - **Language detection:** free & local (`langdetect` / `fasttext`). Easy win.
  - **Entity extraction:** free & local (`spaCy` + a model, ~50 MB). Solid.
  - **Summarization:** the high-quality version (LLM) needs an API key and costs
    money — that would break the free-only rule. The free alternative is
    *extractive* summarization (TextRank via `sumy`): works, lower quality than an
    LLM. Recommend keeping it extractive unless an LLM is explicitly adopted later.
- **Effort:** low–medium per feature.

### 13.6 Distributed crawling — *likely YAGNI*
- **What:** multiple machines/workers pulling from a shared queue (Redis/Celery)
  for very large (million-page) crawls.
- **Recommendation:** **park indefinitely.** The in-process thread pool already
  handles thousands of pages on a single capable machine; distributed crawling
  adds a broker, serialization, and cross-node dedup for a scale this project is
  unlikely to hit. Revisit only with a concrete need.

### Recommended order within Tier 4
1. Tests + CI (foundation that makes everything else safe)
2. Ponytail cleanup (under test coverage)
3. Structured logging / metrics (cheap visibility)
4. Language detection + entity extraction (free, high "useful" value)
5. Type hints (mechanical, ongoing)
6. ~~Distributed crawling~~ (skip for now)

---

*Last updated: 2026-06-28.*
