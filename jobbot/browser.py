"""Persistent browser session.

The profile directory keeps your LinkedIn/Indeed logins between runs. You log in yourself
(`python -m jobbot login`); the bot never sees or types your passwords.
No stealth or fingerprint-spoofing is used - if a site shows a CAPTCHA the bot pauses for you.
"""
from __future__ import annotations

import random
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from .config import BrowserConfig


class BrowserSession:
    def __init__(self, cfg: BrowserConfig):
        self.cfg = cfg
        self.pw: Playwright | None = None
        self.context: BrowserContext | None = None
        self._pdf_browser: Browser | None = None

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> None:
        self.pw = sync_playwright().start()
        Path(self.cfg.user_data_dir).mkdir(parents=True, exist_ok=True)
        kwargs = dict(user_data_dir=self.cfg.user_data_dir, headless=self.cfg.headless, slow_mo=self.cfg.slow_mo_ms,
                      locale=self.cfg.locale, timezone_id=self.cfg.timezone, viewport={"width": 1366, "height": 860},
                      accept_downloads=False)
        if self.cfg.channel:
            kwargs["channel"] = self.cfg.channel
        self.context = self.pw.chromium.launch_persistent_context(**kwargs)
        self.context.set_default_timeout(15000)

    def new_page(self) -> Page:
        assert self.context is not None
        return self.context.new_page()

    @property
    def pdf_browser(self) -> Browser:
        """Headless browser for PDF rendering (page.pdf() is headless-only)."""
        assert self.pw is not None
        if self._pdf_browser is None:
            self._pdf_browser = self.pw.chromium.launch(headless=True)
        return self._pdf_browser

    def close(self) -> None:
        for closer in (lambda: self._pdf_browser and self._pdf_browser.close(),
                       lambda: self.context and self.context.close(),
                       lambda: self.pw and self.pw.stop()):
            try:
                closer()
            except Exception:  # noqa: BLE001 - shutting down
                pass


def polite_pause(page: Page, lo: float, hi: float) -> None:
    page.wait_for_timeout(int(random.uniform(lo, hi) * 1000))
