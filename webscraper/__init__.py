"""Web Scraper engine package.

Public API used by the web app (app.py) and importers:
    from webscraper import crawl, build_report, contacts_csv, same_domain
"""

from .scraper import crawl, build_report, contacts_csv, same_domain

__all__ = ["crawl", "build_report", "contacts_csv", "same_domain"]
