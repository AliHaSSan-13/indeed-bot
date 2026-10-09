"""
Indeed Auto-Apply Bot
---------------------
Starts the browser, verifies the login session, scrapes the Easy Apply job
links from the configured search pages, then opens each one in its own tab.

Usage:
  - Configure your search and Camoufox settings in config.yaml
  - Run: python indeed_bot.py

Author: @meteor314 
License: MIT
"""
import logging
import sys
import time

import yaml
from camoufox.sync_api import Camoufox


CONFIG_PATH = "config.yaml"

# How long each search page gets to settle, so a cloudflare check can be clicked.
SEARCH_SETTLE_SECONDS = 10
# How long an opened job page rests after its document content is ready.
JOB_PAGE_SETTLE_SECONDS = 5


def load_config(path=CONFIG_PATH):
    """Read config.yaml."""
    with open(path, "r") as f:
        return yaml.safe_load(f)


def setup_logger():
    """Log to the terminal only."""
    logger = logging.getLogger("indeed_apply")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    logger.addHandler(handler)
    return logger


def start_browser(camoufox_config):
    """Launch Camoufox with the settings from config.yaml."""
    return Camoufox(
        user_data_dir=camoufox_config.get("user_data_dir"),
        persistent_context=True,
        humanize=True,
    )


def ensure_login(page, browser, language, logger):
    """Return True if a PPID session cookie is present, otherwise prompt for a login."""
    page.goto("https://" + language + ".indeed.com")

    cookies = page.context.cookies()
    ppid_cookie = next((cookie for cookie in cookies if cookie['name'] == 'PPID'), None)
    if ppid_cookie:
        logger.info("Token found, proceeding with job search...")
        return True

    try:
        print("Token not found, please log in to Indeed first.")
        print("Redirecting to login page...")
        print("You need  to restart the bot after logging in.")
        page.goto("https://secure.indeed.com/auth?hl=" + language)
        input("Press Enter to close the browser - After logging in")
        print("Closing browser")
        browser.close()
    except Exception as e:
        print("Error logging in ,Please try again")
        print(e)
    return False


def build_search_urls(search_config):
    """One results URL per page between start and end, 10 jobs apart."""
    base_url = search_config.get("base_url", "")
    start = int(search_config.get("start", 0))
    end = int(search_config.get("end", 0))

    urls = []
    offset = start
    while offset <= end:
        urls.append(f"{base_url}&start={offset}")
        offset += 10
    return urls


def collect_indeed_apply_links(page, language):
    """
    Collect all 'Easily apply' job links from the current Indeed search result page.
    """
    links = []
    
    # Target result containers directly (handles tr/td or card wrappers)
    job_cards = page.query_selector_all('td.resultContent, div.job_seen_beacon')
    
    for card in job_cards:
        # Check for the "Easily apply" text badge or the data-testid attribute
        easy_apply_badge = card.query_selector('[data-testid="indeedApply"]')
        has_easy_apply_text = "Easily apply" in card.inner_text()

        # Skip jobs that are not "Easy Apply"
        if not (easy_apply_badge or has_easy_apply_text):
            continue

        # Target the job link
        link = card.query_selector('a.jcs-JobTitle')
        if link:
            job_url = link.get_attribute('href')
            if job_url:
                # Resolve relative URLs
                if job_url.startswith('/'):
                    job_url = f"https://{language}.indeed.com{job_url}"
                links.append(job_url)
                
    return links


def scrape_page(page, url, language, logger):
    """Load one search results page and return its Easy Apply job links."""
    print(f"Visiting URL: {url}")
    page.goto(url)
    page.wait_for_load_state("domcontentloaded")
    print(
        "Waiting for page to load, if any cloudflare protection button appears... please click it.")
    #Will be adding auto detector and resolver for this
    time.sleep(SEARCH_SETTLE_SECONDS)

    try:
        links = collect_indeed_apply_links(page, language)
    except Exception as e:
        logger.error(f"Error extracting jobs from {url}: {e}")
        links = []

    print(f"Found {len(links)} Indeed Apply jobs on this page.")
    return links


def open_job_page(browser, job_url, settle_seconds=JOB_PAGE_SETTLE_SECONDS):
    """Open one job URL in a new tab of the same window and let it settle."""
    print(f"Opening: {job_url}")
    page = browser.new_page()
    page.goto(job_url)
    page.wait_for_load_state("domcontentloaded")
    time.sleep(settle_seconds)
    if page.title() == "Just a moment...":
        print("Solving CAPTCHA...")
        page.wait_for_load_state("networkidle")
        page.mouse.click(627,215)
        time.sleep(settle_seconds)
        if page.title() != "Just a moment...":
            print("CAPTCHA solved! 🎉")
    page.close()


def main():
    logger = setup_logger()
    config = load_config()
    camoufox_config = config.get("camoufox", {})
    search_config = config.get("search", {})
    language = camoufox_config.get("language")

    with start_browser(camoufox_config) as browser:
        page = browser.new_page()

        if not ensure_login(page, browser, language, logger):
            return

        all_job_links = []
        for url in build_search_urls(search_config):
            all_job_links.extend(scrape_page(page, url, language, logger))
            time.sleep(5)

        job_links = list(dict.fromkeys(all_job_links))
        print(f"Total Indeed Apply jobs found: {len(job_links)}")

        for job_url in job_links:
            try:
                open_job_page(browser, job_url)
            except Exception as e:
                logger.error(f"Failed to open {job_url}: {str(e).splitlines()[0]}")


if __name__ == "__main__":
    main()