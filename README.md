# jobbot: a local job-application agent (Python + Ollama + Qwen)

Finds jobs on LinkedIn and Indeed, scores them against your profile, tailors your CV, writes a cover
letter, and fills in the application: LinkedIn Easy Apply, Indeed Apply, **and multi-page forms on
external company/ATS sites**. UK diversity-monitoring questions are handled automatically. Everything
runs on your machine; the only model is the one served by your local Ollama (default `qwen3:4b`).

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1      # recommended; keeps deps out of your global Python
pip install -e ".[dev]"
python -m playwright install chromium
ollama pull qwen3:4b                                      # and keep `ollama serve` running

python -m jobbot init          # creates config.yaml + profile.yaml
# edit profile.yaml (your facts) and config.yaml (searches, limits)
python -m jobbot check         # verifies profile, model, browser
python -m jobbot login         # log in to LinkedIn / Indeed YOURSELF; the session is kept in data/browser_profile
```

## Two ways to use this

**`fill` - simple, manual, one job at a time.** You paste in an advert, it tailors a CV and cover
letter, then *you* drive the browser: log in, open the application, click Apply, solve any CAPTCHA.
On every page (including later pages of a multi-step form) you press Enter and it fills in whatever
it can from your profile and the advert - using the same LLM (`qwen3:4b`) for the free-text/tailoring
parts. **It never clicks Next or Submit.** This is the one to reach for by default.

```bash
python -m jobbot fill --title "Backend Developer" --company "Acme Robotics" --description-file jd.txt
# or omit --description-file and it asks you to paste the advert; add --url to open straight to the job page
```
```
> [Enter]
[jobbot] filled 8/10 fields on this page
  [profile ] First name                                     -> 'Alex'
  [llm     ] Why do you want to work here?                   -> 'I'm drawn to Acme Robotics because...'
[jobbot] needs your attention (required, could not answer):
   - Current salary: not in your profile - never guessed
