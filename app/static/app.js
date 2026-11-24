// All page scripts live here (no inline JS), so the Content-Security-Policy can forbid inline scripts.
(() => {
  const $ = id => document.getElementById(id);

  // <form data-confirm="..."> and <button data-confirm="...">: ask before submitting.
  document.addEventListener('click', e => {
    const b = e.target.closest('button[data-confirm]');
    if (b && !confirm(b.dataset.confirm)) e.preventDefault();
  });
  document.addEventListener('submit', e => {
    const f = e.target;
    if (f.dataset.confirm && !(e.submitter && e.submitter.dataset.confirm) && !confirm(f.dataset.confirm)) e.preventDefault();
  });
  // <button data-print>
  document.addEventListener('click', e => { if (e.target.closest('[data-print]')) window.print(); });

  // Colour inputs: phone and tablet browsers often show only a short preset list for <input type=color>,
  // so every colour input also gets a hex box and hue / saturation / lightness sliders that work everywhere.
  const toHsl = hex => {
    const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255), mx = Math.max(r, g, b), mn = Math.min(r, g, b), l = (mx + mn) / 2, d = mx - mn;
    if (!d) return [0, 0, Math.round(l * 100)];
    const s = d / (1 - Math.abs(2 * l - 1)), h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [Math.round((h * 60 + 360) % 360), Math.round(s * 100), Math.round(l * 100)];
  };
  const toHex = (h, s, l) => {
    s /= 100; l /= 100; const k = n => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
    const f = n => Math.round(255 * (l - a * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1)))).toString(16).padStart(2, '0');
    return '#' + f(0) + f(8) + f(4);
  };
  const cleanHex = v => { v = v.trim().replace(/^#/, '').toLowerCase(); if (/^[0-9a-f]{3}$/.test(v)) v = [...v].map(c => c + c).join(''); return /^[0-9a-f]{6}$/.test(v) ? '#' + v : null; };
  document.querySelectorAll('input[type=color]').forEach(c => {
    const label = c.getAttribute('aria-label') || c.closest('.field')?.querySelector('span')?.textContent || 'Colour';
    const tools = document.createElement('span'); tools.className = 'colour-tools';
    const hex = Object.assign(document.createElement('input'), {type: 'text', value: c.value, maxLength: 7, spellcheck: false, autocomplete: 'off', className: 'hex-in'});
    hex.setAttribute('aria-label', label + ' (hex code)'); hex.setAttribute('autocapitalize', 'off'); hex.placeholder = '#d98ca3';
    const more = Object.assign(document.createElement('button'), {type: 'button', className: 'btn secondary sm', textContent: 'Sliders'});
    more.setAttribute('aria-expanded', 'false');
    const box = document.createElement('div'); box.className = 'hsl'; box.hidden = true;
    const sliders = [['Hue', 360], ['Saturation', 100], ['Lightness', 100]].map(([name, max]) => {
      const r = Object.assign(document.createElement('input'), {type: 'range', min: 0, max, step: 1, className: 'hsl-r'});
      r.setAttribute('aria-label', `${label} ${name.toLowerCase()}`);
      const row = document.createElement('div'); row.className = 'hsl-row'; row.append(Object.assign(document.createElement('span'), {textContent: name}), r); box.append(row);
      return r;
    });
    tools.append(hex, more); c.after(tools); tools.after(box);
    const paintTracks = ([h, s, l]) => {
      sliders[1].style.background = `linear-gradient(90deg, hsl(${h} 0% ${l}%), hsl(${h} 100% ${l}%))`;
      sliders[2].style.background = `linear-gradient(90deg, #000, hsl(${h} ${s}% 50%), #fff)`;
    };
    const sync = from => {
      const hsl = from === 'sliders' ? sliders.map(r => +r.value) : toHsl(c.value);
      if (from !== 'hex') hex.value = c.value;
      if (from !== 'sliders') sliders.forEach((r, i) => r.value = hsl[i]);
      paintTracks(hsl);
    };
    const set = (v, from) => {
      c.value = v; sync(from);
      c.dispatchEvent(new Event('input', {bubbles: true}));   // other listeners (theme preview, "show swatch") react as if picked
    };
    c.addEventListener('input', e => { if (e.isTrusted) sync('picker'); });
    c.addEventListener('change', () => sync('picker'));
    hex.addEventListener('input', () => { const v = cleanHex(hex.value); hex.classList.toggle('bad', !v && hex.value.trim() !== ''); if (v) set(v, 'hex'); });
    hex.addEventListener('blur', () => { hex.value = c.value; hex.classList.remove('bad'); });
    sliders.forEach(r => r.addEventListener('input', () => set(toHex(...sliders.map(x => +x.value)), 'sliders')));
    more.addEventListener('click', () => { box.hidden = !box.hidden; more.setAttribute('aria-expanded', String(!box.hidden)); });
    sync('init');
  });
  // Item form: choosing a swatch colour turns the swatch on.
  document.querySelectorAll('input[type=color][name=hex]').forEach(c => c.addEventListener('input', () => {
    const tick = c.form && c.form.elements.has_hex; if (tick) tick.checked = true;
  }));

  // Shopping list: copy as text.
  $('copy')?.addEventListener('click', async e => {
    const t = [...document.querySelectorAll('[data-line]')].map(n => '- ' + n.dataset.line).join('\n');
    try { await navigator.clipboard.writeText(t); e.currentTarget.lastChild.textContent = 'Copied'; } catch (_) { prompt('Copy this list:', t); }
  });

  // Appearance: live preview and contrast check for custom colours.
  const f = $('theme-form');
  if (f && $('custom-preview')) {
    const pv = $('custom-preview').firstElementChild, out = $('contrast'),
      rgb = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255), lin = c => c <= .03928 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4,
      L = h => { const [r, g, b] = rgb(h).map(lin); return .2126 * r + .7152 * g + .0722 * b; },
      CR = (a, b) => { const x = L(a), y = L(b); return (Math.max(x, y) + .05) / (Math.min(x, y) + .05); },
      v = n => f.elements[n].value, custom = () => { f.querySelector('input[name=theme][value=custom]').checked = true; };
    const paint = () => {
      const ac = v('accent'), t = CR(v('ink'), v('surface')), a = CR(ac, v('surface'));
      pv.style.cssText = `--bg:${v('bg')};--surface:${v('surface')};--ink:${v('ink')};--accent:${ac};--accent-ink:${CR(ac, '#ffffff') >= CR(ac, '#111322') ? '#ffffff' : '#111322'}`;
      out.textContent = `Text on cards: ${t.toFixed(1)}:1 ${t >= 4.5 ? '(good)' : '(hard to read, aim for 4.5 or more)'}. Accent on cards: ${a.toFixed(1)}:1 ${a >= 3 ? '(good)' : '(low, aim for 3 or more)'}.`;
    };
    ['bg', 'surface', 'ink', 'accent'].forEach(n => f.elements[n].addEventListener('input', () => { custom(); paint(); }));
    f.elements.mode.addEventListener('change', custom);
    f.querySelectorAll('input[name=theme]').forEach(r => r.addEventListener('change', () => {
      if (r.dataset.bg) { ['bg', 'surface', 'ink', 'accent'].forEach(n => { f.elements[n].value = r.dataset[n]; f.elements[n].dispatchEvent(new Event('change')); }); f.elements.mode.value = r.dataset.mode; paint(); }
    }));
    paint();
  }

  // Admin self-test: follow a run live.
  const panel = $('status-panel');
  if (panel && panel.dataset.status) {
    let since = 0, total = 0, seen = 0;
    const ms = v => v >= 10 ? Math.round(v).toLocaleString() + ' ms' : v.toFixed(1) + ' ms';
    const cell = (tr, text, cls) => { const td = document.createElement('td'); td.textContent = text; if (cls) td.className = cls; tr.appendChild(td); return td; };
    const pill = (text, cls) => { const s = document.createElement('span'); s.className = 'pill ' + cls; s.textContent = text; $('summary').appendChild(s); };
    const render = e => {
      if (e.event === 'start') { total = e.total; $('bar').max = total + 1; panel.hidden = false; $('features-panel').hidden = false; }
      else if (e.event === 'check') {
        seen++; $('bar').value = seen; $('counter').textContent = seen + ' of ' + total + ' checks';
        const tr = document.createElement('tr'); cell(tr, e.status === 'pass' ? 'PASS' : e.status === 'fail' ? 'FAIL' : 'ERROR', 'pill ' + (e.status === 'pass' ? 'ok' : 'bad'));
        cell(tr, e.category); const td = cell(tr, e.name);
        if (e.error) { const d = document.createElement('div'); d.className = 'small'; d.style.color = 'var(--danger)'; d.textContent = e.error; td.appendChild(d); }
        cell(tr, ms(e.ms)); cell(tr, String(e.requests)); cell(tr, e.slowest ? e.slowest.method + ' ' + e.slowest.path + ' ' + ms(e.slowest.total) : ''); $('rows').appendChild(tr);
      } else if (e.event === 'perf') {
        $('perf-panel').hidden = false; $('bar').value = total; const tr = document.createElement('tr'); cell(tr, e.page); cell(tr, ms(e.cold)); cell(tr, ms(e.median) + (e.slow ? ' (slow)' : ''), e.slow ? 'pill warn' : '');
        cell(tr, ms(e.p95)); cell(tr, ms(e.ttfb)); cell(tr, e.kb.toFixed(1) + ' KB'); $('perf').appendChild(tr);
      } else if (e.event === 'done') {
        const s = e.summary; $('bar').value = $('bar').max; $('headline').textContent = s.failed ? s.failed + ' of ' + s.total + ' checks need attention' : 'All ' + s.total + ' checks passed';
        $('summary').textContent = ''; pill(s.passed + ' passed', 'ok'); if (s.failed) pill(s.failed + ' failed', 'bad'); pill(s.duration_s + ' s total', ''); pill('median page ' + ms(s.median_load), ''); pill('slowest ' + ms(s.slowest_load), '');
        $('report').hidden = false; $('json').hidden = false;
      } else if (e.event === 'log') { panel.hidden = false; $('headline').textContent = e.text; }
    };
    const poll = async () => {
      try {
        const r = await fetch(panel.dataset.status + '?since=' + since, {credentials: 'same-origin'}); const d = await r.json(); since += d.events.length; d.events.forEach(render);
        $('run').disabled = d.state === 'running';
        if (d.state === 'running') setTimeout(poll, 700); else if (d.state === 'error') $('headline').textContent = $('headline').textContent || 'The self-test could not run';
      } catch (err) { setTimeout(poll, 2000); }
    };
    poll();
  }
})();
