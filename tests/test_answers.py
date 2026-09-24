"""Offline tests for the answer engine (no browser, no LLM)."""
from pathlib import Path

import pytest

from jobbot.config import load_profile
from jobbot.forms.answers import answer_field
from jobbot.forms.dom import Field, Option
from jobbot.forms.text import best_option, pick_numeric_band
from jobbot.forms.types import AnswerContext, Documents, JobInfo

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def ctx():
    return AnswerContext(profile=load_profile(ROOT / "profile.example.yaml"), job=JobInfo(title="Python Developer", company="Acme"),
                         docs=Documents(cv_path="cv.pdf", cover_path="cl.pdf"), llm=None)


def F(label, kind="text", options=(), required=False, section="", **kw):
    return Field(id=1, kind=kind, label=label, frame=None, required=required, section=section,
                 options=[Option(text=o) for o in options], **kw)


def test_basic_contact_fields(ctx):
    assert answer_field(F("First name", required=True), ctx).value == "Alex"
    assert answer_field(F("Surname"), ctx).value == "Morgan"
    assert answer_field(F("Email address", kind="email"), ctx).value == "alex.morgan@example.com"
    assert answer_field(F("Mobile phone number", kind="tel"), ctx).value == "07700 900123"
    assert answer_field(F("Postcode"), ctx).value == "M1 1AA"
    assert answer_field(F("Please state your expected salary"), ctx).value == "34000"


def test_right_to_work_and_sponsorship(ctx):
    a = answer_field(F("Do you have the right to work in the UK?", "radio", ["Yes", "No"]), ctx)
    assert a.value == "Yes"
    a = answer_field(F("Will you now or in the future require sponsorship?", "radio", ["Yes", "No"]), ctx)
    assert a.value == "No"


def test_over_18_is_not_an_age_range_question(ctx):
    a = answer_field(F("Are you over the age of 18?", "radio", ["Yes", "No"]), ctx)
    assert a.value == "Yes" and a.source == "profile"


def test_demographics_default_to_prefer_not_to_say(ctx):
    for label, opts in [
        ("What is your gender?", ["Male", "Female", "Non-binary", "Prefer not to say"]),
        ("Ethnic group", ["White", "Asian or Asian British", "Black or Black British", "I do not wish to disclose"]),
        ("Sexual orientation", ["Heterosexual", "Gay or lesbian", "Bisexual", "Prefer not to answer"]),
        ("Religion or belief", ["No religion", "Christian", "Muslim", "Rather not say"]),
        ("Do you have a disability?", ["Yes", "No", "Prefer not to say"]),
    ]:
        a = answer_field(F(label, "select", opts, required=True), ctx)
        assert a.source == "demographic"
        assert a.value == opts[-1], label


def test_demographic_detected_from_options_when_label_is_generic(ctx):
    opts = ["White British", "Asian or Asian British - Indian", "Black or Black British - African", "Mixed", "Prefer not to say"]
    a = answer_field(F("Please select", "select", opts, required=True), ctx)
    assert a.value == "Prefer not to say"


def test_demographic_optional_without_pnts_option_is_left_blank(ctx):
    a = answer_field(F("Gender", "select", ["Male", "Female"], required=False), ctx)
    assert a.value is None and a.confident


def test_demographic_required_without_pnts_option_is_unconfident(ctx):
    a = answer_field(F("Gender", "select", ["Male", "Female"], required=True), ctx)
    assert a.value is None and not a.confident


def test_user_stated_demographic_is_used_only_when_matching(ctx):
    ctx.profile.demographics.ethnicity = "Asian or Asian British - Indian"
    opts = ["White", "Asian or Asian British - Indian", "Prefer not to say"]
    assert answer_field(F("Ethnicity", "select", opts, required=True), ctx).value == "Asian or Asian British - Indian"
    # stated value has no matching category -> falls back to prefer-not-to-say, never a different category
    assert answer_field(F("Ethnicity", "select", ["White", "Black", "Prefer not to say"], required=True), ctx).value == "Prefer not to say"


def test_unknown_question_in_eeo_section_defaults_to_pnts(ctx):
    a = answer_field(F("Which of these best describes you?", "select", ["A", "B", "Prefer not to say"], section="Equal Opportunities Monitoring"), ctx)
    assert a.value == "Prefer not to say"


def test_marketing_declined_consent_ticked(ctx):
    assert answer_field(F("Sign me up to the newsletter", "checkbox"), ctx).value is False
    assert answer_field(F("I agree to the privacy policy", "checkbox", required=True), ctx).value is True
    ctx.apply_cfg.accept_consent_checkboxes = False
    assert not answer_field(F("I agree to the privacy policy", "checkbox", required=True), ctx).confident


def test_files(ctx):
    assert answer_field(F("Upload your CV", "file", required=True), ctx).value == "cv.pdf"
    assert answer_field(F("Cover letter", "file"), ctx).value == "cl.pdf"
    assert answer_field(F("Portfolio samples", "file"), ctx).value is None


