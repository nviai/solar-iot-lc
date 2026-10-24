/* Activity page: every relay movement across all devices. */
(function () {
  'use strict';

  const App = window.App;
  const $ = App.$;
  const filter = $('device-filter');

  async function load() {
    const device = filter ? filter.value : '';
    try {
      const { events } = await App.api('/api/v1/events?limit=200'
        + (device ? '&device=' + encodeURIComponent(device) : ''));
      App.renderEvents($('events'), $('events-empty'), events, !device);
    } catch (e) {
      App.toast('Could not load activity: ' + e.message, 'fault');
    }
  }

  if (filter) filter.addEventListener('change', load);
  load();
  App.every(load, 20000);
})();
