(opts) => {
  // Extracts every fillable field in `opts.scope` (CSS selector) or the whole page.
  // Each element gets a data-jb attribute so Python can find it again.
  const scope = (opts.scope && document.querySelector(opts.scope)) || document.body;
  // Ids must stay unique per frame across repeated snapshots (elements revealed later get fresh ids).
  let counter = window.__jbCounter || 0;
  const tag = (el) => {
    if (!el.hasAttribute('data-jb')) el.setAttribute('data-jb', String(++counter));
    return Number(el.getAttribute('data-jb'));
  };
  const clean = (s) => (s || '').replace(/\s+/g, ' ').replace(/\s*\*\s*$/, '').trim();
  const dedupe = (s) => {
    // Sites often render "Email Email" (visible + screen-reader copy).
    s = clean(s);
    const words = s.split(' ');
    if (words.length % 2 === 0 && words.length >= 2) {
      const h = words.length / 2;
      if (words.slice(0, h).join(' ') === words.slice(h).join(' ')) return words.slice(0, h).join(' ');
    }
    const m = s.match(/^(.{3,}?)\s*\1$/);
    return m ? clean(m[1]) : s;
  };
  const visible = (el) => {
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const textOfIds = (ids) => ids.split(/\s+/).map((i) => document.getElementById(i))
    .filter(Boolean).map((e) => e.innerText || e.textContent).join(' ');
  const ownText = (label, self) => {
    const c = label.cloneNode(true);
    c.querySelectorAll('input,select,textarea,button,ul,[role=listbox]').forEach((n) => n.remove());
    return c.textContent;
  };

  function labelFor(el) {
    const ids = el.getAttribute('aria-labelledby');
    if (ids) { const t = clean(textOfIds(ids)); if (t) return dedupe(t); }
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) { const t = clean(ownText(l)); if (t) return dedupe(t); }
    }
    const wrap = el.closest('label');
    if (wrap) { const t = clean(ownText(wrap)); if (t) return dedupe(t); }
    const al = el.getAttribute('aria-label');
    if (clean(al)) return dedupe(al);
    let p = el.parentElement;
    for (let i = 0; i < 4 && p && p !== document.body; i++, p = p.parentElement) {
      const cand = p.querySelector(':scope > label, :scope > legend, :scope > [class*="label" i], :scope > div > label');
      if (cand && !cand.contains(el)) { const t = clean(ownText(cand)); if (t) return dedupe(t); }
      const prev = p.previousElementSibling;
      if (prev && !prev.querySelector('input,select,textarea')) {
        const t = clean(prev.textContent);
        if (t && t.length < 200) return dedupe(t);
      }
    }
    return clean(el.getAttribute('placeholder') || el.getAttribute('name') || '');
  }

  function groupLabel(els) {
    const first = els[0];
    const fs = first.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg && clean(lg.textContent)) return dedupe(lg.textContent); }
    const grp = first.closest('[role=radiogroup],[role=group]');
    if (grp) {
      const ids = grp.getAttribute('aria-labelledby');
      if (ids && clean(textOfIds(ids))) return dedupe(textOfIds(ids));
      if (clean(grp.getAttribute('aria-label'))) return dedupe(grp.getAttribute('aria-label'));
    }
    // climb to the smallest ancestor containing every option, then look at what precedes it
    let anc = first.parentElement;
    while (anc && !els.every((e) => anc.contains(e))) anc = anc.parentElement;
    for (let i = 0; i < 4 && anc && anc !== document.body; i++, anc = anc.parentElement) {
      const cand = anc.querySelector(':scope > label, :scope > legend, :scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > p, ' +
        ':scope > [class*="label" i], :scope > [class*="question" i], :scope > [class*="title" i]');
      if (cand && !els.some((e) => cand.contains(e))) { const t = clean(cand.textContent); if (t && t.length < 400) return dedupe(t); }
      const prev = anc.previousElementSibling;
      if (prev && !prev.querySelector('input,select,textarea')) {
        const t = clean(prev.textContent);
        if (t && t.length < 400) return dedupe(t);
      }
    }
    return '';
  }

  function optionText(el) {
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l) return clean(ownText(l));
    }
    const wrap = el.closest('label');
    if (wrap) return clean(ownText(wrap));
    const al = el.getAttribute('aria-label');
    if (al) return clean(al);
    const sib = el.nextElementSibling || el.nextSibling;
    return clean(sib && (sib.textContent || '')) || clean(el.value);
  }

  // Many sites mark required fields only with a trailing "*" (which clean() strips from the label)
  const reqMark = (el) => {
    const raw = [];
    if (el.id) { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) raw.push(l.textContent); }
    const w = el.closest('label'); if (w) raw.push(w.textContent);
    const ids = el.getAttribute('aria-labelledby'); if (ids) raw.push(textOfIds(ids));
    const lg = el.closest('fieldset'); if (lg && lg.querySelector('legend')) raw.push(lg.querySelector('legend').textContent);
    return raw.some((t) => /\*\s*$|\(required\)|\brequired\b/i.test(t || ''));
  };

  const sectionOf = (el) => {
    let p = el.parentElement;
    for (let i = 0; i < 8 && p && p !== document.body; i++, p = p.parentElement) {
      const h = p.querySelector(':scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > legend, :scope > header h2, :scope > header h3');
      if (h && clean(h.textContent)) return clean(h.textContent).slice(0, 120);
    }
    return '';
  };

  const fields = [];
  const done = new Set();
  const inScope = (el) => scope.contains(el);
  const SKIP_TYPES = new Set(['hidden', 'submit', 'button', 'image', 'reset', 'search']);

  const nodes = Array.from(scope.querySelectorAll(
    'input, select, textarea, [role=combobox], [role=radiogroup], button[aria-haspopup=listbox], [contenteditable=true]'));

  for (const el of nodes) {
    if (done.has(el) || !inScope(el)) continue;
    const tagName = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tagName === 'input' && SKIP_TYPES.has(type)) continue;
    if (el.disabled || el.getAttribute('aria-hidden') === 'true') continue;
    const isFile = type === 'file';
    if (!isFile && !visible(el)) {
      // custom-styled radios/checkboxes are often opacity:0 but still sized; truly hidden ones are skipped
      continue;
    }
    // ignore search boxes that belong to a combobox dropdown, and cookie banners
    if (el.closest('[id*="cookie" i],[class*="cookie" i],[id*="onetrust" i]')) continue;

    const required = el.required || el.getAttribute('aria-required') === 'true' || reqMark(el);
    const base = {
      name: el.getAttribute('name') || '',
      htmlId: el.id || '',
      placeholder: el.getAttribute('placeholder') || '',
      section: sectionOf(el),
      maxlength: el.maxLength > 0 && el.maxLength < 100000 ? el.maxLength : null,
      invalid: el.getAttribute('aria-invalid') === 'true',
      autocomplete: el.getAttribute('autocomplete') || '',
    };

    if (tagName === 'input' && type === 'radio') {
      const name = el.name;
      const group = name
        ? Array.from(scope.querySelectorAll('input[type=radio]')).filter((r) => r.name === name && visible(r))
        : [el];
      group.forEach((g) => done.add(g));
      const opts = group.map((g) => ({ id: tag(g), text: optionText(g), value: g.value, checked: g.checked }));
      fields.push({ ...base, kind: 'radio', id: opts[0].id, label: groupLabel(group) || labelFor(group[0]),
        required: group.some((g) => g.required || g.getAttribute('aria-required') === 'true') || reqMark(group[0]),
        options: opts, value: (opts.find((o) => o.checked) || {}).text || '' });
      continue;
    }
    if (tagName === 'input' && type === 'checkbox') {
      const name = el.name;
      const group = name
        ? Array.from(scope.querySelectorAll('input[type=checkbox]')).filter((r) => r.name === name && visible(r))
        : [el];
      if (group.length > 1) {
        group.forEach((g) => done.add(g));
        const opts = group.map((g) => ({ id: tag(g), text: optionText(g), value: g.value, checked: g.checked }));
        fields.push({ ...base, kind: 'checkbox_group', id: opts[0].id, label: groupLabel(group) || labelFor(group[0]),
          required, options: opts, value: opts.filter((o) => o.checked).map((o) => o.text).join(', ') });
      } else {
        done.add(el);
        fields.push({ ...base, kind: 'checkbox', id: tag(el), label: optionText(el) || labelFor(el), required,
          options: [], value: el.checked ? 'checked' : '' });
      }
      continue;
    }
    if (tagName === 'select') {
      const options = Array.from(el.options).map((o) => ({ text: clean(o.textContent), value: o.value, disabled: o.disabled }));
      done.add(el);
      const sel = el.selectedOptions[0];
      fields.push({ ...base, kind: 'select', id: tag(el), label: labelFor(el), required, options,
        value: sel && sel.value ? clean(sel.textContent) : '' });
      continue;
    }
    if (tagName === 'textarea') {
      done.add(el);
      fields.push({ ...base, kind: 'textarea', id: tag(el), label: labelFor(el), required, options: [], value: el.value });
      continue;
    }
    if (isFile) {
      done.add(el);
      fields.push({ ...base, kind: 'file', id: tag(el), label: labelFor(el) || clean((el.closest('div,section,label') || {}).textContent || '').slice(0, 120),
        required, options: [], value: el.files && el.files.length ? el.files[0].name : '', accept: el.accept || '' });
      continue;
    }
    if (el.getAttribute('role') === 'combobox' || el.getAttribute('aria-haspopup') === 'listbox') {
      done.add(el);
      const isInput = tagName === 'input' || tagName === 'textarea';
      let val = isInput ? el.value : clean(el.textContent);
      if (!val) {
        // react-select & friends render the chosen value in a sibling element, not in the input
        let q = el.parentElement;
        for (let i = 0; i < 4 && q; i++, q = q.parentElement) {
          const sv = q.querySelector('[class*="single-value" i],[class*="singleValue" i],[class*="selected-value" i],[class*="SelectedValue" i]');
          if (sv && clean(sv.textContent)) { val = clean(sv.textContent); break; }
        }
      }
      if (/^(select|choose|please select|pick|--|—)/i.test(val)) val = '';  // placeholder text, not a value
      fields.push({ ...base, kind: 'combobox', id: tag(el), label: labelFor(el), required, options: [], value: val });
      continue;
    }
    if (el.getAttribute('role') === 'radiogroup') { done.add(el); continue; } // handled through inner inputs
    if (el.getAttribute('contenteditable') === 'true') {
      done.add(el);
      fields.push({ ...base, kind: 'textarea', id: tag(el), label: labelFor(el), required, options: [], value: clean(el.textContent), editable: true });
      continue;
    }
    if (tagName === 'input') {
      done.add(el);
      const k = ['email', 'tel', 'number', 'date', 'url', 'password'].includes(type) ? type : 'text';
      fields.push({ ...base, kind: k, id: tag(el), label: labelFor(el), required, options: [], value: el.value });
    }
  }

  // Validation messages currently on screen
  const errSel = '[role=alert], [aria-live=assertive], .error, .errors, .invalid-feedback, [class*="error-message" i],' +
    ' [class*="field-error" i], [class*="inline-error" i], [id*="error" i]:not(input):not(select):not(textarea)';
  const errors = Array.from(new Set(Array.from(scope.querySelectorAll(errSel)).filter(visible)
    .map((e) => clean(e.innerText || e.textContent)).filter((t) => t && t.length < 300)));

  const buttons = Array.from(scope.querySelectorAll('button, input[type=submit], input[type=button], a[role=button], [role=button]'))
    .filter((b) => visible(b) && !b.disabled && b.getAttribute('aria-disabled') !== 'true')
    .map((b) => ({
      id: tag(b),
      text: clean(b.innerText || b.value || b.getAttribute('aria-label') || b.textContent),
      aria: clean(b.getAttribute('aria-label') || ''),
      type: (b.getAttribute('type') || '').toLowerCase(),
      primary: /primary|submit|next|continue/i.test(b.className || ''),
    })).filter((b) => b.text || b.aria);

  const headings = Array.from(scope.querySelectorAll('h1, h2, h3')).filter(visible)
    .map((h) => clean(h.textContent)).filter(Boolean).slice(0, 6);

  window.__jbCounter = counter;
  return { fields, errors, buttons, headings, counter, url: location.href };
}
