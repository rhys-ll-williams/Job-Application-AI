"""Search -> filter/score -> apply, with safety limits."""
from __future__ import annotations

import logging
import random
import time
from typing import Callable

from .applicant import Applicant
from .browser import BrowserSession
from .config import Config, Profile
from .db import DB
from .llm import LLMClient
from .matching import hard_filter, score_job
from .sources.base import Posting, Source
from .sources.indeed import Indeed
from .sources.linkedin import LinkedIn

log = logging.getLogger(__name__)
SOURCES: dict[str, Source] = {"linkedin": LinkedIn(), "indeed": Indeed()}
MAX_CONSECUTIVE_PROBLEMS = 5


class Pipeline:
    def __init__(self, cfg: Config, profile: Profile, llm: LLMClient | None, db: DB, session: BrowserSession,
                 notify: Callable[[str], None] = print, confirm: Callable[[str], bool] | None = None):
        self.cfg, self.profile, self.llm, self.db, self.session, self.notify = cfg, profile, llm, db, session, notify
        self.applicant = Applicant(cfg, profile, llm, db, session, SOURCES, notify, confirm)

    def _pause(self, page) -> None:
        page.wait_for_timeout(int(random.uniform(self.cfg.apply.min_delay_s, self.cfg.apply.max_delay_s) * 1000))

    # -------------------------------------------------------------- search ---
    def search(self) -> int:
        """Collect new postings from every configured source/query and score them. Returns number queued."""
        page = self.session.new_page()
        new_ids: list[int] = []
        try:
            for name in self.cfg.search.sources:
                src = SOURCES[name]
                if not src.ensure_ready(page, self.notify, self.cfg.apply.handoff_timeout_s):
                    self.notify(f"[jobbot] {name}: not ready (login / verification needed) - skipping this source")
                    continue
                for q in self.cfg.search.queries:
                    found = 0
                    for posting in src.search(page, q, self.cfg.search):
                        jid = self.db.add_job(source=posting.source, external_id=posting.external_id, url=posting.url,
                                              title="", company="")
                        if jid is not None:
                            new_ids.append(jid)
                            found += 1
                    self.notify(f"[jobbot] {name}: '{q.keywords}' in {q.location} -> {found} new postings")
                    self._pause(page)
            return self._score_new(page, new_ids)
        finally:
            page.close()

    def _score_new(self, page, ids: list[int]) -> int:
        queued = 0
        f = self.cfg.filters
        for n, jid in enumerate(ids, 1):
            row = self.db.get_job(jid)
            src = SOURCES[row["source"]]
            try:
                p = src.details(page, Posting(row["source"], row["external_id"], row["url"]))
            except Exception as exc:  # noqa: BLE001
                self.db.update_job(jid, status="rejected", status_note=f"could not load: {type(exc).__name__}")
                continue
            self.db.update_job(jid, title=p.title, company=p.company, location=p.location,
                               description=p.description[:8000], apply_kind=p.apply_kind)
            info = p.info()
            reason = None
            if p.closed or not p.title:
                reason = "closed / unreadable"
            elif self.cfg.search.easy_apply_only and p.apply_kind != "easy_apply":
                reason = "not an easy-apply job"
            else:
                reason = hard_filter(info, f)
            if reason is None and self.db.seen_same_role(p.title, p.company):
                reason = "already applied / queued via another listing"
            if reason:
                self.db.update_job(jid, status="rejected", status_note=reason)
            else:
                m = score_job(info, self.profile, self.llm)
                ok = m.score >= f.min_match_score and not m.reject
                self.db.update_job(jid, score=m.score, score_reason=m.reason, status="queued" if ok else "rejected",
                                   status_note="" if ok else f"score {m.score} < {f.min_match_score}" if not m.reject else m.reason)
                queued += ok
                self.notify(f"[jobbot] ({n}/{len(ids)}) {p.title} @ {p.company}: score {m.score} -> {'queued' if ok else 'rejected'}")
            self._pause(page)
        return queued

    # --------------------------------------------------------------- apply ---
    def apply_queued(self, limit: int | None = None) -> dict[str, int]:
        results: dict[str, int] = {}
        problems = 0
        done = 0
        while limit is None or done < limit:
            if self.db.applied_today() >= self.cfg.apply.max_per_day:
                self.notify(f"[jobbot] daily cap of {self.cfg.apply.max_per_day} applications reached")
                break
            rows = self.db.jobs_with_status("queued", 1)
            if not rows:
                break
            row = rows[0]
            self.notify(f"[jobbot] applying: {row['title']} @ {row['company']} (score {row['score']})")
            status = self.applicant.apply_to_job(row["id"])
            results[status] = results.get(status, 0) + 1
            done += 1
            self.notify(f"[jobbot]   -> {status}")
            problems = problems + 1 if status in {"failed", "needs_human"} else 0
            if problems >= MAX_CONSECUTIVE_PROBLEMS:
                self.notify(f"[jobbot] {problems} problems in a row - stopping (a site may have changed or is blocking us; "
                            "check data/applications/*/final.png)")
                break
            time.sleep(random.uniform(self.cfg.apply.min_delay_s, self.cfg.apply.max_delay_s) * 3)
        return results
