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
      if (r.dataset.bg) { ['bg', 'surface', 'ink', 'accent'].forEach(n => f.elements[n].value = r.dataset[n]); f.elements.mode.value = r.dataset.mode; paint(); }
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
