# 🕷️ Web Scraper

> ⚡ Give it a website URL and it crawls the whole site (following internal links,
up to a page limit) and scrapes as much as it can from every page.

It writes two outputs:

- 📦 **`output.json`** — structured, machine-readable data of everything found
- 📄 **`output.md`** — a human-readable report summarizing the crawl

## 🔍 What it extracts per page

- 🏷️ Title, language, canonical URL, and all `<meta>` tags (description, OG, etc.)
- 📝 All headings (h1–h6)
- 📰 Paragraph text, list items, and the full page text + word count
- 🔗 Every link (URL, anchor text, rel) — internal and external
- 🖼️ Every image (src, alt, title)
- 📊 Tables (as rows of cells)
- 🧾 Forms (action, method, fields incl. hidden values)
- 🧩 JSON-LD structured data
- 👻 Hidden-in-HTML extras: comments, `data-*`, `hreflang`, RSS/Atom feeds, favicon, manifest
- 🛠️ Tech fingerprint (WordPress, React, Shopify, jQuery, Cloudflare, …)
- 📇 Social profiles, emails, and phone numbers
- 🌐 HTTP response headers and top keywords

It also captures **site-wide** information:

- 🤖 **robots.txt** — fetched and shown in full
- 🗺️ **Sitemaps** — reads `robots.txt` + `/sitemap.xml` (including sitemap indexes)
  to discover every published page, even ones not linked anywhere
- 🗂️ **File catalog** — every document, image, media, archive, data and code file
  found, grouped by category
- 📃 **Text files** — `.txt`, `.xml`, `.json`, etc. are captured as readable content
  instead of being skipped

By default `robots.txt` rules are respected. Tick **Ignore robots.txt** (web UI)
or pass `--ignore-robots` (CLI) to crawl everything regardless.

✨ **Deep scraping (opt-in):** render JavaScript pages with a headless browser,
capture background API calls, take screenshots, and extract text from PDF/Word/
Excel files.

## 📁 Project structure

```
web-scraper/
├── app.py                 # Flask web app (entry point for the UI)
├── webscraper/            # the scraping engine (package)
│   ├── __init__.py        # public API: crawl, build_report, contacts_csv
│   ├── scraper.py         # crawl loop, extraction, report + CLI
│   ├── documents.py       # PDF / Word / Excel text extraction
│   └── render.py          # headless-browser rendering (Playwright)
├── templates/
│   └── index.html         # the single-page UI
├── docs/
│   └── document.md        # full project documentation
├── requirements.txt
├── LICENSE
└── README.md
```

## ⚙️ Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium     # only needed for JS rendering
```

## 🖥️ Web UI (minimalist website)

```bash
python app.py
```

Then open **http://127.0.0.1:8000** in your browser. Enter a URL, tweak the
options, and click **Scrape**. You get a **live progress bar** (with a **Stop**
button that keeps whatever was scraped so far), live stats, a per-page summary,
and buttons to download the JSON data, the Markdown report, and a detailed
**contacts spreadsheet (CSV)** of every email, phone, and social profile found
(with the page each came from). The CLI writes the same files, including
`<out>-contacts.csv`.

UI options: Max pages · **All pages** · **Parallel** (workers) · Delay · Crawl
site · Ignore robots.txt · Render JS · Screenshots.

## 💻 Command line usage

```bash
# Crawl a whole site (default: 50 pages, 8 parallel fetchers)
python -m webscraper.scraper https://example.com

# Faster / bigger
python -m webscraper.scraper https://example.com --max-pages 500 --workers 16

# Single page only
python -m webscraper.scraper https://example.com --no-crawl

# Slow & polite (delay only applies with 1 worker)
python -m webscraper.scraper https://example.com --workers 1 --delay 0.5

# Render JavaScript sites + screenshots
python -m webscraper.scraper https://example.com --render --screenshots

# Ignore robots.txt (use responsibly) / custom output name
python -m webscraper.scraper https://example.com --ignore-robots --out mysite
```

## 🎛️ Options

| Flag | Default | Description |
|------|---------|-------------|
| `--max-pages` | 50 | Maximum number of pages to crawl |
| `--workers` | 8 | Parallel fetchers (forced to 1 with `--render`) |
| `--delay` | 0.0 | Seconds between requests; only applies with `--workers 1` |
| `--out` | output | Output filename prefix |
| `--no-crawl` | off | Scrape only the given URL, don't follow links |
| `--ignore-robots` | off | Ignore robots.txt rules |
| `--render` | off | Render pages with a headless browser (JS sites) |
| `--screenshots` | off | Capture a screenshot of each rendered page |
| `--no-docs` | off | Don't extract text from PDF/Word/Excel |

## 🚀 Speed

Parallel fetching makes it ~3–4× faster than sequential. **8–16 workers** is the
sweet spot for speed *and* completeness; more can trigger server throttling and
drop pages. JS rendering runs single-threaded by design. See `docs/document.md` for
benchmarks and details.

## 📌 Notes

- 🌍 Stays on the same domain (treats `www` and non-`www` as the same site).
- 🤖 Respects `robots.txt` by default (fetched once, reused for rules + sitemaps).
- 🙏 Be considerate: don't hammer sites you don't own; lower `--workers` / add delay.
- 📚 Full project documentation lives in **`docs/document.md`**.

---

<p align="center">🐍 Built with Python · 🧪 Flask · 🎭 Playwright · 💛 Free & open-source (MIT)</p>
