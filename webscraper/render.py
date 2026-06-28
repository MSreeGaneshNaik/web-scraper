"""
Render pages with a headless browser (Playwright) so JavaScript-built content
is captured, log the background API/XHR calls the page makes, and optionally
take a screenshot.

A single RenderSession reuses one browser across many pages for speed.
Everything is lazy-imported so the rest of the scraper works without Playwright.
"""

import base64
import json


class RenderSession:
    def __init__(self, screenshots=False, timeout=20000, wait="networkidle"):
        self.screenshots = screenshots
        self.timeout = timeout
        self.wait = wait
        self._pw = None
        self._browser = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        return self

    def __exit__(self, *exc):
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def fetch(self, url):
        """Return dict: html, status, api_calls, screenshot (base64 png or None)."""
        context = self._browser.new_context(
            user_agent=("Mozilla/5.0 (compatible; SiteScraper/1.0; "
                        "+https://example.com/bot)")
        )
        page = context.new_page()
        api_calls = []

        def on_response(resp):
            try:
                ct = (resp.headers or {}).get("content-type", "")
                if "json" in ct.lower():
                    body = None
                    try:
                        body = resp.json()
                    except Exception:
                        body = None
                    api_calls.append({
                        "url": resp.url,
                        "status": resp.status,
                        "content_type": ct,
                        "data": _trim(body),
                    })
            except Exception:
                pass

        page.on("response", on_response)

        status = None
        html = ""
        shot = None
        try:
            nav = page.goto(url, timeout=self.timeout, wait_until=self.wait)
            status = nav.status if nav else None
        except Exception:
            # Even on timeout we may have partial content.
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass
        try:
            html = page.content()
        except Exception:
            html = ""
        if self.screenshots:
            try:
                png = page.screenshot(full_page=True, type="png")
                shot = base64.b64encode(png).decode("ascii")
            except Exception:
                shot = None

        context.close()
        return {
            "html": html,
            "status": status,
            "api_calls": api_calls,
            "screenshot": shot,
        }


def _trim(obj, max_chars=4000):
    """Keep captured API payloads from bloating the output."""
    if obj is None:
        return None
    try:
        s = json.dumps(obj, ensure_ascii=False)
    except Exception:
        return None
    if len(s) > max_chars:
        return s[:max_chars] + "…(truncated)"
    return obj


def is_available():
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False