>                                    # you click Next in the browser yourself, then press Enter again
```
Commands at the `>` prompt: **Enter** = fill the current page, **`u <url>`** = open a URL, **`n`** = start a new job
(re-tailors documents), **`q`** = quit. The job/CV/cover-letter context is kept for the whole session, so later
pages of the same form don't need the advert re-pasted.

**`run` / `search` / `apply` - autonomous, many jobs, no supervision by default.** Searches LinkedIn/Indeed,
scores postings, and works through the queue clicking Next/Submit itself, asking you to confirm each final
submit unless `auto_submit: true`. This is the higher-risk, higher-effort mode - see "Things you should know"
below before turning it on.

| Command | What it does |
|---|---|
| `python -m jobbot fill ...` | The manual filler described above. **Start here.** |
| `python -m jobbot tailor --title "X" --company "Y" --description-file jd.txt` | Just the tailored CV + cover letter PDFs, no browser at all. |
| `python -m jobbot apply-url <url> --dry-run` | Autonomous: fill a company/ATS form end to end (clicking Next/Submit) but stop before the final submit. |
| `python -m jobbot search` | Find + score new jobs from your configured queries, queue the good ones. |
| `python -m jobbot apply --dry-run` | Work through the queue, filling forms without submitting. |
| `python -m jobbot apply` | Apply for real. By default it asks `Submit? [y/N]` before each final submit. |
| `python -m jobbot run [--forever]` | The autonomous loop: search -> score -> apply, capped per day. |
| `python -m jobbot status` | What was found / applied / needs you. |
| `python -m jobbot answers [set "question" "answer"]` | See or override remembered answers. Your overrides are never replaced by the LLM. |

**Recommended first week:** `tailor` to judge CV/cover-letter quality, then `fill` on a few real postings so you
watch exactly what it enters before anything is submitted. Only reach for the autonomous `run`/`apply` once
you've read a batch of `data/applications/*/answers.json` and are happy with what it writes, and only set
`apply.auto_submit: true` after that.

Each application leaves a folder in `data/applications/<id>-<company>-<title>/` with the exact CV and cover
letter that were generated, `tailoring.json`, and (autonomous mode only) `answers.json` and `final.png`.

## How it works

Both tools share the same field-answering engine (`jobbot/forms/`); they differ only in what decides to move
to the next page.

```
tailor CV (select/reorder your real facts; Qwen writes only the summary) + cover letter
  -> snapshot every field on the page and in iframes (label, type, options, required)
  -> answer each field:  your answer_bank -> eligibility rules -> UK diversity rules -> profile
                         -> remembered answers -> Qwen (only for REQUIRED fields the rules can't answer)
  -> `fill`: report what was filled/left blank, then WAIT - you click Next/Submit yourself
  -> `run`/`apply`: click Next/Continue/Review itself; on the final page: submit (or ask you / dry-run);
     blockers? (CAPTCHA/login wall) -> hand to you;  validation errors? retry with alternate formats, else give up
```

Why deterministic-first: a 4B model is fine at "which option matches this?" and at short grounded text,
but unreliable at facts. So anything derivable from `profile.yaml` never goes near the model, and every
LLM-written sentence is checked (`grounding.py`): any number, employer, or tool/proper noun that isn't in
your profile or the job posting causes the draft to be rejected and regenerated (then a plain template is
used). Qwen's JSON-schema mode is used for anything structured; unconstrained, Qwen3-4B "thinks aloud" in
the answer even with `think: false`.

### UK diversity / equal-opportunities questions
Gender, ethnicity, sexual orientation, religion, disability, age, marital status, caring responsibilities,
veteran status, pregnancy/maternity, socio-economic background, school type, free school meals,
first-generation university... are detected by label, by section heading ("Equal Opportunities"), or by
the options offered. The answer comes **only from `profile.demographics`, default `prefer_not_to_say`**;
nothing is inferred from your CV or name. If you state a value the form has no matching option for, it
falls back to "prefer not to say" rather than picking a different category. If a *required* question offers
no such option, the job is flagged for you rather than guessed.

### What it will not do
* Solve or bypass CAPTCHAs / bot checks. `fill` just tells you it saw one and leaves it alone; the autonomous
  `run`/`apply` pauses and waits for you (`handoff: skip` skips the job instead).
* Type your passwords, create accounts, or complete sign-up walls (Workday-style "create an account" pages
  are left for you; you register once, then continue).
* Invent experience: the CV only ever contains your own bullets/skills, reordered and selected.
* Guess required answers it has no basis for (`skip_if_unsure`), e.g. *current* salary.
* Tick marketing / talent-pool / "follow company" boxes (always declined). Privacy/terms-consent boxes are
  ticked only if `accept_consent_checkboxes: true`.

## Things you should know

* **Terms of service.** LinkedIn and Indeed prohibit automated access; accounts can be restricted. `fill` is the
  lower-risk mode - you do all the clicking and submitting, it only writes into fields you're looking at. The
  autonomous `run`/`apply` goes further (it navigates and submits itself across many jobs); it uses your real
  logged-in session, human-like pacing and a hard daily cap (default 15), but the ToS risk is still yours to
  weigh, and no anti-detection/evasion techniques are used - a site's bot checks are treated as a hard stop, not
  something to defeat. Keep `max_per_day` low.
* **Site adapters are best-effort.** LinkedIn/Indeed change their pages regularly and require login, so the
  adapters in `jobbot/sources/` use layered fallbacks (links and text roles first, class names last) and have
  not been verified against your live account. If a step breaks, the `error.png` / `error.html` (or
  `no_apply_modal.*`) dumped under `data/applications/` show what the bot saw; the selectors are in a short list
  at the top of each adapter file. The *form-filling* engine is generic and was tested on mock ATS flows
  (server-rendered + single-page, conditional questions, custom dropdowns, hidden file inputs), and its field
  extraction/filling was checked against a live public Greenhouse form (filled in the browser, not submitted).
* **Quality of Qwen 4B.** Expect good results on standard fields and multiple-choice questions, and
  serviceable-but-generic free-text answers. Read a few `answers.json` files before turning on auto-submit.
  Each LLM call takes ~5-20 s on CPU/consumer GPU, so a form with several free-text questions takes minutes.
* **Not handled:** applications that only accept `.docx` uploads, video/assessment steps, and email-only applications.
* Some ATSs (Workday, iCIMS, Taleo, Oracle) require accounts: expect `needs_human` there unless you register first.

## Tests
```
python -m pytest tests -q          # unit + browser tests; the e2e/CV tests need Ollama running with the model
```
`tests/mock_ats.py` is a fake ATS (5-page server-rendered flow and a single-page flow) that you can also open
manually: `python tests/mock_ats.py` then visit `/spa` or `/classic/step/1`.
