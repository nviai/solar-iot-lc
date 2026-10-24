/* System page: live solar-feed health and per-switch reachability. */
(function () {
  'use strict';

  const App = window.App;
  const $ = App.$;

  function renderFeed(solar) {
    const [tone, label] = App.feedSummary(solar);
    $('sys-feed-badge').dataset.tone = tone;
    $('sys-feed-badge').textContent = solar.error ? 'Unreachable' : label.replace('Solar feed · ', '');
    $('sys-fetched').textContent = solar.fetched_at
      ? App.fmt.dateTime(solar.fetched_at) + ' (' + App.fmt.ago(solar.fetched_at) + ')' : 'Never';
    $('sys-age').textContent = solar.age_seconds == null ? '—' : App.fmt.duration(solar.age_seconds);
    $('sys-paused').textContent = solar.paused ? 'Paused for quiet hours' : 'Active';
    $('sys-error').textContent = solar.error || 'None';
  }

  async function load() {
    try {
      const [status, devices] = await Promise.all([
        App.api('/api/v1/status'),
        App.api('/api/v1/devices'),
      ]);
      renderFeed(status.solar);
      devices.devices.forEach(snap => {
        const badge = document.querySelector('[data-sys-device="' + CSS.escape(snap.id) + '"]');
        if (badge) App.linkBadge(badge, snap);
      });
    } catch (e) {
      App.toast('Could not load status: ' + e.message, 'fault');
    }
  }

  const feedBtn = $('btn-feed');
  feedBtn.addEventListener('click', () => App.busy(feedBtn, async () => {
    try {
      const { status } = await App.api('/api/v1/solar?refresh=true');
      renderFeed(status);
      App.toast(status.error ? status.error : 'Solar Monitor answered', status.error ? 'fault' : 'ok');
      App.refreshShell();
    } catch (e) {
      App.toast(e.message, 'fault');
    }
  }));

  load();
  App.every(load, 10000);
})();
