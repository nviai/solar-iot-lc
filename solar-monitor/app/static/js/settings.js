/* Settings page: copy buttons, polling schedule and the live device fields. */
(function () {
  'use strict';

  const App = window.App;
  const $ = App.$;
  const esc = App.escape;
  const fmt = App.fmt;

  // ------------------------------------------------------------ copy fields
  document.querySelectorAll('[data-copy]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const input = $(btn.dataset.copy);
      input.select();
      try {
        await navigator.clipboard.writeText(input.value);
        App.toast('Copied to clipboard', 'ok');
      } catch (e) {
        App.toast('Press Ctrl+C to copy', 'info');
      }
    });
  });

  // -------------------------------------------------------- poll schedule
  function renderSchedule(s) {
    App.setField('s-interval', s.poll_interval_seconds);
    App.setField('s-start', s.quiet_start);
    App.setField('s-end', s.quiet_end);
    App.setField('s-mode', s.quiet_mode);
    App.setField('s-quiet-interval', s.quiet_interval_seconds);
    App.setField('s-enabled', s.quiet_enabled);

    const badge = $('sched-badge');
    badge.dataset.tone = s.paused ? 'warn' : s.quiet_now ? 'info' : 'ok';
    badge.textContent = s.paused ? 'Paused · quiet hours' : s.quiet_now ? 'Quiet hours' : 'Normal';
    $('sched-hint').textContent = (s.paused ? 'Not calling the API right now'
      : 'Polling every ' + fmt.duration(s.effective_interval_seconds)) + ' · local time ' + s.local_time;
  }

  async function loadSchedule() {
    try {
      renderSchedule(await App.api('/api/v1/schedule'));
    } catch (e) {
      $('sched-hint').textContent = 'Could not load the schedule: ' + e.message;
    }
  }

  $('schedule-form').addEventListener('submit', e => {
    e.preventDefault();
    const body = {
      poll_interval_seconds: Number($('s-interval').value),
      quiet_enabled: $('s-enabled').checked,
      quiet_start: $('s-start').value,
      quiet_end: $('s-end').value,
      quiet_mode: $('s-mode').value,
      quiet_interval_seconds: Number($('s-quiet-interval').value),
    };
    App.busy($('sched-save'), async () => {
      try {
        renderSchedule(await App.api('/api/v1/schedule', { method: 'PUT', body: body }));
        App.toast('Schedule saved', 'ok');
        App.refreshStatus();
      } catch (err) {
        App.toast(err.message, 'fault');
      }
    });
  });

  // ---------------------------------------------------------- device data
  const joinNonEmpty = (values, unit) => (values || [])
    .filter(v => v != null && v > 0).map(v => v.toFixed(1) + ' ' + unit).join(' / ') || '—';
  const withUnit = (v, unit) => (v == null ? '—' : v + ' ' + unit);

  // Grouped so a long list stays scannable.
  const GROUPS = [
    { title: 'Identity', rows: [
      ['Inverter serial', d => d.inverter_sn || '—'],
      ['Registration No.', d => d.register_no],
      ['Plant ID', d => d.plant_id || '—'],
      ['Model', d => d.model || '—'],
      ['Status', d => d.status_label + ' (' + (d.status || '—') + ')'],
      ['Rated power', d => withUnit(d.rated_power, 'kW')],
      ['Last upload', d => d.upload_time || fmt.dateTime(d.ts)],
    ] },
    { title: 'Power', rows: [
      ['Solar total', d => fmt.powerText(d.pv_power)],
      ['Per string', d => (d.pv_strings || [])
        .map((v, i) => (v == null ? null : 'MPPT' + (i + 1) + ' ' + fmt.powerText(v)))
        .filter(Boolean).join(' · ') || '—'],
      ['Inverter AC output', d => fmt.powerText(d.ac_power)],
      ['Consumption', d => fmt.powerText(d.load_power)],
      ['Grid', d => fmt.powerText(d.grid_power)],
      ['EPS output', d => (d.eps_power != null ? fmt.powerText(d.eps_power) : '—')],
    ] },
    { title: 'AC side', rows: [
      ['Voltage', d => joinNonEmpty(d.vac, 'V')],
      ['Current', d => joinNonEmpty(d.iac, 'A')],
      ['Frequency', d => (d.frequency ? d.frequency.toFixed(2) + ' Hz' : '—')],
      ['Inverter temperature', d => withUnit(d.temperature, '°C')],
    ] },
    { title: 'Battery', when: d => d.has_battery, rows: [
      ['State of charge', d => withUnit(d.soc, '%')],
      ['Power', d => fmt.powerText(d.battery_power)],
      ['Remaining', d => withUnit(d.battery_remaining_kwh, 'kWh')],
      ['Health (SOH)', d => withUnit(d.battery_soh, '%')],
      ['Cycles', d => (d.battery_cycles != null ? d.battery_cycles : '—')],
      ['Voltage / current', d => (d.battery_voltage != null
        ? d.battery_voltage + ' V / ' + d.battery_current + ' A' : '—')],
      ['Temperature', d => withUnit(d.battery_temp, '°C')],
    ] },
    { title: 'Energy today', rows: [
      ['Produced', d => fmt.energy(d.yield_today)],
      ['Imported', d => fmt.energy(d.import_today)],
      ['Exported', d => fmt.energy(d.export_today)],
      ['Charged', d => fmt.energy(d.charged_today)],
      ['Discharged', d => fmt.energy(d.discharged_today)],
    ] },
    { title: 'Lifetime', rows: [
      ['Produced', d => fmt.energy(d.yield_total)],
      ['Imported', d => fmt.energy(d.import_total)],
      ['Exported', d => fmt.energy(d.export_total)],
      ['Charged', d => fmt.energy(d.charged_total)],
      ['Discharged', d => fmt.energy(d.discharged_total)],
    ] },
  ];

  function cell(fn, data) {
    try {
      const value = fn(data);
      return value == null ? '—' : esc(value);
    } catch (e) {
      return '—';
    }
  }

  function renderGroup(group, data) {
    return '<div><div class="kv-title">' + esc(group.title) + '</div><dl class="kv">'
      + group.rows.map(([label, fn]) => '<dt>' + esc(label) + '</dt><dd>' + cell(fn, data) + '</dd>').join('')
      + '</dl></div>';
  }

  function renderDevice(entry, withHeading) {
    const heading = withHeading ? '<p class="section-title">' + esc(entry.register_no) + '</p>' : '';
    if (!entry.data) {
      return heading + '<div class="empty">' + esc(entry.error || 'No data for ' + entry.register_no + ' yet.') + '</div>';
    }
    const error = entry.error
      ? '<div class="callout" data-tone="fault" style="margin-bottom:1rem">' + App.icon('alert')
        + '<div><p>' + esc(entry.error) + '</p></div></div>' : '';
    return heading + error + '<div class="device-groups">'
      + GROUPS.filter(g => !g.when || g.when(entry.data)).map(g => renderGroup(g, entry.data)).join('')
      + '</div>';
  }

  async function loadDevices() {
    const host = $('device-detail');
    try {
      const { devices } = await App.api('/api/v1/devices');
      host.innerHTML = devices.length
        ? devices.map(d => renderDevice(d, devices.length > 1)).join('')
        : '<div class="empty">' + App.icon('server') + 'No devices found yet.</div>';
    } catch (e) {
      host.innerHTML = '<div class="empty">Could not load device data: ' + esc(e.message) + '</div>';
    }
  }

  document.addEventListener('datarefresh', loadDevices);
  loadSchedule();
  loadDevices();
  App.every(loadSchedule, 30000);
  App.every(loadDevices, 30000);
})();
