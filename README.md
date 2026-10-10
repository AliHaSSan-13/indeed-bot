# Indeed Auto-Apply Bot

**WARNING:**  
This guide explains how to use this bot. Use at your own risk. Indeed may change their website or introduce new protections (such as captchas or anti-bot measures) at any time, which could break this tool or result in your account being restricted. This is for educational purposes only.

---

## Features

- Scrapes "Indeed Apply" jobs from your Indeed search results.
- Optional AI gate: an OpenAI-compatible endpoint filters job titles against your profile before anything is applied to.
- Uses [Camoufox](https://github.com/daijro/camoufox) for browser automation (fingerprint spoofing, Cloudflare handling).
- Walks the smartapply wizard: picks a resume, clicks Continue, handles captchas, and submits.
- Logs to the terminal only — no log files.

## Prerequisites

- **Python 3.10 – 3.13** (Camoufox 0.5.5 declares `>=3.10,<4.0`)
- Linux, macOS, or Windows (see [Platform support](#platform-support))
- An Indeed account with:
  - Your CV already uploaded
  - Your name, address, and phone number filled in your Indeed profile (It is better to have your Profile Completed)
- Optional, for the AI gate: any OpenAI-compatible chat-completions endpoint

---

## Setup

1. **Clone and install:**
    ```bash
    git clone https://github.com/AliHaSSan-13/indeed-bot.git
    cd indeed_bot
    python -m venv .venv
    source .venv/bin/activate        # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    ```

2. **Edit `config.yaml`.** Copy `config.example.yaml` if you are setting this up fresh.

    ```yaml
    search:
      base_url: "https://uk.indeed.com/jobs?q=python+developer&l=London"
      start: 0
      end: 50        # pagination offset; must be a multiple of 10

    camoufox:
      user_data_dir: "user_data_dir"   # where the Indeed session is stored
      language: "uk"                   # Indeed site code

    ai:
      enabled: false                   # turn on to filter jobs before applying
      base_url: "http://localhost:8081/v1"
      model: "gemini-3.5-flash"
      api_key: "sk-your-key"
      timeout: 20
      user_profile_prompt: |
        I am a Python backend developer with experience in FastAPI and Django...
    ```

    - `base_url` — an Indeed search URL.
    - `start`/`end` — result offsets, 10 apart. `end: 3` fetches only the first page; use `end: 100` for a broad sweep.
    - `language` — Indeed site code, must match `base_url` (`uk`, `us`, `fr`, `de`, `pk`, `in`, …).
    - `user_profile_prompt` — leave empty to disable the AI gate entirely, even if `enabled` is true.

3. **Get your `base_url`:**

    - Go to [Indeed](https://www.indeed.com/) in your browser.
    - Set your search options (title, location, remote, job type).
    - Click **Find jobs** and copy the URL from the address bar.

    ![How to get your base_url](assets/Readme.png)

4. **Fill in your Indeed profile** — upload your CV and confirm your name, address and phone are filled in. The bot relies on the profile to complete applications.

---

## First Run

1. **Log in manually.** Run the bot:
    ```bash
    python indeed_bot.py
    ```
    If no session cookie is found it opens Indeed and waits for you to log in. Press Enter afterwards and **restart the bot** — the session is now saved in `user_data_dir`.

2. **Run it again.** It will reuse that session.

> **One browser instance at a time.** `user_data_dir` holds a lock while the bot runs. If a previous run was killed instead of closed, Firefox refuses to start and you will see *"Camoufox is already running, but is not responding."* Delete `user_data_dir/lock` (and `.parentlock`), or close any leftover browser window, then retry.

---

## Usage

```bash
python indeed_bot.py
```

The bot will:

1. Open the browser and verify your Indeed session.
2. Visit each search results page and collect all "Indeed Apply" jobs.
3. Deduplicate them by title + URL.
4. If the AI gate is on, ask the model about each title and keep only the ones it answers `true` for.
5. For each surviving job, open it in a new tab, click **Apply now**, and step through the wizard — clicking **Continue**, answering any captcha, and submitting.
6. Print a summary: `Done. Applied: N, failed/skipped: M, of K selected.`

You will be prompted to press Enter whenever manual input is needed (captcha, unanswered form fields, or discarding a job — type `n`).

---

## The AI gate

`ai_evaluation.py` calls `{base_url}/chat/completions` **once per job title**, asking whether that title matches `user_profile_prompt`. The model is instructed to answer with a single word, and the gate is strict:

| Model replies | Result |
|---|---|
| `true`, `True`, `  TRUE  ` | apply |
| `false`, `yes`, `true.`, `sure, true`, empty | skip |
| request fails / times out / bad JSON | skip, with a warning |

Skipping on failure is deliberate — the bot never applies to a job it could not evaluate. If the endpoint is down, every job is skipped and the log says `AI evaluation unavailable … (ConnectionError)`.

Set `user_profile_prompt: ""` to turn the gate off completely.

---

## Platform support

Camoufox ships builds for all three desktop platforms — `OS_MAP` in its package maps `darwin`, `linux` and `win32`, and `ARCH_MAP` covers `x86_64`, `i686` and `arm64` (Apple Silicon and ARM Linux included).

| Platform | Status | Notes |
|---|---|---|
| Linux x86_64 / ARM | Supported | Needs `xvfb` installed if you run headless. |
| macOS Intel / Apple Silicon | Supported | Native ARM builds. |
| Windows x86_64 | Supported | Use `.venv\Scripts\activate` instead of `source`. |

The bot's own code uses nothing platform-specific — no `os.system`, no `subprocess`, no POSIX-only `select` (the earlier `select`-based prompt helper was removed for this reason).

**One real portability caveat:** the captcha clicks use hardcoded pixel coordinates (`page.mouse.click(627, 215)` and `(589, 2688)`). These assume a particular window size. On a different resolution those clicks land somewhere else, and you may need to solve captchas manually. Everything else is resolution-independent.


## Notes & Limitations

- Only works for jobs with "Indeed Apply".
- Captcha handling is best-effort — the coordinates are tuned for one window size, and some captchas need you.
- Indeed changes its site regularly; selectors like `APPLY_BTN_XPATH` are brittle and will need updating.
- The AI gate only sees the job **title** — not the description, company, or salary.
- Run it with judgment. Do not spam applications; bulk submitting is a good way to get your account restricted.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| *"Camoufox is already running, but is not responding"* | A previous run is still alive. Close the browser, or delete `user_data_dir/lock`. |
| `Found 0 Indeed Apply jobs` | Wrong `language`/`base_url` mismatch, or a cloudflare page that never resolved. Check `base_url` opens in a normal browser. |
| *"AI evaluation unavailable"* | Your endpoint at `base_url` is down. Verify with the curl command in `ai_evaluation.py`'s header comment. |
| Click fails with `Timeout 30000ms exceeded` | Increase `SEARCH_SETTLE_SECONDS` / `JOB_PAGE_SETTLE_SECONDS` at the top of `indeed_bot.py` on a slow connection. |
| Bot clicks the wrong thing on captchas | Window size differs from the one the coordinates were tuned for. |
| `'NoneType' object has no attribute 'click'` | Indeed changed the job page layout; update `APPLY_BTN_XPATH` or rely on `APPLY_BTN_SELECTOR`. |

---

## Disclaimer

This project is not affiliated with Indeed. Use at your own risk.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.