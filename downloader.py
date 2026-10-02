"""Generic, re-runnable file downloader for paginated web pages (Selenium + requests).

Configuration is read from environment variables / .env (see .env.example).
Pattern: Selenium collects links page by page; downloads reuse the browser session cookies.
Properties: idempotent (skips existing files), fault-tolerant (per-file try/except),
throttled, read-only (never modifies the site), logs a run summary.
"""
import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

START_URL = os.getenv("START_URL", "")
LINK_SELECTOR = os.getenv("LINK_SELECTOR", "a")                  # CSS selector for candidate links
FILE_EXTENSIONS = tuple(e.strip().lower() for e in os.getenv("FILE_EXTENSIONS", ".pdf,.zip,.txt,.csv").split(","))
NEXT_SELECTOR = os.getenv("NEXT_SELECTOR", "")                   # CSS selector for "next page" (optional)
MAX_PAGES = int(os.getenv("MAX_PAGES", "50"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "downloads")) / date.today().isoformat()
THROTTLE_SECONDS = float(os.getenv("THROTTLE_SECONDS", "1"))
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# Optional login (only if the site you are authorised to use requires one)
LOGIN_URL = os.getenv("LOGIN_URL", "")
USERNAME = os.getenv("SITE_USERNAME", "")
PASSWORD = os.getenv("SITE_PASSWORD", "")
USERNAME_SELECTOR = os.getenv("USERNAME_SELECTOR", "input[name='username']")
PASSWORD_SELECTOR = os.getenv("PASSWORD_SELECTOR", "input[name='password']")
SUBMIT_SELECTOR = os.getenv("SUBMIT_SELECTOR", "button[type='submit']")

log = logging.getLogger("downloader")


def safe_filename(url: str) -> str:
    name = unquote(Path(urlparse(url).path).name) or "download"
    return re.sub(r"[^\w.\-]+", "_", name)


def download_file(session: requests.Session, url: str, dest_dir: Path, timeout: int = 60) -> str:
    """Download one file. Returns 'skipped' or 'downloaded'. Raises on failure."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_filename(url)
    if dest.exists() and dest.stat().st_size > 0:      # idempotency check
        return "skipped"
    part = dest.with_suffix(dest.suffix + ".part")      # write to temp, then rename
    with session.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with part.open("wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
    part.replace(dest)
    return "downloaded"


def collect_links(driver, by_css) -> list[str]:
    links = []
    for el in driver.find_elements(by_css, LINK_SELECTOR):
        href = el.get_attribute("href")
        if href and urlparse(href).path.lower().endswith(FILE_EXTENSIONS):
            links.append(urljoin(driver.current_url, href))
    return links


def run() -> dict:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By

    if not START_URL:
        raise SystemExit("Set START_URL in .env (a page you are authorised to access).")

    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    driver = webdriver.Chrome(options=opts)
    stats = {"downloaded": 0, "skipped": 0, "failed": 0}
    try:
        if LOGIN_URL and USERNAME:
            driver.get(LOGIN_URL)
            driver.find_element(By.CSS_SELECTOR, USERNAME_SELECTOR).send_keys(USERNAME)
            driver.find_element(By.CSS_SELECTOR, PASSWORD_SELECTOR).send_keys(PASSWORD)
            driver.find_element(By.CSS_SELECTOR, SUBMIT_SELECTOR).click()

        driver.get(START_URL)
        seen, all_links = set(), []
        for page in range(1, MAX_PAGES + 1):
            for link in collect_links(driver, By.CSS_SELECTOR):
                if link not in seen:
                    seen.add(link)
                    all_links.append(link)
            log.info("Page %d: %d files found so far", page, len(all_links))
            if not NEXT_SELECTOR:
                break
            nxt = driver.find_elements(By.CSS_SELECTOR, NEXT_SELECTOR)
            if not nxt or not nxt[0].is_enabled():
                break
            nxt[0].click()
            time.sleep(THROTTLE_SECONDS)

        session = requests.Session()
        for c in driver.get_cookies():                  # reuse the browser's authenticated session
            session.cookies.set(c["name"], c["value"])

        for url in all_links:
            try:
                result = download_file(session, url, DOWNLOAD_DIR)
                stats[result] += 1
                log.info("%s: %s", result, url)
                if result == "downloaded":
                    time.sleep(THROTTLE_SECONDS)       # throttle only real downloads
            except Exception as exc:                    # one failure must not stop the run
                stats["failed"] += 1
                log.error("FAILED %s (%s)", url, exc)
    finally:
        driver.quit()
    log.info("Done: %s", stats)
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()
