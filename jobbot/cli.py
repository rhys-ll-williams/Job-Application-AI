"""Command line entry point:  python -m jobbot <command>"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .browser import BrowserSession
from .config import Config, Profile, load_config, load_profile
from .db import DB
from .forms.norm_key import norm_key
from .forms.types import JobInfo
from .llm import LLMError, OllamaLLM

console = Console()
ROOT = Path(__file__).resolve().parent.parent


def _notify(msg: str) -> None:
    console.print(msg.replace("\a", ""), highlight=False)
    if "\a" in msg:
        sys.stdout.write("\a")
        sys.stdout.flush()


def _setup(args, need_llm: bool = True):
    cfg = load_config(args.config)
    if getattr(args, "dry_run", False):
        cfg.apply.dry_run = True
    if getattr(args, "auto_submit", False):
        cfg.apply.auto_submit = True
    if getattr(args, "headless", False):
        cfg.browser.headless = True
    profile = load_profile(cfg.profile_path)
    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s", handlers=[
        RichHandler(console=console, show_path=False, show_time=False, level=logging.WARNING),
        logging.FileHandler(Path(cfg.db_path).parent / "jobbot.log", encoding="utf-8")])
    llm = None
    if need_llm:
        llm = OllamaLLM(cfg.llm)
        llm.check()
    return cfg, profile, llm, DB(cfg.db_path)


# --------------------------------------------------------------- commands ---
def cmd_init(args) -> None:
    for src, dst in (("config.example.yaml", "config.yaml"), ("profile.example.yaml", "profile.yaml")):
        if Path(dst).exists():
            console.print(f"{dst} already exists - left untouched")
        else:
            shutil.copy(ROOT / src, dst)
            console.print(f"created {dst}")
    console.print("\nNext: edit profile.yaml and config.yaml, then run  [bold]python -m jobbot check[/bold]")


def cmd_check(args) -> None:
    ok = True
    cfg = load_config(args.config)
    profile = load_profile(cfg.profile_path)
    console.print(f"[green]ok[/green] profile for {profile.personal.full_name}; "
                  f"{sum(len(j.bullets) for j in profile.experience)} experience bullets, {len(profile.skills)} skill groups")
    llm = OllamaLLM(cfg.llm)
    try:
        llm.check()
        t = time.time()
        out = llm.json("Reply in JSON.", "Return {\"ok\": true}", {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}, max_tokens=20)
        console.print(f"[green]ok[/green] Ollama model {cfg.llm.model} answered in {time.time() - t:.1f}s: {out}")
    except LLMError as exc:
        ok = False
        console.print(f"[red]FAIL[/red] {exc}")
    try:
        with BrowserSession(cfg.browser.model_copy(update={"headless": True, "user_data_dir": str(Path(cfg.browser.user_data_dir).parent / "check_profile")})) as s:
            s.new_page().goto("about:blank")
        console.print("[green]ok[/green] Playwright Chromium launches")
    except Exception as exc:  # noqa: BLE001
        ok = False
        console.print(f"[red]FAIL[/red] Playwright: {exc}\n  run: python -m playwright install chromium")
    demo = profile.demographics.model_dump()
    stated = {k: v for k, v in demo.items() if v not in ("prefer_not_to_say", "")}
    console.print("diversity questions: " + (f"you have set {sorted(stated)}; everything else is 'prefer not to say'" if stated else "all 'prefer not to say'"))
    sys.exit(0 if ok else 1)


def cmd_login(args) -> None:
    cfg = load_config(args.config)
    urls = {"linkedin": "https://www.linkedin.com/login", "indeed": "https://secure.indeed.com/auth?hl=en_GB"}
    cfg.browser.headless = False
    with BrowserSession(cfg.browser) as s:
        for site in (args.site and [args.site] or list(urls)):
            page = s.new_page()
            page.goto(urls[site])
            console.print(f"Log in to {site} in the browser window (including any 2FA / verification).")
        input("Press Enter here when you are done logging in... ")
    console.print("Session saved in", cfg.browser.user_data_dir)


def cmd_search(args) -> None:
    cfg, profile, llm, db = _setup(args)
    from .pipeline import Pipeline
    with BrowserSession(cfg.browser) as s:
        n = Pipeline(cfg, profile, llm, db, s, _notify).search()
    console.print(f"[bold]{n}[/bold] jobs queued. See them with: python -m jobbot status")


def _confirm(prompt: str) -> bool:
    if not sys.stdin or not sys.stdin.isatty():
        return False
    sys.stdout.write("\a")
    return input(f"{prompt} Submit? [y/N] ").strip().lower() in {"y", "yes"}


def cmd_apply(args) -> None:
    cfg, profile, llm, db = _setup(args)
    from .pipeline import Pipeline
    with BrowserSession(cfg.browser) as s:
        res = Pipeline(cfg, profile, llm, db, s, _notify, _confirm).apply_queued(args.limit)
    console.print("result:", res or "nothing queued")


def cmd_apply_url(args) -> None:
    cfg, profile, llm, db = _setup(args)
    from .applicant import Applicant
    from .pipeline import SOURCES
    with BrowserSession(cfg.browser) as s:
        Applicant(cfg, profile, llm, db, s, SOURCES, _notify, _confirm).apply_to_url(args.url)


def cmd_run(args) -> None:
    cfg, profile, llm, db = _setup(args)
    from .pipeline import Pipeline
    while True:
        with BrowserSession(cfg.browser) as s:
            pipe = Pipeline(cfg, profile, llm, db, s, _notify, _confirm)
            pipe.search()
            res = pipe.apply_queued()
        _notify(f"[jobbot] cycle finished: {res or 'nothing applied'}")
        if not args.forever:
            break
        _notify(f"[jobbot] sleeping {args.interval_hours}h (Ctrl+C to stop)")
        time.sleep(args.interval_hours * 3600)


def cmd_status(args) -> None:
    cfg = load_config(args.config)
    db = DB(cfg.db_path)
    console.print("counts:", db.counts() or "no jobs yet")
    t = Table("id", "status", "score", "title", "company", "note", show_lines=False)
    for r in db.conn.execute("SELECT * FROM jobs WHERE status!='new' ORDER BY id DESC LIMIT ?", (args.n,)):
        t.add_row(str(r["id"]), r["status"], str(r["score"] or ""), (r["title"] or "")[:40], (r["company"] or "")[:26], (r["status_note"] or "")[:60])
    console.print(t)


def cmd_tailor(args) -> None:
    """Generate the tailored CV + cover letter for a pasted job description (touches no website)."""
    cfg, profile, llm, db = _setup(args)
    from .applicant import Applicant
    desc = Path(args.description_file).read_text(encoding="utf-8")
    info = JobInfo(title=args.title, company=args.company, description=desc)
    with BrowserSession(cfg.browser.model_copy(update={"headless": True})) as s:
        docs, out = Applicant(cfg, profile, llm, db, s, {}, _notify).prepare_documents(info, 0)
    console.print(f"wrote CV and cover letter to [bold]{out}[/bold]")


def cmd_answers(args) -> None:
    cfg = load_config(args.config)
    db = DB(cfg.db_path)
    if args.action == "set":
        db.save_answer(norm_key(args.question), args.question, args.answer, "user")
        console.print("saved (user answers are never overwritten by the LLM)")
        return
    t = Table("source", "question", "answer")
    for r in db.all_answers():
        t.add_row(r["source"], r["question"][:60], r["answer"][:70])
    console.print(t)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="jobbot", description="Local LLM job application agent")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_, **flags):
        sp = sub.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        if flags.get("run_flags"):
            sp.add_argument("--dry-run", action="store_true", help="fill every page but never press the final submit")
            sp.add_argument("--auto-submit", action="store_true", help="submit without asking (overrides config)")
            sp.add_argument("--headless", action="store_true", help="hide the browser (no CAPTCHA hand-off possible)")
        return sp

    add("init", cmd_init, "create config.yaml and profile.yaml from the examples")
    add("check", cmd_check, "verify profile, Ollama model and browser")
    sp = add("login", cmd_login, "log in to LinkedIn/Indeed yourself; the session is kept")
    sp.add_argument("site", nargs="?", choices=["linkedin", "indeed"])
    add("search", cmd_search, "find and score new jobs", run_flags=True)
    sp = add("apply", cmd_apply, "apply to queued jobs", run_flags=True)
    sp.add_argument("--limit", type=int)
    sp = add("apply-url", cmd_apply_url, "apply to a company/ATS page directly", run_flags=True)
    sp.add_argument("url")
    sp = add("run", cmd_run, "search, score and apply (the autonomous loop)", run_flags=True)
    sp.add_argument("--forever", action="store_true")
    sp.add_argument("--interval-hours", type=float, default=6)
    sp = add("status", cmd_status, "show what has been found/applied")
    sp.add_argument("-n", type=int, default=25)
    sp = add("tailor", cmd_tailor, "generate CV + cover letter for a job description file")
    sp.add_argument("--title", required=True)
    sp.add_argument("--company", required=True)
    sp.add_argument("--description-file", required=True)
    sp = add("answers", cmd_answers, "list or override remembered answers")
    sp.add_argument("action", choices=["list", "set"], nargs="?", default="list")
    sp.add_argument("question", nargs="?")
    sp.add_argument("answer", nargs="?")

    args = p.parse_args(argv)
    try:
        args.fn(args)
    except (FileNotFoundError, LLMError) as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)
    except KeyboardInterrupt:
        console.print("\nstopped")
        sys.exit(130)
