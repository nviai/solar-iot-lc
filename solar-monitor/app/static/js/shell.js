/* Sidebar status for the monitor: poller health, unread alerts, manual poll. */
(function () {
  'use strict';

  const App = window.App;
  const $ = App.$;

  function show(tone, label, detail, pulse) {
    const dot = $('poll-dot');
    dot.dataset.tone = tone;
    if (pulse) dot.setAttribute('data-pulse', ''); else dot.removeAttribute('data-pulse');
    $('poll-label').textContent = label;
    $('poll-detail').textContent = detail;
  }

  App.refreshStatus = async function () {
    let s;
    try {
      s = await App.api('/api/v1/status');
    } catch (e) {
      show('fault', 'Disconnected', 'Cannot reach the server');
      return null;
    }

    const errors = Object.keys(s.errors || {}).length;
    const every = 'every ' + App.fmt.duration(s.interval_seconds);
    if (!s.configured) show('warn', 'Not configured', 'Add SolaX credentials');
    else if (errors) show('fault', errors + ' device error' + (errors > 1 ? 's' : ''), 'See Settings');
    else if (s.paused) show('idle', 'Paused', 'Quiet hours');
    else if (!s.running) show('warn', 'Poller stopped', s.demo_mode ? 'Demo data' : 'SolaX Cloud');
    else show('ok', s.last_poll_at ? 'Live · ' + App.fmt.ago(s.last_poll_at) : 'Live', (s.demo_mode ? 'Demo data · ' : '') + every, true);

    const badge = $('alert-badge');
    if (badge) {
      badge.hidden = !s.unacked_alerts;
      badge.textContent = s.unacked_alerts > 99 ? '99+' : s.unacked_alerts;
    }
    return s;
  };

  const refreshBtn = $('refresh-btn');
  refreshBtn.addEventListener('click', () => App.busy(refreshBtn, async () => {
    try {
      const r = await App.api('/api/v1/refresh', { method: 'POST' });
      const errors = Object.values(r.errors || {});
      if (errors.length) App.toast(errors[0], 'fault');
      else App.toast(r.polled ? 'Fresh data from SolaX' : 'No devices to poll yet', r.polled ? 'ok' : 'warn');
      document.dispatchEvent(new CustomEvent('datarefresh'));
    } catch (e) {
      App.toast('Refresh failed: ' + e.message, 'fault');
    }
    App.refreshStatus();
  }));

  App.refreshStatus();
  App.every(App.refreshStatus, 20000);
})();
