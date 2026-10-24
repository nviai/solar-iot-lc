/* App-wide pieces for IoT Control: the solar-feed status in the sidebar, the
 * per-device dots in the navigation, and renderers shared by several pages.
 */
(function () {
  'use strict';

  const App = window.App;
  const $ = App.$;
  const esc = App.escape;

  /** Stale beyond this many seconds: the monitor answers but its data is old. */
  const STALE_AFTER_S = 600;

  // ---------------------------------------------------------- shared renderers
  /** One status word for a device snapshot: on | off | offline | unavailable | unknown. */
  App.deviceState = function (snap) {
    const sw = snap.switch || {};
    if (!snap.device.available) return 'unavailable';
    if (sw.checked_at && !sw.online) return 'offline';
    if (sw.on === true) return 'on';
    if (sw.on === false) return 'off';
    return 'unknown';
  };

  App.linkBadge = function (badge, snap) {
    const state = App.deviceState(snap);
    const map = {
      unavailable: ['warn', 'Unavailable'],
      offline: ['fault', 'Unreachable'],
      unknown: ['idle', 'Checking'],
    };
    const [tone, text] = map[state] || ['ok', 'Online'];
    badge.dataset.tone = tone;
    badge.textContent = text;
    badge.title = (snap.switch && snap.switch.error) || snap.device.reason || '';
  };

  App.autoBadge = function (badge, snap) {
    if (snap.override_seconds) {
      badge.dataset.tone = 'warn';
      badge.textContent = 'Override · ' + App.fmt.duration(snap.override_seconds);
    } else if (snap.config.auto_enabled) {
      badge.dataset.tone = 'ok';
      badge.textContent = 'Auto armed';
    } else {
      badge.dataset.tone = 'idle';
      badge.textContent = 'Manual only';
    }
  };

  /** Short line under the state: a pending switch, or how fresh the reading is. */
  App.stateMeta = function (snap) {
    const sw = snap.switch || {};
    if (snap.pending) {
      return 'Switching ' + (snap.pending.target ? 'on' : 'off') + ' in ' + snap.pending.remaining_s + 's';
    }
    if (!snap.device.available) return snap.device.reason || '';
    if (sw.checked_at && !sw.online) return sw.error || 'No answer from the switch';
    return sw.checked_at ? 'Read ' + App.fmt.ago(sw.checked_at) : 'Reading the switch…';
  };

  // ------------------------------------------------------------ schedules
  const MODE = {
    solar: { label: 'Solar', tone: 'sun', icon: 'sun' },
    offpeak: { label: 'Off-peak', tone: 'night', icon: 'moon' },
    idle: { label: 'Idle', tone: 'idle', icon: 'clock' },
  };
  App.MODE = MODE;

  /** The schedule in charge right now: solar | offpeak | idle. */
  App.modeOf = snap => (snap.config.auto_enabled && MODE[snap.mode] ? snap.mode : 'idle');

  /** "Off-peak starts at 20:30 · in 3 h", or '' with nothing scheduled. */
  App.nextText = function (snap, short) {
    const n = snap.next_change;
    if (!n) return snap.config.auto_enabled ? '' : 'Automation is off';
    const what = MODE[n.mode].label + (n.edge === 'start' ? (short ? ' on' : ' starts') : (short ? ' off' : ' ends'));
    return what + ' ' + n.at + ' · in ' + App.fmt.duration(n.in_s);
  };

  const hhmm = v => { const [h, m] = String(v).split(':').map(Number); return (h * 60 + (m || 0)) % 1440; };
  App.hhmm = hhmm;

  /** Window [start, end) as day fractions; a wrapped window gives two spans. */
  App.spans = function (start, end) {
    const a = hhmm(start) / 1440, b = hhmm(end) / 1440;
    if (a === b) return [[0, 1]];
    return a < b ? [[a, b]] : [[a, 1], [0, b]];
  };

  function arcPath(a, b) {
    // a, b: day fractions; 0 = midnight at the top, clockwise.
    const R = 84, C = 110;
    if (b - a >= 0.999) b = a + 0.9999;
    const pt = f => [C + R * Math.sin(f * 2 * Math.PI), C - R * Math.cos(f * 2 * Math.PI)];
    const [x1, y1] = pt(a), [x2, y2] = pt(b);
    return 'M' + x1.toFixed(2) + ' ' + y1.toFixed(2) + ' A' + R + ' ' + R + ' 0 ' + (b - a > 0.5 ? 1 : 0)
      + ' 1 ' + x2.toFixed(2) + ' ' + y2.toFixed(2);
  }

  /** Draw the 24 h dial: both windows, the hand at local time, the next change. */
  App.renderDial = function (fig, snap) {
    if (!fig) return;
    const cfg = snap.config;
    const ticks = fig.querySelector('.dial__ticks');
    if (!ticks.childElementCount) {
      let html = '';
      for (let h = 0; h < 24; h++) {
        const ang = h / 24 * 2 * Math.PI, major = h % 6 === 0;
        const r1 = major ? 68 : 71, r2 = 75;
        html += '<line class="' + (major ? 'is-major' : '') + '" x1="' + (110 + r1 * Math.sin(ang)).toFixed(1)
          + '" y1="' + (110 - r1 * Math.cos(ang)).toFixed(1) + '" x2="' + (110 + r2 * Math.sin(ang)).toFixed(1)
          + '" y2="' + (110 - r2 * Math.cos(ang)).toFixed(1) + '"/>';
        if (major) {
          html += '<text x="' + (110 + 58 * Math.sin(ang)).toFixed(1) + '" y="' + (110 - 58 * Math.cos(ang)).toFixed(1)
            + '">' + h + '</text>';
        }
      }
      ticks.innerHTML = html;
    }
    const mode = App.modeOf(snap);
    const set = (sel, start, end, armed, current) => {
      const el = fig.querySelector(sel);
      el.setAttribute('d', App.spans(start, end).map(([a, b]) => arcPath(a, b)).join(' '));
      el.toggleAttribute('data-armed', armed);
      el.toggleAttribute('data-current', current);
    };
    set('.dial__arc--solar', cfg.window_start, cfg.window_end,
      cfg.auto_enabled && cfg.solar_enabled, mode === 'solar');
    set('.dial__arc--offpeak', cfg.offpeak_start, cfg.offpeak_end,
      cfg.auto_enabled && cfg.offpeak_enabled, mode === 'offpeak');

    // Keep the hand turning forward across midnight instead of spinning back.
    const hand = fig.querySelector('.dial__hand');
    const deg = snap.local_minutes / 1440 * 360;
    const prev = Number(hand.dataset.deg || deg);
    let next = deg + 360 * Math.round((prev - deg) / 360);
    if (next < prev - 1) next += 360;
    hand.dataset.deg = String(next);
    hand.style.transform = 'rotate(' + next + 'deg)';

    fig.querySelector('[data-role="dial-time"]').textContent = snap.local_time;
    fig.querySelector('[data-role="dial-zone"]').textContent = snap.timezone;
    fig.querySelector('[data-role="dial-next"]').textContent = App.nextText(snap, true);
  };

  /** Update a [data-garage] hero: state colours, mode glow, charging effects. */
  App.renderGarage = function (root, snap) {
    if (!root) return;
    const state = App.deviceState(snap);
    root.dataset.state = state === 'on' ? 'on' : state === 'off' ? 'off'
      : state === 'offline' ? 'offline' : 'unknown';
    root.dataset.mode = App.modeOf(snap);
  };

  /** Tilt the 3D stage gently toward the pointer. */
  function stageParallax() {
    if (App.fx && App.fx.reduced()) return;
    document.addEventListener('pointermove', e => {
      document.querySelectorAll('.stage').forEach(stage => {
        const r = stage.getBoundingClientRect();
        if (e.clientY < r.top - 200 || e.clientY > r.bottom + 200) return;
        const x = (e.clientX - (r.left + r.width / 2)) / innerWidth;
        const y = (e.clientY - (r.top + r.height / 2)) / innerHeight;
        stage.style.setProperty('--ty', (x * 10).toFixed(2) + 'deg');
        stage.style.setProperty('--tx', (-y * 6).toFixed(2) + 'deg');
      });
    }, { passive: true });
  }
  stageParallax();

  /** Timeline markup for device events. */
  App.renderEvents = function (host, empty, events, showDevice) {
    empty.hidden = events.length > 0;
    host.innerHTML = events.map(ev => {
      const tone = !ev.ok ? 'fault' : ev.source === 'offpeak' ? 'night' : ev.action === 'on' ? 'ok' : 'idle';
      const icon = !ev.ok ? 'alert'
        : { manual: 'user', offpeak: 'moon', auto: 'sun' }[ev.source] || 'wand';
      const who = { manual: 'By hand', offpeak: 'Off-peak schedule', auto: 'Solar automation' }[ev.source]
        || 'Automation';
      const title = (showDevice ? esc(ev.device_name || ev.device_id) + ' · ' : '')
        + 'Switched ' + esc(String(ev.action).toUpperCase()) + (ev.ok ? '' : ' — failed');
      const meta = [who, esc(ev.reason)];
      if (ev.pv_power != null) meta.push('solar ' + App.fmt.powerText(ev.pv_power));
      meta.push('<time datetime="' + esc(ev.ts) + '" title="' + esc(ev.ts) + '">'
        + App.fmt.dateTime(ev.ts) + '</time>');
      return '<div class="timeline__item" data-tone="' + tone + '">'
        + '<span class="timeline__icon">' + App.icon(icon) + '</span>'
        + '<div><div class="timeline__title">' + title + '</div>'
        + '<div class="timeline__meta">' + meta.filter(Boolean).join(' · ') + '</div></div></div>';
    }).join('');
  };

  // ------------------------------------------------------------- sidebar
  function show(tone, label, detail, pulse) {
    const dot = $('feed-dot');
    dot.dataset.tone = tone;
    if (pulse) dot.setAttribute('data-pulse', ''); else dot.removeAttribute('data-pulse');
    $('feed-label').textContent = label;
    $('feed-detail').textContent = detail;
  }

  /** Describe the solar feed for any status line: [tone, label, detail]. */
  App.feedSummary = function (solar) {
    if (solar.error) return ['fault', 'Monitor unreachable', solar.error];
    if (!solar.measured_at) return ['warn', 'No solar data yet', 'Solar Monitor has no reading'];
    if (solar.age_seconds > STALE_AFTER_S) {
      return ['warn', 'Solar data is stale', 'Last reading ' + App.fmt.duration(solar.age_seconds) + ' old'];
    }
    if (solar.paused) return ['idle', 'Monitor paused', 'Quiet hours · reading ' + App.fmt.duration(solar.age_seconds) + ' old'];
    return ['ok', 'Solar feed · live', 'Reading ' + App.fmt.duration(solar.age_seconds) + ' old'];
  };

  async function refreshShell() {
    try {
      const [status, devices] = await Promise.all([
        App.api('/api/v1/status'),
        document.querySelector('[data-device-dot]') ? App.api('/api/v1/devices') : null,
      ]);
      const [tone, label, detail] = App.feedSummary(status.solar);
      show(tone, label, detail, tone === 'ok');
      (devices ? devices.devices : []).forEach(snap => {
        const dot = document.querySelector('[data-device-dot="' + CSS.escape(snap.id) + '"]');
        if (!dot) return;
        const state = App.deviceState(snap);
        dot.dataset.tone = { on: 'ok', offline: 'fault', unavailable: 'warn' }[state] || 'idle';
        dot.title = state;
      });
    } catch (e) {
      show('fault', 'Disconnected', 'Cannot reach IoT Control');
    }
  }

  App.refreshShell = refreshShell;
  refreshShell();
  App.every(refreshShell, 15000);
})();