def test_current_salary_is_never_guessed(ctx):
    a = answer_field(F("Current salary", required=True), ctx)
    assert a.value is None and not a.confident


def test_salary_band_and_notice_options(ctx):
    opts = ["Under £25,000", "£25,000 - £30,000", "£30,001 - £35,000", "£35,001+"]
    assert answer_field(F("Salary expectation", "select", opts), ctx).value == "£30,001 - £35,000"
    assert answer_field(F("Notice period", "select", ["Immediately", "2 weeks", "1 month", "3 months"]), ctx).value == "1 month"


def test_answer_bank_overrides(ctx):
    a = answer_field(F("What is your strongest programming language?"), ctx)
    assert a.value == "Python" and a.source == "bank"


def test_password_needs_human(ctx):
    assert not answer_field(F("Password", "password", required=True), ctx).confident


def test_option_matching_helpers():
    assert best_option("yes", ["Yes, I do", "No"]) == "Yes, I do"
    assert best_option("no", ["Yes", "No preference", "No"]) in {"No", "No preference"}
    assert best_option("prefer_not_to_say", ["Male", "Female", "I'd rather not say"]) == "I'd rather not say"
    assert best_option("Manchester", ["London", "Manchester, UK"]) == "Manchester, UK"
    assert best_option("banana", ["London", "Leeds"]) is None
    assert pick_numeric_band(4, ["0-1 years", "2-3 years", "4-5 years", "6+ years"]) == "4-5 years"
    assert pick_numeric_band(8, ["0-1 years", "6+ years"]) == "6+ years"


def test_grounding_flags_invented_facts():
    from jobbot.grounding import ungrounded
    src = "Python developer at Northern Data Ltd with FastAPI and PostgreSQL for 2 years. Job: Junior Developer at Acme Robotics"
    ok = "I built FastAPI services at Northern Data Ltd and enjoy working with PostgreSQL. I am keen to join Acme Robotics."
    assert ungrounded(ok, src) == []
    assert "Kubernetes" in ungrounded("I also deployed everything on Kubernetes at scale.", src)
    assert "40%" in ungrounded("I cut query time by 40% for the team.", src)
    assert "Google" in ungrounded("Before this I worked at Google as an intern.", src)
    assert ungrounded("Their focus on warehouse robots is appealing. 2 years of Python.", src) == []


def test_generic_attach_label_uses_html_id_and_first_unknown_upload_is_cv(ctx):
    # real Greenhouse markup: label "Attach", id "resume" / "cover_letter"
    assert answer_field(F("Attach", "file", html_id="resume"), ctx).value == "cv.pdf"
    assert answer_field(F("Attach", "file", html_id="cover_letter"), ctx).value == "cl.pdf"
    # unlabelled first upload -> CV; once the CV is in, further unknown optional uploads are left alone
    assert answer_field(F("Attach", "file"), ctx).value == "cv.pdf"
    ctx.docs.cv_uploaded = True
    assert answer_field(F("Attach", "file"), ctx).value is None


def test_experience_questions_come_from_the_profile(ctx):
    # years with a skill = time in roles that used it, not total career length
    assert answer_field(F("How many years of experience do you have with Python?", "number"), ctx).value == "2"
    assert answer_field(F("How many years of experience do you have in JavaScript?", "number"), ctx).value in {"0", "1"}  # internship only
    assert answer_field(F("Years of experience with Kubernetes", "number"), ctx).value == "0"
    # yes/no experience questions: yes only if the profile says so, never invented
    assert answer_field(F("Do you have experience with Docker?", "radio", ["Yes", "No"]), ctx).value == "Yes"
    a = answer_field(F("Do you have experience with Kubernetes?", "radio", ["Yes", "No"]), ctx)
    assert a.value == "No" and a.source == "profile"
    # total years still answers the generic question
    assert answer_field(F("Total years of professional experience"), ctx).value == "2"


def test_phone_country_code_prefers_uk_over_crown_dependencies(ctx):
    opts = ["Guernsey +44", "Isle of Man +44", "Jersey +44", "United Kingdom +44", "United States +1"]
    assert answer_field(F("Country code", "select", opts), ctx).value == "United Kingdom +44"
    assert answer_field(F("Phone country code", "select", ["+1", "+44", "+353"]), ctx).value == "+44"


def test_generic_experience_phrases_are_not_treated_as_skills(ctx):
    for label in ["Years of professional experience", "How many years of relevant experience do you have?",
                  "How many years of experience do you have in a similar role?", "Total years of experience"]:
        a = answer_field(F(label, "number"), ctx)
        assert a.value == "2" and a.note == "", label  # total career years via the general rule
    assert answer_field(F("How many years of Python experience do you have?", "number"), ctx).note.startswith("2y with python")
