"""
Indeed Auto-Apply Bot
---------------------
Starts the browser, verifies the login session, scrapes the Easy Apply job
links from the configured search pages, then opens each one in its own tab and
clicks its Apply now button.

Usage:
  - Configure your search and Camoufox settings in config.yaml
  - Run: python indeed_bot.py

Author: @meteor314 
License: MIT
"""
import logging
import re
import sys
import time

import yaml
from camoufox.sync_api import Camoufox
from playwright.sync_api import TimeoutError as PWTimeout

from ai_evaluation import filter_jobs


CONFIG_PATH = "config.yaml"

# How long each search page gets to settle, so a cloudflare check can be clicked.
SEARCH_SETTLE_SECONDS = 10
# How long an opened job page rests after its document content is ready.
JOB_PAGE_SETTLE_SECONDS = 5

# Tracking and logging endpoints that add load and contribute to 429s.
BLOCKED_ROUTES = [
    "**/log**",
    "**/telemetry**",
    "**/analytics**",
]

# Primary Apply button on a job page, with a text/testid fallback below it.
APPLY_BTN_XPATH = "/html/body/div[1]/div[2]/div/div/div/div[2]/div/div/div/div/div/div/div/div[2]/div[4]/div[1]/div"
APPLY_BTN_SELECTOR = (
    'a:has-text("Apply now"), button:has-text("Apply now"), '
    'a:has-text("Easily apply"), [data-testid*="apply"]'
)

# Clicking Apply lands on a bootstrap URL that smartapply's router then swaps for
# a /form/<module> route, so the landing has to be waited on by URL - the SPA
# route change fires no load event for wait_for_load_state() to see.
FORM_URL_RE = re.compile(r"smartapply\.indeed\.com/beta/indeedapply/form/")

# Submitted applications land on a post-apply route.
POST_APPLY_RE = re.compile(r"post-apply")

# Review module
SUBMIT_BTN_SELECTOR = '[data-testid="submit-application-button"]:visible'

# Tail of the prompt Indeed shows when a human has to clear something (captcha,
# unanswered screening question) before the module will advance.
MANUAL_INPUT_RE = re.compile(r"to continue", re.IGNORECASE)

# Tried in order on every non-review module. Each carries :visible because the SPA
# keeps all its modules mounted and hides the inactive ones, so a hidden module's
# button would otherwise be picked by .first and block forever on a visibility wait.
ACTION_BUTTON_SELECTORS = [
    '[data-testid="continue-button"]:visible',
    SUBMIT_BTN_SELECTOR,
    'button[type="submit"]:visible',
    'button:has-text("Continue"):visible',
    'button:has-text("Next"):visible',
    '[aria-label*="Continue"]:visible',
]


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


def block_tracking(browser):
    """Abort tracking/logging requests. Set on the context so every new tab is covered."""
    # Camoufox yields a BrowserContext when persistent_context=True, else a Browser.
    context = getattr(browser, "context", browser)
    for pattern in BLOCKED_ROUTES:
        context.route(pattern, lambda route: route.abort())


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


def collect_indeed_apply_links(page, language = "www"):
    """
    Collect all 'Easily apply' jobs from the current Indeed search result page
    as (title, url) pairs.
    """
    jobs = []
    
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
                jobs.append((extract_job_title(link), job_url))
                
    return jobs


def extract_job_title(link):
    """Job title from a jcs-JobTitle anchor. The aria-label carries a
    'full details of ' prefix, so the titled span is preferred."""
    span = link.query_selector('span[title]')
    if span:
        title = span.get_attribute('title')
        if title:
            return title.strip()

    title = link.inner_text().strip()
    if title:
        return title

    label = link.get_attribute('aria-label') or ""
    return label.removeprefix('full details of ').strip()


def scrape_page(page, url, language, logger):
    """Load one search results page and return its Easy Apply jobs as (title, url) pairs."""
    print(f"Visiting URL: {url}")
    page.goto(url)
    page.wait_for_load_state("domcontentloaded")
    print(
        "Waiting for page to load, if any cloudflare protection button appears... please click it.")
    #Will be adding auto detector and resolver for this
    time.sleep(SEARCH_SETTLE_SECONDS)

    try:
        jobs = collect_indeed_apply_links(page, language)
    except Exception as e:
        logger.error(f"Error extracting jobs from {url}: {e}")
        jobs = []

    print(f"Found {len(jobs)} Indeed Apply jobs on this page.")
    return jobs


def click_apply_now(page):
    """Click the primary Apply now button on an open job page."""
    if page.title() == "Just a moment...":
        print("Solving CAPTCHA...")
        page.wait_for_load_state("networkidle")
        page.mouse.click(627,215)
        print("CAPTCHA solved! 🎉")
        time.sleep(5)
    apply_btn = page.query_selector(f"xpath={APPLY_BTN_XPATH}")
    if apply_btn is None:
        apply_btn = page.locator(APPLY_BTN_SELECTOR).first

    label = apply_btn.inner_text().strip()[:30]
    apply_btn.click(timeout=15000)
    print(f"Clicked {label!r}")
    return label


