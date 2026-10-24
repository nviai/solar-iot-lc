/* Devices page: the charging-bay hero, the live solar strip and one card per
 * switch.
 *
 * The server owns every decision; this file renders snapshots and posts
 * presses back. It polls quickly while something is in flight (a pending
 * switch, a manual override, an unreachable device) and lazily otherwise.
 */
(function () {
  'use strict';

  const App = window.App;
  const $ = App.$;

  const FAST_MS = 5000;
  const SLOW_MS = 15000;
  let timer = null;
  let snapshots = [];

  // ------------------------------------------------------------ solar strip
  function renderSolar(payload) {
    const strip = document.querySelector('.solar-strip');
    const status = payload.status;
    const totals = payload.snapshot && payload.snapshot.totals;
    const [tone, label, detail] = App.feedSummary(status);

    $('solar-badge').dataset.tone = tone;
    $('solar-badge').textContent = tone === 'ok' ? 'Live' : label;
    $('solar-sub').textContent = detail;
    if (tone === 'ok') strip.removeAttribute('data-stale'); else strip.setAttribute('data-stale', '');

    if (!totals) return;
    const hudPv = document.querySelector('[data-role="g-pv"]');
    if (hudPv) hudPv.innerHTML = App.fmt.powerHtml(totals.pv_power);
    $('s-pv').innerHTML = App.fmt.powerHtml(totals.pv_power);
    $('s-load').innerHTML = App.fmt.powerHtml(totals.load_power);
    $('s-grid').innerHTML = App.fmt.powerHtml(Math.abs(totals.grid_power));
    $('s-grid-label').textContent = totals.grid_power > 20 ? 'Grid · exporting'
      : totals.grid_power < -20 ? 'Grid · importing' : 'Grid';
    $('s-soc').textContent = App.fmt.percent(totals.soc);
  }

  // --------------------------------------------------------------- hero
  const garage = $('garage');

  function renderHero(snap) {
    if (!garage || garage.dataset.device !== snap.id) return;
    const role = name => garage.querySelector('[data-role="' + name + '"]');
    const state = App.deviceState(snap);
    const cfg = snap.config;
    const mode = App.modeOf(snap);
    App.renderGarage(garage, snap);

    const words = {
      on: mode === 'offpeak' ? 'Charging · off-peak' : mode === 'solar' ? 'Charging · on sun' : 'Charging',
      off: 'Not charging', offline: 'Charger unreachable', unavailable: 'Unavailable',
    };
    role('g-state').textContent = words[state] || 'Checking…';
    role('g-meta').textContent = App.stateMeta(snap);
    role('g-reason').textContent = snap.device.available ? (snap.reason || '—') : (snap.device.reason || '—');
    role('g-time').textContent = snap.local_time;
    role('g-time').title = snap.timezone;
    if (snap.pv_power == null) role('g-pv').textContent = '—';

    const chip = (name, armed, current, text) => {
      const el = role('chip-' + name);
      el.toggleAttribute('data-armed', armed);
      el.toggleAttribute('data-current', current);
      role('chip-' + name + '-text').textContent = text;
    };
    chip('solar', cfg.auto_enabled && cfg.solar_enabled, mode === 'solar',
      cfg.window_start + '–' + cfg.window_end + (cfg.solar_enabled ? '' : ' · off'));
    chip('offpeak', cfg.auto_enabled && cfg.offpeak_enabled, mode === 'offpeak',
      cfg.offpeak_start + '–' + cfg.offpeak_end + (cfg.offpeak_enabled ? '' : ' · off'));

    const key = role('g-power');
    const on = snap.switch.on === true;
    key.setAttribute('aria-pressed', String(on));
    key.disabled = !(snap.device.available && snap.switch.online);
    role('g-power-label').textContent = on ? 'Stop charging' : 'Start charging';

    App.renderDial($('garage-dial'), snap);
  }

  // ------------------------------------------------------------- device cards
  function renderCard(snap) {
    const card = document.querySelector('.device-card[data-device="' + CSS.escape(snap.id) + '"]');
    if (!card) return;
    const role = name => card.querySelector('[data-role="' + name + '"]');
    const state = App.deviceState(snap);
    const on = snap.switch.on;

    card.dataset.state = state === 'on' ? 'on' : state === 'off' ? 'off' : 'unknown';
    role('state').textContent = { on: 'On', off: 'Off', offline: 'Offline', unavailable: 'Unavailable' }[state] || 'Unknown';
    role('meta').textContent = App.stateMeta(snap);
    role('reason').textContent = snap.device.available ? (snap.reason || '—') : (snap.device.reason || '—');
    App.linkBadge(role('link'), snap);
    App.autoBadge(role('auto'), snap);

    const mode = App.modeOf(snap);
    [['solar', snap.config.solar_enabled], ['offpeak', snap.config.offpeak_enabled]].forEach(([name, armed]) => {
      const badge = role('mode-' + name);
      const live = snap.config.auto_enabled && armed;
      badge.dataset.tone = mode === name ? App.MODE[name].tone : 'idle';
      badge.style.opacity = live ? '' : '0.5';
      badge.title = live ? (mode === name ? 'In charge now' : 'Armed') : 'Not armed';
    });

    const controllable = snap.device.available && snap.switch.online;
    card.querySelectorAll('.power-toggle button').forEach(btn => {
      const target = btn.dataset.on === 'true';
      btn.setAttribute('aria-pressed', String(on === target));
      btn.disabled = !controllable || on === target;
    });
  }

  async function load() {
    try {
      const [solar, devices] = await Promise.all([
        App.api('/api/v1/solar'),
        App.api('/api/v1/devices'),
      ]);
      renderSolar(solar);
      snapshots = devices.devices;
      snapshots.forEach(renderCard);
      snapshots.forEach(renderHero);
    } catch (e) {
      App.toast('Could not load devices: ' + e.message, 'fault');
    }
    schedule();
  }

  function schedule() {
    const busy = snapshots.some(s =>
      s.pending || s.override_seconds || (s.device.available && !s.switch.online));
    clearTimeout(timer);
    timer = setTimeout(function tick() {
      if (document.hidden) { timer = setTimeout(tick, SLOW_MS); return; }
      load();
    }, busy ? FAST_MS : SLOW_MS);
  }

  // ------------------------------------------------------------------ actions
  async function press(id, on, busyEl) {
    const snap = snapshots.find(s => s.id === id);
    const name = snap ? snap.name : id;
    busyEl.classList.add('is-busy');
    try {
      const detail = await App.api('/api/v1/devices/' + encodeURIComponent(id) + '/switch', {
        method: 'POST', body: { on },
      });
      snapshots = snapshots.map(s => (s.id === detail.id ? detail : s));
      const update = () => { renderCard(detail); renderHero(detail); };
      // Morph the state change where the browser supports view transitions.
      if (document.startViewTransition && !App.fx.reduced()) document.startViewTransition(update);
      else update();
      App.toast(name + ' switched ' + (on ? 'on' : 'off'), 'ok');
      App.refreshShell();
    } catch (err) {
      App.toast(name + ': ' + err.message, 'fault');
    } finally {
      busyEl.classList.remove('is-busy');
      schedule();
    }
  }

  document.addEventListener('click', e => {
    const btn = e.target.closest('.power-toggle button');
    if (btn) press(btn.closest('.device-card').dataset.device, btn.dataset.on === 'true', btn.parentElement);

    const key = e.target.closest('[data-role="g-power"]');
    if (key) press(garage.dataset.device, key.getAttribute('aria-pressed') !== 'true', key);
  });

  const refreshBtn = $('refresh-all');
  refreshBtn.addEventListener('click', () => App.busy(refreshBtn, async () => {
    try {
      await App.api('/api/v1/solar?refresh=true');
      await Promise.all(snapshots
        .filter(s => s.device.available)
        .map(s => App.api('/api/v1/devices/' + encodeURIComponent(s.id) + '/refresh', { method: 'POST' })));
    } catch (err) {
      App.toast(err.message, 'fault');
    }
    await load();
  }));

  load();
})();
