/* History page: totals, daily roll-up and the battery state-of-charge trace. */
(function () {
  'use strict';

  const App = window.App;
  const $ = App.$;
  const fmt = App.fmt;
  const price = window.ENERGY_PRICE || 0;
  const currency = window.CURRENCY || '';

  const deviceSelect = $('h-device');
  let device = deviceSelect.value || null;
  let hours = 168;

  const dailyChart = App.barChart($('hist-daily'), {
    categories: [], series: [], height: 280, legend: '#hist-legend',
    format: v => v.toFixed(1) + ' kWh',
    yTick: v => String(v),
    xTick: d => fmt.day(d),
    xLabel: d => fmt.day(d),
    ariaLabel: 'Energy by day',
    emptyMessage: 'Not enough samples to summarise by day yet.',
  });

  const socChart = App.lineChart($('soc-chart'), {
    x: [], series: [{ key: 'soc', label: 'State of charge', color: 'var(--s-bat)', values: [] }],
    height: 220, area: true, legend: '#soc-legend',
    format: v => Math.round(v) + '%',
    yTick: v => v + '%',
    xTick: t => fmt.dateTime(t),
    xLabel: t => fmt.dateTime(t),
    ariaLabel: 'Battery state of charge over time',
    emptyMessage: 'No state-of-charge data — this system may not have a battery.',
  });

  const money = kwh => (price ? Math.round(kwh * price).toLocaleString(App.LOCALE) + ' ' + currency : '');
  const devicePath = () => '/api/v1/devices/' + encodeURIComponent(device);

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

  function renderTotals(rows) {
    const total = rows.reduce((acc, r) => ({
      pv: acc.pv + r.pv_kwh,
      load: acc.load + r.load_kwh,
      imported: acc.imported + r.import_kwh,
      exported: acc.exported + r.export_kwh,
    }), { pv: 0, load: 0, imported: 0, exported: 0 });

    const split = kwh => {
      const [value, unit] = fmt.energy(kwh).split(' ');
      return value + (unit ? '<small>' + unit + '</small>' : '');
    };
    $('s-pv').innerHTML = split(total.pv);
    $('s-load').innerHTML = split(total.load);
    $('s-import').innerHTML = split(total.imported);
    $('s-export').innerHTML = split(total.exported);

    const days = rows.length || 1;
    $('s-pv-meta').innerHTML = '<strong>' + (total.pv / days).toFixed(1) + ' kWh</strong> per day';
    $('s-load-meta').innerHTML = '<strong>' + (total.load / days).toFixed(1) + ' kWh</strong> per day';
    $('s-import-meta').innerHTML = price ? 'About <strong>' + money(total.imported) + '</strong>' : days + ' days';
    $('s-export-meta').innerHTML = price ? 'About <strong>' + money(total.exported) + '</strong>' : days + ' days';
  }

  function renderTable(rows) {
    $('daily-empty').hidden = rows.length > 0;
    $('daily-table').innerHTML = [...rows].reverse().map(r => {
      const self = r.load_kwh > 0
        ? Math.round(Math.max(0, (r.load_kwh - r.import_kwh) / r.load_kwh) * 100) + '%' : '—';
      const saved = price
        ? '<td class="num">' + money(Math.max(0, r.load_kwh - r.import_kwh)) + '</td>' : '';
      return '<tr><td>' + fmt.day(r.day) + '</td>'
        + '<td class="num">' + r.pv_kwh.toFixed(1) + '</td>'
        + '<td class="num">' + r.load_kwh.toFixed(1) + '</td>'
        + '<td class="num">' + r.import_kwh.toFixed(1) + '</td>'
        + '<td class="num">' + r.export_kwh.toFixed(1) + '</td>'
        + '<td class="num">' + self + '</td>' + saved + '</tr>';
    }).join('');
  }

  async function loadDaily() {
    const days = Math.max(1, Math.min(90, Math.ceil(hours / 24)));
    const { days: rows } = await App.api(devicePath() + '/daily?days=' + days);
    dailyChart.update({
      categories: rows.map(r => r.day),
      series: [
        { key: 'pv', label: 'Solar', color: 'var(--s-pv)', values: rows.map(r => r.pv_kwh) },
        { key: 'load', label: 'Consumption', color: 'var(--s-load)', values: rows.map(r => r.load_kwh) },
        { key: 'import', label: 'Imported', color: 'var(--s-grid)', values: rows.map(r => r.import_kwh) },
        { key: 'export', label: 'Exported', color: 'var(--s-bat)', values: rows.map(r => r.export_kwh) },
      ],
    });
    renderTotals(rows);
    renderTable(rows);
  }

  async function loadSoc() {
    const { points } = await App.api(devicePath() + '/history?hours=' + hours);
    const withSoc = points.filter(p => p.soc != null);
    socChart.update({
      x: withSoc.map(p => p.ts),
      series: [{ key: 'soc', label: 'State of charge', color: 'var(--s-bat)', values: withSoc.map(p => p.soc) }],
    });
  }

  async function loadAll() {
    try {
      if (!(await resolveDevice())) return;
      await Promise.all([loadDaily(), loadSoc()]);
    } catch (e) {
      App.toast('Could not load history: ' + e.message, 'fault');
    }
  }

  $('h-range').addEventListener('click', e => {
    const btn = e.target.closest('[data-hours]');
    if (!btn) return;
    $('h-range').querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b === btn)));
    hours = Number(btn.dataset.hours);
    loadAll();
  });
  deviceSelect.addEventListener('change', () => { device = deviceSelect.value; loadAll(); });
  document.addEventListener('datarefresh', loadAll);
  loadAll();
})();
