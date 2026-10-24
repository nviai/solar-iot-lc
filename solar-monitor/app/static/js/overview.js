/* Overview page: live tiles, energy flow, power chart and daily energy. */
(function () {
  'use strict';

  const App = window.App;
  const $ = App.$;
  const fmt = App.fmt;

  const deviceSelect = $('device-select');
  let device = deviceSelect.value || null;
  let hours = 24;

  const flow = App.flowDiagram($('flow-diagram'));

  // Series identity is fixed — a series always keeps its colour slot.
  const SERIES = [
    { key: 'pv_power', label: 'Solar', color: 'var(--s-pv)' },
    { key: 'load_power', label: 'Consumption', color: 'var(--s-load)' },
    { key: 'grid_power', label: 'Grid', color: 'var(--s-grid)' },
    { key: 'battery_power', label: 'Battery', color: 'var(--s-bat)' },
  ];

  const kw = w => (w / 1000).toFixed(2) + ' kW';
  const kwTick = w => (Math.abs(w) >= 1000 ? (w / 1000) + 'k' : String(w));
  const devicePath = () => '/api/v1/devices/' + encodeURIComponent(device);

  const powerChart = App.lineChart($('power-chart'), {
    x: [], series: SERIES.map(s => Object.assign({ values: [] }, s)),
    height: 260, legend: '#power-legend',
    format: kw, yTick: kwTick,
    xTick: t => fmt.time(t),
    xLabel: t => fmt.dateTime(t),
    ariaLabel: 'Solar, consumption, grid and battery power over time',
    emptyMessage: 'No history yet. Run scripts/backfill.py, or wait for a few poll cycles.',
  });

  const dailyChart = App.barChart($('daily-chart'), {
    categories: [], series: [], height: 220, legend: '#daily-legend',
    format: v => v.toFixed(1) + ' kWh',
    yTick: v => String(v),
    xTick: d => fmt.day(d),
    xLabel: d => fmt.day(d),
    ariaLabel: 'Energy by day',
    emptyMessage: 'At least a day of samples is needed here.',
  });

  // ------------------------------------------------------------ devices
  async function resolveDevice() {
    if (device) return device;
    const { devices } = await App.api('/api/v1/devices');
    if (!devices.length) return null;
    deviceSelect.innerHTML = devices.map(d =>
      '<option value="' + App.escape(d.register_no) + '">' + App.escape(d.register_no) + '</option>').join('');
    deviceSelect.hidden = devices.length < 2;
    device = devices[0].register_no;
    return device;
  }

  // ------------------------------------------------------------ realtime
  function setDirection(id, tone, label) {
    const el = $(id);
    el.dataset.tone = tone;
    el.textContent = label;
  }

  function renderTiles(d) {
    $('k-pv').innerHTML = fmt.powerHtml(d.pv_power);
    $('k-pv-meta').innerHTML = '<strong>' + fmt.energy(d.yield_today) + '</strong> today'
      + (d.rated_power ? ' · ' + d.rated_power + ' kW rated' : '');

    $('k-load').innerHTML = fmt.powerHtml(d.load_power);
    $('k-load-meta').innerHTML = d.self_sufficiency == null ? 'No load'
      : '<strong>' + Math.round(d.self_sufficiency) + '%</strong> self-sufficient';

    $('k-grid').innerHTML = fmt.powerHtml(Math.abs(d.grid_power));
    if (d.grid_power > 20) setDirection('k-grid-dir', 'ok', 'Exporting');
    else if (d.grid_power < -20) setDirection('k-grid-dir', 'warn', 'Importing');
    else setDirection('k-grid-dir', 'idle', 'Idle');
    $('k-grid-meta').innerHTML = '<strong>' + fmt.energy(d.import_today) + '</strong> in · <strong>'
      + fmt.energy(d.export_today) + '</strong> out today';

    const battery = $('kpi-battery');
    battery.hidden = !d.has_battery;
    if (!d.has_battery) return;

    $('k-soc').innerHTML = d.soc == null ? '—' : Math.round(d.soc) + '<small>%</small>';
    const bp = d.battery_power || 0;
    if (bp > 20) setDirection('k-bat-dir', 'ok', 'Charging');
    else if (bp < -20) setDirection('k-bat-dir', 'info', 'Discharging');
    else setDirection('k-bat-dir', 'idle', 'Idle');
    const parts = [];
    if (Math.abs(bp) > 20) parts.push('<strong>' + fmt.powerText(Math.abs(bp)) + '</strong>');
    if (d.battery_remaining_kwh != null) parts.push(d.battery_remaining_kwh + ' kWh left');
    if (d.battery_temp != null) parts.push(d.battery_temp + ' °C');
    $('k-bat-meta').innerHTML = parts.join(' · ') || '&nbsp;';
    const fill = $('soc-fill');
    fill.style.width = Math.max(0, Math.min(100, d.soc || 0)) + '%';
    fill.dataset.level = d.soc < 15 ? 'critical' : d.soc < 30 ? 'low' : 'ok';
  }

  function renderToday(d) {
    $('d-yield').textContent = fmt.energy(d.yield_today);
    $('d-import').textContent = fmt.energy(d.import_today);
    $('d-export').textContent = fmt.energy(d.export_today);
    $('d-charged').textContent = fmt.energy(d.charged_today);
    $('d-discharged').textContent = fmt.energy(d.discharged_today);
    $('d-self').textContent = fmt.percent(d.self_sufficiency);
    $('d-temp').textContent = d.temperature == null ? '—' : d.temperature + ' °C';
  }

  async function loadRealtime() {
    let entry;
    try {
      if (!(await resolveDevice())) return;
      entry = await App.api(devicePath());
    } catch (e) {
      return;
    }

    $('device-error').hidden = !entry.error;
    $('device-error-msg').textContent = entry.error || '';

    const d = entry.data;
    if (!d) return;

    $('overview-sub').textContent = 'Updated ' + fmt.ago(d.ts)
      + (d.model && d.model !== '-' ? ' · ' + d.model : '')
      + ' · Lifetime yield ' + fmt.energy(d.yield_total);
    const status = $('status-badge');
    status.dataset.tone = d.status_severity || 'idle';
    status.textContent = 'Inverter: ' + (d.status_label || '—');

    renderTiles(d);
    renderToday(d);
    flow.render(d);
  }

  // ------------------------------------------------------------- history
  async function loadHistory() {
    let payload;
    try {
      if (!(await resolveDevice())) return;
      payload = await App.api(devicePath() + '/history?hours=' + hours);
    } catch (e) {
      return;
    }
    const points = payload.points || [];
    powerChart.update({
      x: points.map(p => p.ts),
      series: SERIES.map(s => Object.assign({}, s, { values: points.map(p => p[s.key]) })),
    });

    App.sparkline($('sp-pv'), points.map(p => p.pv_power || 0), 'var(--s-pv)');
    App.sparkline($('sp-load'), points.map(p => p.load_power || 0), 'var(--s-load)');
    App.sparkline($('sp-grid'), points.map(p => p.grid_power || 0), 'var(--s-grid)');

    // Table view, newest first and capped so the DOM stays light.
    $('power-table').innerHTML = points.slice(-300).reverse().map(p =>
      '<tr><td>' + fmt.dateTime(p.ts) + '</td>'
      + '<td class="num">' + kw(p.pv_power || 0) + '</td>'
      + '<td class="num">' + kw(p.load_power || 0) + '</td>'
      + '<td class="num">' + kw(p.grid_power || 0) + '</td>'
      + '<td class="num">' + kw(p.battery_power || 0) + '</td></tr>').join('');
  }

  async function loadDaily() {
    let payload;
    try {
      if (!(await resolveDevice())) return;
      payload = await App.api(devicePath() + '/daily?days=14');
    } catch (e) {
      return;
    }
    const days = payload.days || [];
    dailyChart.update({
      categories: days.map(d => d.day),
      series: [
        { key: 'pv', label: 'Solar', color: 'var(--s-pv)', values: days.map(d => d.pv_kwh) },
        { key: 'load', label: 'Consumption', color: 'var(--s-load)', values: days.map(d => d.load_kwh) },
        { key: 'import', label: 'Imported', color: 'var(--s-grid)', values: days.map(d => d.import_kwh) },
        { key: 'export', label: 'Exported', color: 'var(--s-bat)', values: days.map(d => d.export_kwh) },
      ],
    });
  }

  // ------------------------------------------------------------ controls
  $('range').addEventListener('click', e => {
    const btn = e.target.closest('[data-hours]');
    if (!btn) return;
    $('range').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === btn)));
    hours = Number(btn.dataset.hours);
    loadHistory();
  });

  $('toggle-table').addEventListener('click', e => {
    const btn = e.currentTarget;
    const showTable = btn.getAttribute('aria-pressed') !== 'true';
    btn.setAttribute('aria-pressed', String(showTable));
    $('power-table-wrap').hidden = !showTable;
    $('power-chart').hidden = showTable;
    $('power-legend').hidden = showTable;
  });

  deviceSelect.addEventListener('change', () => { device = deviceSelect.value; loadAll(); });

  function tickClock() {
    const now = new Date();
    $('clock-time').textContent = now.toLocaleTimeString(App.LOCALE, { hour: '2-digit', minute: '2-digit' });
    $('clock-date').textContent = now.toLocaleDateString(App.LOCALE, { weekday: 'short', day: '2-digit', month: 'short' });
  }

  function loadAll() { loadRealtime(); loadHistory(); loadDaily(); }

  tickClock();
  setInterval(tickClock, 10000);
  document.addEventListener('datarefresh', loadAll);
  loadAll();
  App.every(loadRealtime, 15000);
  App.every(loadHistory, 60000);
  App.every(loadDaily, 300000);
})();
