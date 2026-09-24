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

## Use

| Command | What it does |
|---|---|
| `python -m jobbot tailor --title "X" --company "Y" --description-file jd.txt` | Make the tailored CV + cover letter PDFs for a pasted job description. Touches no website. **Start here** to judge quality. |
| `python -m jobbot apply-url <url> --dry-run` | Fill a company/ATS form (any site) but stop before the final submit. |
| `python -m jobbot search` | Find + score new jobs from your configured queries, queue the good ones. |
| `python -m jobbot apply --dry-run` | Work through the queue, filling forms without submitting. |
| `python -m jobbot apply` | Apply for real. By default it asks `Submit? [y/N]` before each final submit. |
| `python -m jobbot run [--forever]` | The autonomous loop: search -> score -> apply, capped per day. |
| `python -m jobbot status` | What was found / applied / needs you. |
| `python -m jobbot answers [set "question" "answer"]` | See or override remembered answers. Your overrides are never replaced by the LLM. |

**Recommended first week:** `tailor` -> `apply-url --dry-run` on a few real postings -> `apply --dry-run` -> then
`apply` with the default confirm prompt. Only set `apply.auto_submit: true` once you've read a batch of
`data/applications/*/answers.json` and are happy with what it writes.

Each application leaves a folder in `data/applications/<id>-<company>-<title>/` with the exact CV and cover
letter that were sent, `answers.json` (every field, what was entered, and where the answer came from:
`profile` / `demographic` / `bank` / `memory` / `llm`), `tailoring.json`, and `final.png`.

## How it works

```
search (LinkedIn/Indeed adapters) -> hard filters + score (skills overlap + Qwen fit rating)
  -> tailor CV (select/reorder your real facts; Qwen writes only the summary) + cover letter
  -> open apply form (Easy Apply modal / Indeed Apply / follow "Apply" to company site)
  -> FormAgent loop, per page:
       blockers? (CAPTCHA / login wall -> hand to you)   success text? -> done
       snapshot every field on the page and in iframes (label, type, options, required)
       answer each field:  your answer_bank -> eligibility rules -> UK diversity rules -> profile
                           -> remembered answers -> Qwen (only for REQUIRED fields the rules can't answer)
       click Next/Continue/Review; on the final page: submit (or ask you / dry-run)
       validation errors? retry with alternate formats (phone, dates), then give up cleanly
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
* Solve or bypass CAPTCHAs / bot checks (it pauses and waits for you; `handoff: skip` skips the job).
* Type your passwords, create accounts, or complete sign-up walls (Workday-style "create an account" pages
  are handed to you; you register once, the bot continues).
* Invent experience: the CV only ever contains your own bullets/skills, reordered and selected.
* Guess required answers it has no basis for (`skip_if_unsure`), e.g. *current* salary.
* Tick marketing / talent-pool / "follow company" boxes (always declined). Privacy/terms-consent boxes are
  ticked only if `accept_consent_checkboxes: true`.

## Things you should know

* **Terms of service.** LinkedIn and Indeed prohibit automated access; accounts can be restricted. The bot uses
  your real logged-in browser session, human-like pacing and a hard daily cap (default 15), and no evasion
  techniques, but the risk is yours. Keep `max_per_day` low.
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
