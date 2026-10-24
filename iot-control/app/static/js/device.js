/* Device page: the relay, its automation settings, and its activity log.
 *
 * The server owns the whole decision; this file renders the snapshot it
 * returns and posts changes back. Form fields are left alone while they have
 * focus so a poll never overwrites something half-typed.
 */
(function () {
  'use strict';

  const App = window.App;
  const $ = App.$;

  const root = $('device-root');
  const base = '/api/v1/devices/' + encodeURIComponent(root.dataset.deviceId);
  const FAST_MS = 5000;
  const SLOW_MS = 15000;

  let snap = null;
  let timer = null;

  const kw = w => (w / 1000).toFixed(2).replace(/\.?0+$/, '');

  // ------------------------------------------------------------- rendering
  function fillForm(cfg) {
    App.setField('f-solar', cfg.solar_enabled);
    App.setField('f-on', kw(cfg.on_threshold_w));
    App.setField('f-off', kw(cfg.off_threshold_w));
    App.setField('f-start', cfg.window_start);
    App.setField('f-end', cfg.window_end);
    App.setField('f-off-outside', cfg.off_outside_window);
    App.setField('f-offpeak', cfg.offpeak_enabled);
    App.setField('f-op-start', cfg.offpeak_start);
    App.setField('f-op-end', cfg.offpeak_end);
    App.setField('f-tz', cfg.timezone);
    App.setField('f-sustain', cfg.sustain_s);
    App.setField('f-override', cfg.manual_override_minutes);
    App.setField('f-stale', cfg.stale_after_s);
    App.setField('f-tick', cfg.tick_seconds);
    App.setField('auto-enabled', cfg.auto_enabled);
    $('auto-label').textContent = cfg.auto_enabled ? 'Armed' : 'Off';
  }

  /** The 24 h bar follows the inputs live, before anything is saved. */
  function renderDaybar(nowMinutes) {
    const bar = $('daybar');
    const seg = (cls, span, off) => {
      const el = bar.querySelector(cls);
      el.hidden = !span;
      if (!span) return;
      el.style.setProperty('--from', span[0]);
      el.style.setProperty('--len', span[1] - span[0]);
      el.toggleAttribute('data-off', off);
    };
    const solar = App.spans($('f-start').value || '00:00', $('f-end').value || '00:00');
    const night = $('f-op-start').value && $('f-op-start').value !== $('f-op-end').value
      ? App.spans($('f-op-start').value, $('f-op-end').value || '00:00') : [];
    seg('.daybar__seg--solar', solar[0], !$('f-solar').checked);
    seg('.daybar__seg--offpeak', night[0], !$('f-offpeak').checked);
    seg('.daybar__seg--offpeak2', night[1], !$('f-offpeak').checked);
    if (nowMinutes != null) bar.style.setProperty('--now', nowMinutes / 1440);
  }

  function renderRelay(s) {
    const state = App.deviceState(s);
    const on = s.switch.on;
    const labels = { on: 'On', off: 'Off', offline: 'Unreachable', unavailable: 'Unavailable' };
    $('relay').dataset.state = state === 'unavailable' ? 'unknown' : state;
    $('relay-value').textContent = labels[state] || 'Unknown';
    $('relay-meta').textContent = App.stateMeta(s);
    App.linkBadge($('link-badge'), s);

    const controllable = s.device.available && s.switch.online;
    $('btn-on').disabled = !controllable || on === true;
    $('btn-off').disabled = !controllable || on === false;

    const banner = $('override-banner');
    banner.hidden = !s.override_seconds;
    if (s.override_seconds) {
      $('override-text').textContent = 'You switched this device by hand, so automation '
        + 'stands down for another ' + App.fmt.duration(s.override_seconds) + '.';
    }
  }

  function renderStatus(s) {
    $('stat-pv').textContent = s.pv_power == null ? '—' : App.fmt.powerText(s.pv_power);
    $('stat-time').textContent = s.local_time;
    $('stat-time').title = s.timezone;
    const mode = App.modeOf(s);
    $('stat-mode').textContent = s.config.auto_enabled ? App.MODE[mode].label : 'Manual only';
    $('stat-next').textContent = App.nextText(s) || '—';
    $('hud-mode').textContent = s.config.auto_enabled ? App.MODE[mode].label : 'Manual';
    $('hud-pv').innerHTML = s.pv_power == null ? '—' : App.fmt.powerHtml(s.pv_power);
    $('panel-solar').toggleAttribute('data-current', mode === 'solar');
    $('panel-offpeak').toggleAttribute('data-current', mode === 'offpeak');
    $('stat-reason').textContent = s.device.available ? (s.reason || '—') : (s.device.reason || '—');
    $('auto-status').dataset.active = s.config.auto_enabled ? 'yes' : 'no';
  }

  function render(s) {
    snap = s;
    fillForm(s.config);
    renderRelay(s);
    renderStatus(s);
    renderDaybar(s.local_minutes);
    App.renderGarage($('garage'), s);
    App.renderDial($('device-dial'), s);
    if (s.events) App.renderEvents($('events'), $('events-empty'), s.events, false);
  }

  // ------------------------------------------------------------------ data
  async function load() {
    try {
      render(await App.api(base));
    } catch (e) {
      App.toast('Could not read the device: ' + e.message, 'fault');
    }
    schedule();
  }

  /** Re-poll quickly while something is in flight, lazily when settled. */
  function schedule() {
    const fast = snap && (snap.pending || snap.override_seconds
      || (snap.device.available && !snap.switch.online));
    clearTimeout(timer);
    timer = setTimeout(function tick() {
      if (document.hidden) { timer = setTimeout(tick, SLOW_MS); return; }
      load();
    }, fast ? FAST_MS : SLOW_MS);
  }

  /** Send a change and render the reply; on failure re-sync from the server. */
  async function send(path, method, body) {
    try {
      render(await App.api(base + path, { method: method, body: body || {} }));
      return true;
    } catch (e) {
      App.toast(e.message, 'fault');
      await load();
      return false;
    } finally {
      schedule();
      App.refreshShell();
    }
  }

  // --------------------------------------------------------------- actions
  $('btn-on').addEventListener('click', e => App.busy(e.currentTarget, async () => {
    if (await send('/switch', 'POST', { on: true })) App.toast('Switched on', 'ok');
  }));

  $('btn-off').addEventListener('click', e => App.busy(e.currentTarget, async () => {
    if (await send('/switch', 'POST', { on: false })) App.toast('Switched off', 'ok');
  }));

  $('btn-clear-override').addEventListener('click', e => App.busy(e.currentTarget, async () => {
    if (await send('/override/clear', 'POST')) App.toast('Automation is back in control', 'ok');
  }));

  $('btn-recheck').addEventListener('click', e =>
    App.busy(e.currentTarget, () => send('/refresh', 'POST')));

  $('auto-enabled').addEventListener('change', async e => {
    const on = e.target.checked;
    if (await send('/automation', 'PUT', { auto_enabled: on })) {
      App.toast(on ? 'Automatic mode armed' : 'Automatic mode off', 'ok');
    } else {
      e.target.checked = !on;
    }
  });

  // ------------------------------------------------------------ time zone
  const zones = (Intl.supportedValuesOf && Intl.supportedValuesOf('timeZone')) || [];
  $('tz-list').innerHTML = ['UTC', 'Asia/Ho_Chi_Minh'].concat(zones.filter(z => z !== 'Asia/Ho_Chi_Minh'))
    .map(z => '<option value="' + App.escape(z) + '"></option>').join('');
  const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (browserZone) {
    $('tz-browser').textContent = 'Use this browser\'s zone (' + browserZone + ')';
    $('tz-browser').addEventListener('click', () => { $('f-tz').value = browserZone; $('f-tz').focus(); });
  }

  ['f-start', 'f-end', 'f-op-start', 'f-op-end', 'f-solar', 'f-offpeak'].forEach(id =>
    $(id).addEventListener('input', () => renderDaybar()));

  $('auto-form').addEventListener('submit', e => {
    e.preventDefault();
    const num = id => Number($(id).value);
    const body = {
      solar_enabled: $('f-solar').checked,
      on_threshold_w: num('f-on') * 1000,
      off_threshold_w: num('f-off') * 1000,
      window_start: $('f-start').value,
      window_end: $('f-end').value,
      off_outside_window: $('f-off-outside').checked,
      offpeak_enabled: $('f-offpeak').checked,
      offpeak_start: $('f-op-start').value,
      offpeak_end: $('f-op-end').value,
      timezone: $('f-tz').value.trim(),
      sustain_s: num('f-sustain'),
      manual_override_minutes: num('f-override'),
      stale_after_s: num('f-stale'),
      tick_seconds: num('f-tick'),
    };
    if (body.off_threshold_w >= body.on_threshold_w) {
      App.toast('The switch-off threshold must be lower than the switch-on threshold.', 'warn');
      $('f-off').focus();
      return;
    }
    if (body.offpeak_start === body.offpeak_end) {
      App.toast('Off-peak must start and end at different times.', 'warn');
      $('f-op-end').focus();
      return;
    }
    // The server checks the zone: browsers list some under older aliases.
    App.busy($('btn-save'), async () => {
      if (await send('/automation', 'PUT', body)) App.toast('Automation settings saved', 'ok');
    });
  });

  load();
})();
