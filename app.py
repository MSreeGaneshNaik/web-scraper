#!/usr/bin/env python3
"""
Minimalist web UI for the site scraper.

Run:
    python app.py
Then open http://127.0.0.1:5000 in your browser.
"""

import base64
import io
import json
import threading
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Flask, render_template, request, jsonify, send_file

from webscraper import crawl, build_report, same_domain, contacts_csv

app = Flask(__name__)

# Keep the most recent crawl in memory so the user can download the files.
LAST = {"json": None, "report": None, "domain": None, "screenshots": [],
        "contacts_csv": None}

# Live state of the running crawl, polled by the progress bar.
JOB = {"running": False, "done": 0, "total": 0, "current": "",
       "summary": None, "error": None, "stopped": False}

# Set to ask the running crawl to stop early (and keep partial results).
STOP = threading.Event()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    if JOB["running"]:
        return jsonify({"error": "A scrape is already running."}), 409

    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "Please enter a URL."}), 400
    if not urlparse(url).scheme:
        url = "https://" + url

    try:
        # Upper bound is high so the "All pages" option can crawl a whole site.
        max_pages = max(1, min(int(data.get("max_pages", 50)), 100000))
    except (TypeError, ValueError):
        max_pages = 50
    try:
        delay = max(0.0, float(data.get("delay", 0.3)))
    except (TypeError, ValueError):
        delay = 0.3
    crawl_site = bool(data.get("crawl", True))
    ignore_robots = bool(data.get("ignore_robots", False))
    render = bool(data.get("render", False))
    screenshots = bool(data.get("screenshots", False))
    try:
        workers = max(1, min(int(data.get("workers", 8)), 32))
    except (TypeError, ValueError):
        workers = 8

    # Reset progress and run the crawl in the background.
    # total starts at 0 (unknown) until discovery finds the real page count.
    STOP.clear()
    JOB.update({"running": True, "done": 0, "total": 0, "current": "",
                "summary": None, "error": None, "stopped": False})
    t = threading.Thread(
        target=run_job,
        args=(url, max_pages, delay, crawl_site, ignore_robots,
              render, screenshots, workers),
        daemon=True,
    )
    t.start()
    return jsonify({"started": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    if not JOB["running"]:
        return jsonify({"stopping": False, "error": "Nothing is running."}), 200
    STOP.set()
    JOB["current"] = "Stopping… finishing in-flight pages"
    return jsonify({"stopping": True})


@app.route("/api/progress")
def api_progress():
    return jsonify({
        "running": JOB["running"],
        "done": JOB["done"],
        "total": JOB["total"],
        "current": JOB["current"],
        "error": JOB["error"],
        "stopped": JOB["stopped"],
        "summary": JOB["summary"],
    })


def run_job(url, max_pages, delay, crawl_site, ignore_robots, render,
            screenshots, workers):
    def on_progress(done, total, current):
        JOB["done"], JOB["total"], JOB["current"] = done, total, current

    try:
        pages, errors, base_netloc, extras = crawl(
            url, max_pages, delay,
            crawl_site=crawl_site,
            ignore_robots=ignore_robots,
            render=render,
            screenshots=screenshots,
            progress_cb=on_progress,
            workers=workers,
            stop_flag=STOP,
        )
    except Exception as e:
        JOB["error"] = f"Crawl failed: {e}"
        JOB["running"] = False
        return

    if not pages:
        JOB["error"] = ("Stopped before any page was scraped."
                        if STOP.is_set() else
                        "No pages scraped. Check the URL or try ignoring robots.txt.")
        JOB["stopped"] = STOP.is_set()
        JOB["running"] = False
        return

    JOB["summary"] = build_outputs(url, pages, errors, base_netloc, extras)
    JOB["done"] = JOB["total"] = len(pages)
    JOB["stopped"] = STOP.is_set()
    JOB["running"] = False


def build_outputs(url, pages, errors, base_netloc, extras):
    result = {
        "start_url": url,
        "domain": base_netloc,
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
    report = build_report(pages, errors, base_netloc, url, extras)

    # Build the detailed contacts spreadsheet.
    csv_text, contact_rows = contacts_csv(pages, base_netloc)

    # Stash for downloads + screenshot serving.
    LAST["json"] = json.dumps(result, indent=2, ensure_ascii=False)
    LAST["report"] = report
    LAST["domain"] = base_netloc
    LAST["screenshots"] = extras["screenshots"]
    LAST["contacts_csv"] = csv_text

    # Build a compact summary for the UI.
    total_words = sum(p["word_count"] for p in pages)
    all_links, external, all_images, all_emails = set(), set(), set(), set()
    for p in pages:
        for l in p["links"]:
            all_links.add(l["url"])
            if not same_domain(l["url"], base_netloc):
                external.add(l["url"])
        for img in p["images"]:
            all_images.add(img["src"])
        all_emails.update(p["emails"])

    resources = extras.get("resources") or {}
    resource_total = sum(len(v) for v in resources.values())

    # Aggregate site-wide tech + social profiles.
    site_tech, site_social = set(), {}
    for p in pages:
        site_tech.update(p.get("tech") or [])
        for k, v in (p.get("social") or {}).items():
            site_social.setdefault(k, v)

    summary = {
        "domain": base_netloc,
        "pages_scraped": len(pages),
        "errors": len(errors),
        "total_words": total_words,
        "unique_links": len(all_links),
        "external_links": len(external),
        "unique_images": len(all_images),
        "emails": sorted(all_emails),
        "tech": sorted(site_tech),
        "social": site_social,
        "contacts_count": contact_rows,
        "rendered": any(p.get("rendered") for p in pages),
        "robots_txt": extras.get("robots_txt"),
        "sitemaps": extras.get("sitemaps") or [],
        "resources": resources,
        "resource_total": resource_total,
        "text_files": [
            {"url": t["url"], "content_type": t["content_type"], "size": t["size"]}
            for t in (extras.get("text_files") or [])
        ],
        "documents": [
            {"url": d["url"], "chars": d["chars"], "preview": d["text"][:400]}
            for d in (extras.get("documents") or [])
        ],
        "screenshots": [
            {"url": s["url"], "index": i}
            for i, s in enumerate(extras.get("screenshots") or [])
        ],
        "pages": [
            {
                "url": p["url"],
                "title": p["title"],
                "status": p["status_code"],
                "words": p["word_count"],
                "links": len(p["links"]),
                "images": len(p["images"]),
                "description": p["description"],
                "h1": p["headings"]["h1"],
                "h2": p["headings"]["h2"][:6],
                "tables": len(p["tables"]),
                "forms": len(p.get("forms") or []),
                "tech": p.get("tech") or [],
                "social": p.get("social") or {},
                "keywords": [k["word"] for k in (p.get("keywords") or [])[:10]],
                "api_calls": len(p.get("api_calls") or []),
                "rendered": p.get("rendered", False),
                "emails": p["emails"],
                "preview": p["text"][:320],
            }
            for p in pages
        ],
    }
    return summary


@app.route("/download/<kind>")
def download(kind):
    if kind == "json" and LAST["json"]:
        content, name, mime = LAST["json"], "output.json", "application/json"
    elif kind == "report" and LAST["report"]:
        content, name, mime = LAST["report"], "output.md", "text/markdown"
    elif kind == "contacts" and LAST.get("contacts_csv"):
        content, name, mime = LAST["contacts_csv"], "contacts.csv", "text/csv"
    else:
        return "Nothing to download yet. Run a scrape first.", 404
    buf = io.BytesIO(content.encode("utf-8"))
    fname = f"{LAST['domain'] or 'scrape'}-{name}"
    return send_file(buf, mimetype=mime, as_attachment=True, download_name=fname)


@app.route("/screenshot/<int:idx>")
def screenshot(idx):
    shots = LAST.get("screenshots") or []
    if 0 <= idx < len(shots):
        png = base64.b64decode(shots[idx]["image"])
        return send_file(io.BytesIO(png), mimetype="image/png")
    return "No such screenshot.", 404


if __name__ == "__main__":
    import os
    # Port 5000 is taken by macOS AirPlay Receiver, so default to 8000.
    port = int(os.environ.get("PORT", 8000))
    app.run(host="127.0.0.1", port=port, debug=False)
