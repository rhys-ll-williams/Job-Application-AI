"""Page snapshotting and low-level field interaction (Playwright sync API)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PWError
from playwright.sync_api import Frame, Locator, Page

from .text import best_option, norm

log = logging.getLogger(__name__)
_EXTRACT_JS = (Path(__file__).parent / "extract.js").read_text(encoding="utf-8")


@dataclass
class Option:
    text: str
    value: str = ""
    id: int | None = None
    checked: bool = False
    disabled: bool = False


@dataclass
class Field:
    id: int
    kind: str  # text email tel number date url password textarea select radio checkbox checkbox_group file combobox
    label: str
    frame: Frame
    required: bool = False
    options: list[Option] = field(default_factory=list)
    value: str = ""
    name: str = ""
    placeholder: str = ""
    section: str = ""
    maxlength: int | None = None
    invalid: bool = False
    autocomplete: str = ""
    accept: str = ""
    editable: bool = False
    html_id: str = ""

    @property
    def sig(self) -> str:
        """Stable identity across re-extractions of the same page."""
        return f"{self.kind}|{norm(self.label)}|{self.name}"

    @property
    def has_value(self) -> bool:
        return bool(self.value.strip())

    def locator(self, option: Option | None = None) -> Locator:
        i = option.id if option and option.id is not None else self.id
        return self.frame.locator(f'[data-jb="{i}"]').first


@dataclass
class Button:
    id: int
    text: str
    aria: str
    type: str
    primary: bool
    frame: Frame

    @property
    def label(self) -> str:
        return (self.text or self.aria).strip()

    def locator(self) -> Locator:
        return self.frame.locator(f'[data-jb="{self.id}"]').first


@dataclass
class Snapshot:
    fields: list[Field]
    buttons: list[Button]
    errors: list[str]
    headings: list[str]
    url: str

    @property
    def fingerprint(self) -> str:
        return "|".join(sorted(f.sig for f in self.fields)) + "||" + "|".join(self.headings[:2])


def snapshot(page: Page, scope: str | None = None) -> Snapshot:
    """Extract fields/buttons from the page and all same-page iframes.

    `scope` is an optional CSS selector (e.g. LinkedIn's Easy Apply dialog)."""
    fields: list[Field] = []
    buttons: list[Button] = []
    errors: list[str] = []
    headings: list[str] = []
    for frame in page.frames:
        if frame.is_detached():
            continue
        try:
            data = frame.evaluate(_EXTRACT_JS, {"scope": scope if frame == page.main_frame else None})
        except PWError:
            continue  # cross-origin / navigating frame
        for f in data["fields"]:
            fields.append(Field(
                id=f["id"], kind=f["kind"], label=f["label"], frame=frame, required=f.get("required", False),
                options=[Option(text=o["text"], value=o.get("value", ""), id=o.get("id"),
                                checked=o.get("checked", False), disabled=o.get("disabled", False))
                         for o in f.get("options", [])],
                value=f.get("value", ""), name=f.get("name", ""), placeholder=f.get("placeholder", ""),
                section=f.get("section", ""), maxlength=f.get("maxlength"), invalid=f.get("invalid", False),
                autocomplete=f.get("autocomplete", ""), accept=f.get("accept", ""),
                editable=f.get("editable", False), html_id=f.get("htmlId", ""),
            ))
        for b in data["buttons"]:
            buttons.append(Button(id=b["id"], text=b["text"], aria=b["aria"], type=b["type"],
                                  primary=b["primary"], frame=frame))
        errors.extend(data["errors"])
        headings.extend(data["headings"])
    return Snapshot(fields, buttons, sorted(set(errors)), headings, page.url)


# ------------------------------------------------------------- interaction ---
def _click_option(field: Field, opt: Option) -> None:
    loc = field.locator(opt)
    try:
        loc.check(timeout=2500, force=True)
    except PWError:
        # custom-styled controls: click the label / wrapper instead
        loc.locator("xpath=ancestor-or-self::label[1]").click(timeout=2500)


def read_combobox_options(page: Page, field: Field, typed: str = "") -> list[str]:
    """Open a custom dropdown and read the options it offers (then close it)."""
    loc = field.locator()
    try:
        loc.click(timeout=2500)
        if typed:
            page.keyboard.type(typed, delay=30)
        page.wait_for_timeout(500)
        texts = page.locator('[role="option"]:visible, [role="listbox"] li:visible').all_inner_texts()
        page.keyboard.press("Escape")
    except PWError:
        return []
    return [re.sub(r"\s+", " ", t).strip() for t in texts if t.strip()][:80]


def _fill_combobox(page: Page, field: Field, answer: str) -> bool:
    loc = field.locator()
    is_input = (loc.evaluate("e => e.tagName") or "").upper() in {"INPUT", "TEXTAREA"}
    loc.click(timeout=3000)
    if is_input:
        loc.fill("")
        page.keyboard.type(answer[:40], delay=35)  # typeahead widgets filter as you type
    page.wait_for_timeout(600)
    texts = page.locator('[role="option"]:visible, [role="listbox"] li:visible').all_inner_texts()
    texts = [re.sub(r"\s+", " ", t).strip() for t in texts if t.strip()]
    choice = best_option(answer, texts)
    if choice is None:
        page.keyboard.press("Escape")
        return False
    page.locator('[role="option"]:visible, [role="listbox"] li:visible').filter(has_text=re.compile(re.escape(choice), re.I)).first.click(timeout=3000)
    return True


def fill(page: Page, field: Field, answer: str | list[str] | bool) -> bool:
    """Apply `answer` to the field. Returns True if the widget accepted it."""
    try:
        k = field.kind
        loc = field.locator()
        if k in {"text", "email", "tel", "number", "url", "textarea"}:
            if field.editable:
                loc.click(); page.keyboard.type(str(answer), delay=5)
            else:
                loc.fill(str(answer), timeout=4000)
            return True
        if k == "date":
            loc.fill(str(answer), timeout=4000)
            return True
        if k == "select":
            texts = [o.text for o in field.options if not o.disabled]
            choice = best_option(str(answer), texts)
            if choice is None:
                return False
            loc.select_option(label=choice, timeout=4000)
            return True
        if k == "radio":
            choice = best_option(str(answer), [o.text for o in field.options])
            if choice is None:
                return False
            _click_option(field, next(o for o in field.options if o.text == choice))
            return True
        if k == "checkbox":
            want = answer if isinstance(answer, bool) else norm(str(answer)) in {"yes", "true", "checked", "agree", "i agree"}
            (loc.check if want else loc.uncheck)(timeout=3000, force=True)
            return True
        if k == "checkbox_group":
            wanted = answer if isinstance(answer, list) else [str(answer)]
            ok = False
            for w in wanted:
                choice = best_option(w, [o.text for o in field.options])
                if choice:
                    _click_option(field, next(o for o in field.options if o.text == choice))
                    ok = True
            return ok
        if k == "file":
            loc.set_input_files(str(answer), timeout=8000)
            return True
        if k == "combobox":
            return _fill_combobox(page, field, str(answer))
    except PWError as exc:
        log.warning("could not fill %r (%s): %s", field.label, field.kind, str(exc).splitlines()[0])
    return False
