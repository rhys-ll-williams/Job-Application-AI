"""A tiny fake ATS used to test the form agent end to end.

  /classic/step/N   server-rendered, full page loads, server-side validation
  /spa              single page, JS-driven steps, conditional question, client-side validation
Submitted data is kept in SUBMISSIONS for assertions.
"""
from __future__ import annotations

import cgi
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SUBMISSIONS: list[dict] = []
SESSION: dict = {}
COOKIE_CHOICES: list[str] = []

BANNER = """<div id="cookie-banner" class="cookie-banner"><p>We use cookies.</p>
<button type="button" onclick="cc('accept')">Accept all</button>
<button type="button" onclick="cc('reject')">Reject all</button></div>
<script>function cc(v){fetch('/cookie?c='+v);document.getElementById('cookie-banner').remove();}</script>"""

CSS = "<style>body{font-family:sans-serif;max-width:640px;margin:2em auto}label{display:block;margin-top:1em}.error{color:#b00}fieldset{margin-top:1em}" \
      "#lb{list-style:none;border:1px solid #999;padding:0;margin:0;display:none}#lb li{padding:4px;cursor:pointer}</style>"

S1 = """<h2>Your details</h2>
<label for="fn">First name *</label><input id="fn" name="first_name" required>
<label for="ln">Last name *</label><input id="ln" name="last_name" required>
<label for="em">Email *</label><input id="em" name="email" type="email" required>
<label for="ph">Phone *</label><input id="ph" name="phone" type="tel" required>
<label for="ctry">Country *</label><select id="ctry" name="country" required><option value="">Select...</option>
<option>Ireland</option><option>United Kingdom</option><option>United States</option></select>
<label>Upload your CV * <span class="btn">Attach</span><input type="file" id="cv" name="cv" required style="position:absolute;left:-9999px"></label>
<label>Cover letter <input type="file" id="cl" name="cover_letter"></label>"""

S2 = """<h2>Eligibility</h2>
<fieldset><legend>Do you have the right to work in the UK? *</legend>
<label><input type="radio" name="rtw" value="yes" required> Yes</label><label><input type="radio" name="rtw" value="no"> No</label></fieldset>
<fieldset><legend>Will you now or in the future require sponsorship? *</legend>
<label><input type="radio" name="spons" value="yes" required> Yes, I will require sponsorship</label>
<label><input type="radio" name="spons" value="no"> No, I will not require sponsorship</label></fieldset>
<label for="np">Notice period *</label><select id="np" name="notice" required><option value="">Please select</option>
<option>Immediately</option><option>2 weeks</option><option>1 month</option><option>3 months</option></select>
<label for="sal">Salary expectation *</label><select id="sal" name="salary" required><option value="">Please select</option>
<option>Under £25,000</option><option>£25,000 - £30,000</option><option>£30,001 - £35,000</option><option>£35,001+</option></select>
<label for="how">How did you hear about us? *</label>
<input id="how" name="how" role="combobox" aria-expanded="false" autocomplete="off" required onfocus="lbShow()" onclick="lbShow()">
<ul id="lb" role="listbox"><li role="option" onclick="pick(this)">LinkedIn</li><li role="option" onclick="pick(this)">Indeed</li>
<li role="option" onclick="pick(this)">Company website</li><li role="option" onclick="pick(this)">Friend or colleague</li></ul>
<script>function lbShow(){document.getElementById('lb').style.display='block'}
function pick(li){var i=document.getElementById('how');i.value=li.textContent;document.getElementById('lb').style.display='none'}</script>
<label><input type="checkbox" name="marketing" value="1"> Keep me informed about future opportunities</label>"""

S3 = """<h2>Application questions</h2>
<label for="why">Why do you want to work at Acme Robotics? *</label><textarea id="why" name="why" rows="5" required maxlength="900"></textarea>
<fieldset><legend>Do you have experience with Python? *</legend>
<label><input type="radio" name="py" value="yes" required onchange="pyq(true)"> Yes</label>
<label><input type="radio" name="py" value="no" onchange="pyq(false)"> No</label></fieldset>
<div id="pyw" style="display:none"><label for="pyy">How many years of Python experience do you have? *</label>
<input id="pyy" name="py_years" type="number"></div>
<script>function pyq(v){var w=document.getElementById('pyw');if(w)w.style.display=v?'block':'none'}</script>"""

