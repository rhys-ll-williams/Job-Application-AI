"""Apply to one job end to end: tailor documents -> open the form -> run the multi-page agent -> record the result."""
from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path
from typing import Callable

from playwright.sync_api import Page

from .browser import BrowserSession
from .config import Config, Profile
from .cv.cover_letter import write_cover_letter
from .cv.render import application_dir, render_cover_letter, render_cv
from .cv.tailor import tailor
from .db import DB
from .forms.agent import ApplyResult, FormAgent
from .forms.entry import reach_form
from .forms.types import AnswerContext, Documents, JobInfo
from .llm import LLMClient
from .sources.base import Posting, Source

log = logging.getLogger(__name__)
STATUS_MAP = {"submitted": "applied", "unconfirmed": "needs_human", "dry_run": "dry_run",
              "declined": "declined", "needs_human": "needs_human", "failed": "failed"}


class Applicant:
    def __init__(self, cfg: Config, profile: Profile, llm: LLMClient | None, db: DB, session: BrowserSession,
                 sources: dict[str, Source], notify: Callable[[str], None] = print,
                 confirm: Callable[[str], bool] | None = None):
        self.cfg, self.profile, self.llm, self.db = cfg, profile, llm, db
        self.session, self.sources, self.notify, self.confirm = session, sources, notify, confirm

    # ---------------------------------------------------------- documents ---
    def prepare_documents(self, info: JobInfo, job_id: int) -> tuple[Documents, Path]:
        out = application_dir(self.cfg.output_dir, info, job_id)
        docs = Documents(cv_path=self.profile.master_cv_pdf)
        cv = tailor(self.profile, info, self.llm, rewrite_bullets=self.cfg.apply.rewrite_bullets)
        if self.cfg.apply.tailor_cv:
            docs.cv_path = str(render_cv(self.session.pw, cv, out / "cv.pdf", self.session.pdf_browser))
        (out / "tailoring.json").write_text(json.dumps({
            "summary": cv.summary, "matched_keywords": cv.matched_keywords, "missing_keywords": cv.missing_keywords,
        }, indent=2), encoding="utf-8")
        if self.cfg.apply.write_cover_letter:
            letter = write_cover_letter(self.profile, info, cv, self.llm)
            docs.cover_text = letter.text(self.profile, info)
            docs.cover_path = str(render_cover_letter(self.session.pw, self.profile, letter, out / "cover_letter.pdf", self.session.pdf_browser))
            (out / "cover_letter.txt").write_text(docs.cover_text, encoding="utf-8")
        return docs, out

    # -------------------------------------------------------------- debug ---
    def _debug_dump(self, page: Page, out: Path, tag: str) -> None:
        try:
            page.screenshot(path=str(out / f"{tag}.png"), full_page=True)
            (out / f"{tag}.html").write_text(page.content(), encoding="utf-8")
        except Exception:  # noqa: BLE001 - best effort
            pass

    # ---------------------------------------------------------------- core ---
    def _run_form(self, target_page: Page, scope: str | None, info: JobInfo, docs: Documents, out: Path,
                  external: bool) -> ApplyResult:
        if external:
            target_page = reach_form(target_page)
        ctx = AnswerContext(profile=self.profile, job=info, docs=docs, llm=self.llm, apply_cfg=self.cfg.apply, db=self.db)
        agent = FormAgent(target_page, ctx, scope=scope, confirm=self.confirm, notify=self.notify,
                          describe=f"{info.title} at {info.company}")
        result = agent.run()
        (out / "answers.json").write_text(json.dumps(result.log, indent=2, default=str), encoding="utf-8")
        self._debug_dump(target_page, out, "final")
        return result

    def apply_to_job(self, job_id: int) -> str:
        row = self.db.get_job(job_id)
        source = self.sources[row["source"]]
        posting = Posting(row["source"], row["external_id"], row["url"])
        pages_before = set(self.session.context.pages)  # so we can close everything this job opens (incl. new tabs)
        page = self.session.new_page()
        out = Path(self.cfg.output_dir)
        try:
            posting = source.details(page, posting)
            if posting.closed:
                self.db.mark_applied(job_id, "rejected", "posting closed or already applied")
                return "rejected"
            info = posting.info()
            docs, out = self.prepare_documents(info, job_id)
            target = source.begin_apply(page, posting)
            if target is None:
                self._debug_dump(page, out, "no_apply_modal")
                self.db.mark_applied(job_id, "failed", "could not open the application form")
                return "failed"
            result = self._run_form(target.page, target.scope, info, docs, out, external=target.kind == "external")
            if result.status == "submitted":
                getattr(source, "after_success", lambda p: None)(page)
        except Exception as exc:  # noqa: BLE001 - one bad job must not stop the run
            log.error("apply failed for job %s: %s", job_id, traceback.format_exc())
            self._debug_dump(page, out, "error")
            result = ApplyResult("failed", f"{type(exc).__name__}: {str(exc)[:200]}")
            docs = Documents()
        finally:
            for p in list(self.session.context.pages):
                if p not in pages_before:
                    try:
                        p.close()
                    except Exception:  # noqa: BLE001
                        pass
        status = STATUS_MAP[result.status]
        self.db.mark_applied(job_id, status, result.note, docs.cv_path, docs.cover_path)
        self.db.log_event(job_id, f"apply:{result.status}", {"note": result.note, "pages": result.pages})
        return status

    def apply_to_url(self, url: str) -> str:
        """Apply to any company / ATS page directly (no job board involved)."""
        page = self.session.new_page()
        out = Path(self.cfg.output_dir)
        try:
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            title = (page.title() or "").strip()
            body = page.inner_text("body")[:3500]
            company = page.evaluate("() => (document.querySelector('meta[property=\"og:site_name\"]')||{}).content || location.hostname")
            info = JobInfo(title=title[:120], company=str(company)[:80], description=body, url=url)
            job_id = self.db.add_job(source="direct", external_id=url, url=url, title=info.title, company=info.company,
                                     description=body, apply_kind="external")
            if job_id is None:
                job_id = self.db.conn.execute("SELECT id FROM jobs WHERE source='direct' AND external_id=?", (url,)).fetchone()["id"]
            docs, out = self.prepare_documents(info, job_id)
            result = self._run_form(page, None, info, docs, out, external=True)
            self.db.mark_applied(job_id, STATUS_MAP[result.status], result.note, docs.cv_path, docs.cover_path)
            self.notify(f"[jobbot] {result.status}: {result.note} (files in {out})")
            return STATUS_MAP[result.status]
        finally:
            page.close()
