/* Core helpers shared by Solar Monitor and IoT Control: formatting, API calls,
 * toasts, theming and the mobile navigation drawer. Keep both copies identical.
 */
(function () {
  'use strict';

  const App = window.App = {};

  App.$ = id => document.getElementById(id);

  App.escape = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  /** Markup for a symbol from the inline sprite in base.html. */
  App.icon = name => '<svg class="i" aria-hidden="true"><use href="#i-' + name + '"/></svg>';

  const isNum = v => v !== null && v !== undefined && !Number.isNaN(Number(v));

  /** The UI is English throughout, so dates and numbers are too — whatever
   *  language the browser is set to. en-GB keeps a 24-hour clock. */
  const LOCALE = App.LOCALE = 'en-GB';

  App.fmt = {
    /** Watts split into value and unit, so the unit can be styled apart. */
    power(w) {
      if (!isNum(w)) return { value: '—', unit: '' };
      const abs = Math.abs(w);
      if (abs >= 1000) return { value: (w / 1000).toFixed(abs >= 10000 ? 1 : 2), unit: 'kW' };
      return { value: String(Math.round(w)), unit: 'W' };
    },
    powerText(w) {
      const p = App.fmt.power(w);
      return p.unit ? p.value + ' ' + p.unit : p.value;
    },
    powerHtml(w) {
      const p = App.fmt.power(w);
      return App.escape(p.value) + (p.unit ? '<small>' + p.unit + '</small>' : '');
    },
    energy(kwh) {
      if (!isNum(kwh)) return '—';
      if (Math.abs(kwh) >= 1000) return (kwh / 1000).toFixed(2) + ' MWh';
      return Number(kwh).toFixed(1) + ' kWh';
    },
    percent(v) { return isNum(v) ? Math.round(v) + '%' : '—'; },
    time(iso) {
      if (!iso) return '—';
      const d = new Date(iso);
      return Number.isNaN(d.getTime()) ? iso
        : d.toLocaleTimeString(LOCALE, { hour: '2-digit', minute: '2-digit' });
    },
    dateTime(iso) {
      if (!iso) return '—';
      const d = new Date(iso);
      return Number.isNaN(d.getTime()) ? iso
        : d.toLocaleString(LOCALE, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
    },
    day(isoDay) {
      const d = new Date(isoDay + 'T00:00:00');
      return Number.isNaN(d.getTime()) ? isoDay
        : d.toLocaleDateString(LOCALE, { day: '2-digit', month: 'short' });
    },
    duration(seconds) {
      if (!isNum(seconds)) return '—';
      const s = Math.max(0, Math.round(seconds));
      if (s < 60) return s + 's';
      if (s < 3600) return Math.round(s / 60) + ' min';
      if (s < 86400) return (s / 3600).toFixed(s < 36000 ? 1 : 0).replace(/\.0$/, '') + ' h';
      return Math.round(s / 86400) + ' d';
    },
    ago(iso) {
      if (!iso) return '—';
      const s = (Date.now() - new Date(iso).getTime()) / 1000;
      if (Number.isNaN(s)) return '—';
      return s < 5 ? 'just now' : App.fmt.duration(s) + ' ago';
    },
  };

  /** fetch() wrapper: JSON in, JSON out, readable errors thrown. */
  App.api = async function (path, options) {
    const opts = Object.assign({ headers: { 'Content-Type': 'application/json' } }, options || {});
    if (opts.body && typeof opts.body !== 'string') opts.body = JSON.stringify(opts.body);
    const res = await fetch(path, opts);
    if (!res.ok) {
      let detail = res.status + ' ' + res.statusText;
      try {
        const body = await res.json();
        if (Array.isArray(body.detail)) {
          detail = body.detail.map(d => (d.loc ? d.loc[d.loc.length - 1] + ': ' : '') + d.msg).join('; ');
        } else if (body.detail) {
          detail = body.detail;
        }
      } catch (e) { /* keep the status line */ }
      throw new Error(detail);
    }
    return res.status === 204 ? null : res.json();
  };

  App.toast = function (message, tone) {
    const stack = App.$('toast-stack');
    if (!stack) return;
    const el = document.createElement('div');
    el.className = 'toast';
    el.dataset.tone = tone || 'info';
    el.setAttribute('role', tone === 'fault' ? 'alert' : 'status');
    el.textContent = message;
    stack.appendChild(el);
    setTimeout(() => {
      el.dataset.leaving = '';
      el.addEventListener('animationend', () => el.remove(), { once: true });
    }, 4500);
  };

  /** Show a spinner on a button for the duration of an async task. */
  App.busy = async function (button, task) {
    button.classList.add('is-busy');
    button.disabled = true;
    try { return await task(); }
    finally { button.classList.remove('is-busy'); button.disabled = false; }
  };

  /** setInterval that skips hidden tabs and catches up when shown again. */
  App.every = function (fn, ms) {
    let last = Date.now();
    const run = () => { last = Date.now(); fn(); };
    setInterval(() => { if (!document.hidden) run(); }, ms);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && Date.now() - last > ms) run();
    });
  };

  /** Set an input value unless the user is editing it right now. */
  App.setField = function (id, value) {
    const el = App.$(id);
    if (!el || document.activeElement === el) return;
    if (el.type === 'checkbox') el.checked = !!value;
    else el.value = value;
  };

  // ------------------------------------------------------------- theme
  document.addEventListener('click', e => {
    const themeBtn = e.target.closest('[data-action="theme"]');
    if (themeBtn) {
      const root = document.documentElement;
      const current = root.getAttribute('data-theme')
        || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
      const next = current === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch (err) { /* private mode */ }
      document.dispatchEvent(new CustomEvent('themechange'));
      return;
    }

    // ------------------------------------------------------- nav drawer
    const navBtn = e.target.closest('[data-action="nav"]');
    if (navBtn) {
      const shell = document.querySelector('.shell');
      const open = shell.dataset.nav === 'open';
      shell.dataset.nav = open ? 'closed' : 'open';
      document.querySelectorAll('[data-action="nav"][aria-expanded]')
        .forEach(b => b.setAttribute('aria-expanded', String(!open)));
    }
  });

  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    const shell = document.querySelector('.shell[data-nav="open"]');
    if (shell) shell.dataset.nav = 'closed';
  });
})();
