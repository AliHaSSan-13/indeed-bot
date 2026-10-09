"""
AI Job Evaluation
-----------------
Asks an OpenAI-compatible endpoint whether each scraped job title matches the
user's profile. Only an exact "true" lets the bot apply to that job; anything
else - including a failed or malformed reply - rejects it.

Config lives under the `ai:` section of config.yaml.
"""
import re

import requests


SYSTEM_PROMPT = (
    "You are a job screening assistant. You are given a candidate profile and a "
    "job title. Decide whether the job title is a good match for that profile.\n"
    "Answer with exactly one word: true or false.\n"
    "Answer 'true' only when the job is relevant to the profile.\n"
    "Output nothing else - no explanation, no punctuation, no quotes, no markdown."
)

# Deliberately strict: only a bare true passes the gate.
TRUE_RE = re.compile(r"^true$", re.IGNORECASE)


def ai_enabled(ai_config):
    """True only when the gate is switched on and a profile prompt is present."""
    if not ai_config.get("enabled"):
        return False
    return bool((ai_config.get("user_profile_prompt") or "").strip())


def build_payload(job_title, ai_config):
    """The chat-completions request body for one job title."""
    profile = (ai_config.get("user_profile_prompt") or "").strip()
    return {
        "model": ai_config.get("model", ""),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Candidate profile:\n{profile}\n\nJob title:\n{job_title}",
            },
        ],
        "temperature": 0,
        "max_tokens": 5,
    }


def is_worth_applying(job_title, ai_config, logger):
    """Return True only when the model answers exactly 'true' for this job title."""
    if not ai_enabled(ai_config):
        return True

    url = ai_config.get("base_url", "").rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ai_config.get('api_key', '')}",
    }

    try:
        response = requests.post(
            url,
            json=build_payload(job_title, ai_config),
            headers=headers,
            timeout=ai_config.get("timeout", 20),
        )
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.warning(
            f"AI evaluation unavailable for '{job_title}' ({type(e).__name__}), skipping job"
        )
        return False

    if not TRUE_RE.match(answer):
        logger.info(f"AI rejected '{job_title}': {answer!r}")
        return False

    logger.info(f"AI accepted '{job_title}'")
    return True


def filter_jobs(jobs, ai_config, logger):
    """Given (title, url) pairs, keep only the ones the AI approves."""
    if not ai_enabled(ai_config):
        logger.info("AI evaluation disabled, keeping all jobs")
        return list(jobs)

    kept = []
    for title, job_url in jobs:
        if is_worth_applying(title, ai_config, logger):
            kept.append((title, job_url))

    logger.info(f"AI selected {len(kept)} of {len(jobs)} jobs")
    return kept