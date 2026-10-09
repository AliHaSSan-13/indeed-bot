"""
Indeed Auto-Apply Bot
---------------------
Automates job applications on Indeed using Camoufox.

Usage:
  - Configure your search and Chrome settings in config.yaml
  - Run: python indeed_bot.py

Author: @meteor314 
License: MIT
"""
import yaml
import time
import re
import sys
from camoufox.sync_api import Camoufox
from playwright.sync_api import TimeoutError as PWTimeout
import logging


# The smartapply entry point is an SPA: clicking Apply lands on a bootstrap URL
# that the app's router then swaps for a /form/<module> route.
FORM_URL_RE = re.compile(r"smartapply\.indeed\.com/beta/indeedapply/form/")

SUBMIT_BTN_SEL = '[data-testid="submit-application-button"]:visible'

ACTION_BUTTON_SELECTORS = [
    '[data-testid="continue-button"]:visible',
    SUBMIT_BTN_SEL,
    'button[type="submit"]:visible',
    'button:has-text("Continue"):visible',
    'button:has-text("Next"):visible',
    '[aria-label*="Continue"]:visible',
]


with open("config.yaml", "r") as f:
    config = yaml.safe_load(f)
camoufox_config = config.get("camoufox", {})
user_data_dir = camoufox_config.get("user_data_dir")
language = camoufox_config.get("language")


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

def click_and_wait(element, timeout=5):
    if element:
        element.click()
        time.sleep(timeout)


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

def apply_to_job(browser, job_url, logger):
    """Open a new tab, apply to the job using dynamic step navigation, log the result, and close the tab."""
    page = browser.new_page()
    page.set_default_timeout(15000)

    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(5)

        # 1. Click the primary "Apply Now" / "Easily Apply" button on the job page
        apply_btn_xpath = "/html/body/div[1]/div[2]/div/div/div/div[2]/div/div/div/div/div/div/div/div[2]/div[4]/div[1]/div"
        apply_btn = page.query_selector(f"xpath={apply_btn_xpath}")
        if apply_btn is None:
            apply_btn = page.locator(
                'a:has-text("Apply now"), button:has-text("Apply now"), '
                'a:has-text("Easily apply"), [data-testid*="apply"]'
            ).first
            apply_btn.wait_for(state="visible", timeout=15000)
        apply_btn.click(timeout=15000)

        # 2. Wait for the smartapply SPA to swap its bootstrap URL for a /form/ route.
        #    wait_for_load_state() does not fire on client-side routing, so the URL
        #    must be waited on explicitly instead of sleeping a fixed amount.
        try:
            page.wait_for_url(FORM_URL_RE, timeout=45000)
        except PWTimeout:
            logger.warning(f"Never reached smartapply form, stalled at {page.url}")

        # 3. Multi-step form loop (handles smartapply pages)
        max_steps = 10
        step = 0

        while step < max_steps:
            logger.info(f"[step {step}] {page.url}")

            # Check for successful completion
            if "post-apply" in page.url:
                logger.info(f"Applied successfully to {job_url}")
                page.close()
                return True
            if "review-module" in page.url:
                #Give the Loader time to genrate the review module
                time.sleep(10)
                # Last step: its only CTA is "Submit your application".
                submit_btn = page.locator(SUBMIT_BTN_SEL)
                while True:
                    try:
                        submit_btn.wait_for(state="visible", timeout=15000)
                    except PWTimeout:
                        logger.warning(f"Submit button never appeared on review module for {job_url}")
                        input("Press Enter to continue")

                    if submit_btn.is_disabled():
                        # Indeed keeps submission disabled until this module's CAPTCHA is solved.
                        logger.warning(f"Captcha required, discarding {job_url}")
                        input("Press Enter to continue after solving the captcha")

                    logger.info(f"[step {step}] clicking 'Submit your application'")
                    try:
                        submit_btn.click(timeout=15000)
                    except PWTimeout as e:
                        logger.warning(f"Submit click failed for {job_url}: {str(e).splitlines()[0]}")
                        page.close()
                        return False

                    step += 1
                    time.sleep(2)
                    break

            # Gate on the DOM, not the URL: the module must hydrate before its buttons exist.
            action_btn = find_action_button(page)
            if action_btn is None:
                logger.warning(f"Could not find action button at step {step} for {job_url}")
                break

            # Indeed keeps the CTA disabled until every required field is filled.
            if action_btn.is_disabled():
                logger.warning(f"Form incomplete at step {step}, action button disabled, for {job_url}")
                break

            logger.info(f"[step {step}] clicking {action_btn.inner_text().strip()[:30]!r}")
            try:
                action_btn.click(timeout=15000)
            except PWTimeout as e:
                logger.warning(f"Click failed at step {step} for {job_url}: {str(e).splitlines()[0]}")
                step += 1
                continue

            time.sleep(2)
            step += 1

        # Verification check after step loop finishes
        if "post-apply" in page.url:
            logger.info(f"Applied successfully to {job_url}")
            page.close()
            return True

        logger.warning(f"Failed to apply to {job_url}")
        page.close()
        return False

    except Exception as e:
        logger.exception(f"Error applying to {job_url}: {e}")
        page.close()
        return False

def setup_logger():
    logger = logging.getLogger("indeed_apply")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    logger.addHandler(handler)
    return logger


with Camoufox(user_data_dir=user_data_dir,
              persistent_context=True) as browser:
    logger = setup_logger()
    page = browser.new_page()
    page.goto("https://" + language + ".indeed.com")

    cookies = page.context.cookies()
    ppid_cookie = next(
        (cookie for cookie in cookies if cookie['name'] == 'PPID'), None)
    if not ppid_cookie:
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
    else:
        print("Token found, proceeding with job search...")
        search_config = config.get("search", {})
        base_url = search_config.get("base_url", "")
        start = search_config.get("start", "")
        end = search_config.get("end", "")

        listURL = []
        i = start
        while i <= end:
            url = f"{base_url}&start={i}"
            listURL.append(url)
            i += 10

        all_job_links = []
        for url in listURL:
            print(f"Visiting URL: {url}")
            page.goto(url)
            page.wait_for_load_state("domcontentloaded")
            print(
                "Waiting for page to load, if any cloudflare protection button appears... please click it.")
            #Will be adding auto detector and resolver for this
            time.sleep(10)

            try:
                links = collect_indeed_apply_links(page, language)
                all_job_links.extend(links)
                print(f"Found {len(links)} Indeed Apply jobs on this page.")
            except Exception as e:
                print("Error extracting jobs:", e)
            time.sleep(5)

        #Deduplication
        all_job_links = list(dict.fromkeys(all_job_links))
        print(f"Total Unique Indeed Apply jobs found: {len(all_job_links)}")    
        for job_url in all_job_links:
            print(f"Applying to: {job_url}")
            apply_to_job(browser, job_url, logger)
            time.sleep(5)