S4 = """<h2>Equal Opportunities Monitoring (voluntary)</h2><p>This information is used for monitoring only.</p>
<label for="gen">What is your gender? *</label><select id="gen" name="gender" required><option value="">Select</option>
<option>Male</option><option>Female</option><option>Non-binary</option><option>Prefer not to say</option></select>
<fieldset><legend>Which ethnic group do you belong to? *</legend>
<label><input type="radio" name="eth" value="1" required> White British</label>
<label><input type="radio" name="eth" value="2"> Asian or Asian British - Indian</label>
<label><input type="radio" name="eth" value="3"> Black or Black British - African</label>
<label><input type="radio" name="eth" value="4"> Mixed or multiple ethnic groups</label>
<label><input type="radio" name="eth" value="5"> I do not wish to disclose</label></fieldset>
<fieldset><legend>Do you consider yourself to have a disability? *</legend>
<label><input type="radio" name="dis" value="y" required> Yes</label><label><input type="radio" name="dis" value="n"> No</label>
<label><input type="radio" name="dis" value="p"> Prefer not to say</label></fieldset>
<label for="rel">Religion or belief</label><select id="rel" name="religion"><option value="">Select</option>
<option>No religion</option><option>Christian</option><option>Muslim</option><option>Hindu</option><option>I would rather not say</option></select>
<label for="so">Sexual orientation</label><select id="so" name="orientation"><option value="">Select</option>
<option>Heterosexual</option><option>Gay or lesbian</option><option>Bisexual</option><option>Prefer not to say</option></select>"""

S5 = """<h2>Review and submit</h2><p>Please review your answers before submitting.</p>
<label><input type="checkbox" name="consent" value="1" required> I confirm the information is true and I agree to the privacy policy *</label>"""

STEPS = {1: S1, 2: S2, 3: S3, 4: S4, 5: S5}
REQUIRED = {1: ["first_name", "last_name", "email", "phone", "country", "cv"], 2: ["rtw", "spons", "notice", "salary", "how"],
            3: ["why", "py"], 4: ["gender", "eth", "dis"], 5: ["consent"]}


def page(body: str) -> bytes:
    return f"<!doctype html><html><head><meta charset=utf-8><title>Acme ATS</title>{CSS}</head><body>{BANNER}{body}</body></html>".encode()


def classic(step: int, errors: list[str]) -> bytes:
    err = "".join(f'<div role="alert" class="error">{e}</div>' for e in errors)
    last = step == 5
    buttons = ('<button type="button" onclick="history.back()">Back</button> <button type="button">Save for later</button> '
               + ('<button type="submit">Submit application</button>' if last else '<button type="submit">Save and continue</button>'))
    return page(f'<h1>Acme Robotics - Junior Developer</h1><p>Step {step} of 5</p>{err}'
                f'<form method="post" action="/classic/step/{step}" enctype="multipart/form-data" novalidate>{STEPS[step]}<p>{buttons}</p></form>')


SPA = None


def spa() -> bytes:
    global SPA
    if SPA is None:
        secs = "".join(f'<section id="s{i}" style="display:{"block" if i == 1 else "none"}" data-step="{i}">{STEPS[i]}</section>' for i in STEPS)
        SPA = page("""<h1>Acme Robotics - Junior Developer</h1><div id="errs"></div>
<form id="f" novalidate>__SECS__<p><button type="button" id="back">Back</button> <button type="button" id="next">Next</button></p></form>
<div id="done" style="display:none"><h2>Thank you for applying</h2><p>Your application has been submitted.</p></div>
<script>
var step=1,N=5,nb=document.getElementById('next'),errs=document.getElementById('errs');
function ok(sec){var bad=[];sec.querySelectorAll('[required]').forEach(function(e){
 if(e.type==='radio'){if(!sec.querySelector('input[name="'+e.name+'"]:checked'))bad.push(e.name)}
 else if(e.type==='checkbox'){if(!e.checked)bad.push(e.name)} else if(e.offsetParent!==null&&!e.value)bad.push(e.name)});
 return Array.from(new Set(bad))}
nb.onclick=function(){var sec=document.getElementById('s'+step),bad=ok(sec);
 if(bad.length){errs.innerHTML='<div role="alert" class="error">Please complete: '+bad.join(', ')+'</div>';return}
 errs.innerHTML='';
 if(step<N){sec.style.display='none';step++;document.getElementById('s'+step).style.display='block';
  nb.textContent=step===N?'Submit application':'Next';return}
 var fd=new FormData(document.getElementById('f'));
 fetch('/spa/submit',{method:'POST',body:fd}).then(function(){document.getElementById('f').style.display='none';document.getElementById('done').style.display='block'})};
document.getElementById('back').onclick=function(){if(step>1){document.getElementById('s'+step).style.display='none';step--;document.getElementById('s'+step).style.display='block'}};
</script>""".replace("__SECS__", secs))
    return SPA