def find_action_button(page, timeout=20):
    """Poll the CTA selector chain until a visible match appears; return it or None."""
    deadline = time.time() + timeout
    while True:
        for selector in ACTION_BUTTON_SELECTORS:
            candidate = page.locator(selector)
            if candidate.count() > 0 and candidate.first.is_visible():
                return candidate.first
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


def submit_review_module(page, job_url):
    """Clear the captcha if present, submit, and confirm via the landing URL."""
    time.sleep(10)
    # Scrool to the bottom of the page to find the CAPTCHA checkbox.
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.mouse.click(589,2688)
    print("Clicking CAPTCHA checkbox")
    time.sleep(2)

    submit_btn = page.locator(SUBMIT_BTN_SELECTOR)
    try:
        submit_btn.wait_for(state="visible", timeout=15000)
    except PWTimeout:
        print(f"Submit button never appeared for {job_url}")
        return False

    if submit_btn.first.is_disabled():
        print(f"Submit still disabled after CAPTCHA, discarding {job_url}")
        input("Press Enter to continue after filling CAPTCHA: ")

    print("Clicking 'Submit your application'")
    try:
        submit_btn.first.click(timeout=15000)
    except PWTimeout as e:
        print(f"Submit click failed: {str(e).splitlines()[0]}")
        return False

    try:
        page.wait_for_url(POST_APPLY_RE, timeout=30000)
    except PWTimeout:
        print(f"No post-apply confirmation, stalled at: {page.url}")
        return False

    print(f"Applied successfully to {job_url}")
    return True


def submit_application(page, job_url, max_steps=10):
    """Drive the smartapply modules until the application is submitted."""
    step = 0

    while step < max_steps:
        print(f"[step {step}] {page.url}")

        if "post-apply" in page.url:
            print(f"Applied successfully to {job_url}")
            return True

        if "review-module" in page.url:
            return submit_review_module(page, job_url)

        action_btn = find_action_button(page)
        if action_btn is None:
            print(f"Could not find action button at step {step} for {job_url}")
            return False

        if action_btn.is_disabled():
            print(f"Form incomplete at step {step}, action button disabled, for {job_url}")
            input("Press Enter to continue after filling the required fields: ")
            continue

        print(f"[step {step}] clicking {action_btn.inner_text().strip()[:30]!r}")
        try:
            action_btn.click(timeout=15000)
        except PWTimeout as e:
            print(f"Click failed at step {step}: {str(e).splitlines()[0]}")
            step += 1
            continue

        time.sleep(2)

        if page.get_by_text(MANUAL_INPUT_RE).count() > 0:
            print("Please fill the required fields manually")
            input("Press Enter to continue after filling the required fields: ")

        step += 1

    if "post-apply" in page.url:
        print(f"Applied successfully to {job_url}")
        return True

    print(f"Failed to apply to {job_url}")
    return False


def open_job_page(browser, job_url, settle_seconds=JOB_PAGE_SETTLE_SECONDS, language="www"):
    """Open one job URL in a new tab of the same window and let it settle."""
    
    page = browser.new_page()
    # Always start at the Indeed homepage, so we dont get 429 errors.
    page.goto(f"https://{language}.indeed.com")
    time.sleep(settle_seconds)

    print(f"Opening: {job_url}")
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
        else:
            print("CAPTCHA not solved, Discarding job...")
            page.close()
            return

    try:
        click_apply_now(page)
        page.wait_for_url(FORM_URL_RE, timeout=45000)
        print(f"Now applying at: {page.url}")
        submit_application(page, job_url)
        input("Press Enter to close the browser - After applying")
    except PWTimeout:
        print(f"Apply did not reach the smartapply form, stalled at: {page.url}")
    page.close()


def main():
    logger = setup_logger()
    config = load_config()
    camoufox_config = config.get("camoufox", {})
    search_config = config.get("search", {})
    ai_config = config.get("ai", {})
    language = camoufox_config.get("language")

    with start_browser(camoufox_config) as browser:
        block_tracking(browser)
        page = browser.new_page()

        if not ensure_login(page, browser, language, logger):
            return

        all_jobs = []
        for url in build_search_urls(search_config):
            all_jobs.extend(scrape_page(page, url, language, logger))
            time.sleep(5)

        jobs = list(dict.fromkeys(all_jobs))
        print(f"Total Indeed Apply jobs found: {len(jobs)}")

        selected_jobs = filter_jobs(jobs, ai_config, logger)
        print(f"Jobs selected for applying: {len(selected_jobs)}")
        for title, job_url in selected_jobs:
            print(f"Applying to: {title} -> {job_url}")

        for title, job_url in selected_jobs:
            try:
                open_job_page(browser, job_url, language=language)
            except Exception as e:
                logger.error(f"Failed to apply to '{title}': {str(e).splitlines()[0]}")


if __name__ == "__main__":
    main()