def _form_to_dict(fs: cgi.FieldStorage) -> dict:
    out: dict = {}
    for k in fs.keys():
        item = fs[k]
        items = item if isinstance(item, list) else [item]
        vals = []
        for it in items:
            vals.append(f"<file:{it.filename}>" if it.filename else it.value) if (it.filename or it.value) else None
        if vals:
            out[k] = vals[0] if len(vals) == 1 else vals
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, body: bytes, code=200, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/cookie":
            COOKIE_CHOICES.append(u.query.split("=")[-1])
            return self._send(b"ok")
        if u.path == "/spa":
            return self._send(spa())
        m = re.fullmatch(r"/classic/step/(\d)", u.path)
        if m:
            return self._send(classic(int(m.group(1)), []))
        if u.path == "/recaptcha/api2/anchor":
            return self._send(page("<div>reCAPTCHA</div>"))
        if u.path == "/captcha/invisible":  # v3-style badge: present on most ATS pages, needs no human
            return self._send(page('<h1>Form</h1><iframe src="/recaptcha/api2/anchor?size=invisible" '
                                   'style="position:fixed;right:0;bottom:14px;width:256px;height:60px"></iframe>'))
        if u.path == "/captcha/visible":  # v2 checkbox challenge: a human has to click it
            return self._send(page('<h1>Form</h1><iframe src="/recaptcha/api2/anchor?size=normal" width="304" height="78"></iframe>'))
        if u.path == "/captcha/cloudflare":
            return self._send(page("<h1>Just a moment...</h1><p>Checking your browser before accessing the site.</p>"))
        if u.path == "/job/1":  # careers-page landing: description + an Apply button, like most company sites
            return self._send(page("<h1>Backend Python Developer</h1><p>Acme Robotics builds warehouse robots. You will build FastAPI "
                                   "services, write SQL against PostgreSQL and containerise apps with Docker. Hybrid in Manchester.</p>"
                                   '<p><a role="button" href="/classic/step/1">Apply now</a></p>'))
        if u.path == "/advert/generic":  # a job-board/company page with its own <title> and og:site_name, unlike page()
            body = ("<!doctype html><html><head><meta charset=utf-8>"
                   "<title>Backend Python Developer - Acme Robotics</title>"
                   '<meta property="og:site_name" content="Acme Robotics">'
                   f"{CSS}</head><body><h1>Backend Python Developer</h1><p>Acme Robotics builds warehouse robots. "
                   "You will build FastAPI services, write SQL against PostgreSQL and containerise apps with Docker. "
                   "Hybrid in Manchester.</p></body></html>").encode()
            return self._send(body)
        if u.path == "/classic/done":
            return self._send(page("<h1>Thank you for applying!</h1><p>We have received your application.</p>"))
        self._send(page("<h1>Acme ATS</h1>"), 200)

    def do_POST(self):
        u = urlparse(self.path)
        fs = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers["Content-Type"]})
        data = _form_to_dict(fs)
        if u.path == "/spa/submit":
            SUBMISSIONS.append({"flow": "spa", **data})
            return self._send(b"ok")
        m = re.fullmatch(r"/classic/step/(\d)", u.path)
        if not m:
            return self._send(b"nope", 404)
        step = int(m.group(1))
        errors = [f"{k} is required" for k in REQUIRED[step] if not data.get(k)]
        if step == 1 and data.get("phone") and len(re.sub(r"\D", "", data["phone"])) < 10:
            errors.append("Enter a valid phone number")
        if errors:
            return self._send(classic(step, errors))
        SESSION.update(data)
        if step == 5:
            SUBMISSIONS.append({"flow": "classic", **SESSION})
            SESSION.clear()
            return self._send(b"", 303, {"Location": "/classic/done"})
        self._send(b"", 303, {"Location": f"/classic/step/{step + 1}"})


def start(port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


if __name__ == "__main__":
    srv, base = start(8765)
    print(base, "- /spa or /classic/step/1")
    threading.Event().wait()
