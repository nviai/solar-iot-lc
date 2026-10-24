/* Alerts page: rule list, rule editor dialog and the event timeline. */
(function () {
  'use strict';

  const App = window.App;
  const $ = App.$;
  const esc = App.escape;

  const SEVERITY = {
    info: { label: 'Info', tone: 'info', icon: 'info' },
    warning: { label: 'Warning', tone: 'warn', icon: 'alert' },
    critical: { label: 'Critical', tone: 'fault', icon: 'alert' },
  };
  const STATE = {
    ok: { label: 'Normal', tone: 'ok' },
    pending: { label: 'Watching', tone: 'warn' },
    firing: { label: 'Firing', tone: 'fault' },
  };

  let catalogue = { metrics: [], operators: [], devices: [] };
  const metricByKey = {};
  const symbolByOp = {};
  let rules = [];

  const dialog = $('rule-dialog');

  // ------------------------------------------------------------ catalogue
  async function loadCatalogue() {
    catalogue = await App.api('/api/v1/alerts/catalogue');
    catalogue.metrics.forEach(m => { metricByKey[m.key] = m; });
    catalogue.operators.forEach(o => { symbolByOp[o.key] = o.symbol; });

    $('f-metric').innerHTML = catalogue.metrics.map(m =>
      '<option value="' + m.key + '">' + esc(m.label) + ' (' + esc(m.unit) + ')</option>').join('');
    $('f-op').innerHTML = catalogue.operators.map(o =>
      '<option value="' + o.key + '">' + esc(o.label) + '</option>').join('');
    $('f-device').innerHTML = '<option value="*">All devices</option>'
      + catalogue.devices.map(d => '<option value="' + esc(d) + '">' + esc(d) + '</option>').join('');
  }

  function updateUnit() {
    const metric = metricByKey[$('f-metric').value];
    $('f-unit').textContent = metric ? metric.unit : '';
  }
  $('f-metric').addEventListener('change', updateUnit);

  // ---------------------------------------------------------------- rules
  function conditionText(r) {
    const metric = metricByKey[r.metric] || { label: r.metric, unit: '' };
    return esc(metric.label) + ' ' + (symbolByOp[r.op] || r.op) + ' '
      + esc(r.threshold) + ' ' + esc(metric.unit)
      + (r.duration_s ? '<div class="muted">for ' + App.fmt.duration(r.duration_s) + '</div>' : '');
  }

  function ruleRow(r) {
    const sev = SEVERITY[r.severity] || SEVERITY.warning;
    const state = r.enabled ? (STATE[r.state] || STATE.ok) : { label: 'Disabled', tone: 'idle' };
    return '<tr class="rule-row" data-enabled="' + r.enabled + '">'
      + '<td><div class="rule-name">' + esc(r.name) + '</div>'
      + '<div class="muted">' + (r.register_no === '*' ? 'All devices' : esc(r.register_no))
      + ' · <span style="color:var(--' + sev.tone + ')">' + sev.label + '</span>'
      + ' · ' + App.fmt.duration(r.cooldown_s) + ' cooldown</div></td>'
      + '<td>' + conditionText(r) + '</td>'
      + '<td><span class="badge" data-tone="' + state.tone + '">' + state.label + '</span></td>'
      + '<td><div class="table-actions">'
      + '<label class="switch" title="' + (r.enabled ? 'Disable' : 'Enable') + '">'
      + '<input type="checkbox" data-act="toggle" data-id="' + r.id + '"' + (r.enabled ? ' checked' : '') + '>'
      + '<span class="switch__track"><span class="switch__thumb"></span></span>'
      + '<span class="visually-hidden">Enabled</span></label>'
      + '<button class="btn btn--ghost btn--sm btn--icon" data-act="edit" data-id="' + r.id + '" aria-label="Edit">' + App.icon('edit') + '</button>'
      + '<button class="btn btn--ghost btn--sm btn--icon btn--danger" data-act="delete" data-id="' + r.id + '" aria-label="Delete">' + App.icon('trash') + '</button>'
      + '</div></td></tr>';
  }

  async function loadRules() {
    ({ rules } = await App.api('/api/v1/alerts/rules'));
    const enabled = rules.filter(r => r.enabled).length;
    $('rule-count').textContent = rules.length + ' rule' + (rules.length === 1 ? '' : 's') + ' · ' + enabled + ' enabled';
    $('rules-empty').hidden = rules.length > 0;
    $('rules-body').innerHTML = rules.map(ruleRow).join('');
  }

  $('rules-body').addEventListener('change', async e => {
    const input = e.target.closest('input[data-act="toggle"]');
    if (!input) return;
    try {
      await App.api('/api/v1/alerts/rules/' + input.dataset.id, {
        method: 'PATCH', body: { enabled: input.checked },
      });
    } catch (err) {
      App.toast(err.message, 'fault');
    }
    loadRules();
  });

  $('rules-body').addEventListener('click', async e => {
    const btn = e.target.closest('button[data-act]');
    if (!btn) return;
    const rule = rules.find(r => String(r.id) === btn.dataset.id);
    if (btn.dataset.act === 'edit') { openEditor(rule); return; }
    if (btn.dataset.act === 'delete') {
      if (!confirm('Delete the rule "' + rule.name + '"?')) return;
      try {
        await App.api('/api/v1/alerts/rules/' + rule.id, { method: 'DELETE' });
        App.toast('Rule deleted', 'ok');
        loadRules();
      } catch (err) {
        App.toast(err.message, 'fault');
      }
    }
  });

  // --------------------------------------------------------------- editor
  function openEditor(rule) {
    $('rule-dialog-title').textContent = rule ? 'Edit rule' : 'New rule';
    $('rule-id').value = rule ? rule.id : '';
    $('f-name').value = rule ? rule.name : '';
    $('f-metric').value = rule ? rule.metric : 'load_power';
    $('f-op').value = rule ? rule.op : 'gt';
    $('f-threshold').value = rule ? rule.threshold : '';
    $('f-duration').value = rule ? rule.duration_s : 0;
    $('f-cooldown').value = rule ? rule.cooldown_s : 900;
    $('f-severity').value = rule ? rule.severity : 'warning';
    $('f-device').value = rule ? rule.register_no : '*';
    updateUnit();
    dialog.showModal();
    $('f-name').focus();
  }

  $('new-rule-btn').addEventListener('click', () => openEditor(null));
  dialog.addEventListener('click', e => {
    if (e.target.closest('[data-close]') || e.target === dialog) dialog.close();
  });

  $('rule-form').addEventListener('submit', async e => {
    e.preventDefault();
    const id = $('rule-id').value;
    const body = {
      name: $('f-name').value.trim(),
      metric: $('f-metric').value,
      op: $('f-op').value,
      threshold: Number($('f-threshold').value),
      duration_s: Number($('f-duration').value) || 0,
      cooldown_s: Number($('f-cooldown').value) || 0,
      severity: $('f-severity').value,
      register_no: $('f-device').value,
    };
    await App.busy($('rule-save'), async () => {
      try {
        if (id) await App.api('/api/v1/alerts/rules/' + id, { method: 'PATCH', body: body });
        else await App.api('/api/v1/alerts/rules', { method: 'POST', body: body });
        App.toast(id ? 'Rule updated' : 'Rule created', 'ok');
        dialog.close();
        loadRules();
      } catch (err) {
        App.toast(err.message, 'fault');
      }
    });
  });

  // --------------------------------------------------------------- events
  function eventItem(ev) {
    const resolved = ev.kind === 'resolved';
    const sev = SEVERITY[ev.severity] || SEVERITY.warning;
    return '<div class="timeline__item" data-tone="' + (resolved ? 'ok' : sev.tone) + '"'
      + (ev.acknowledged ? '' : ' data-unread') + '>'
      + '<span class="timeline__icon">' + App.icon(resolved ? 'check' : sev.icon) + '</span>'
      + '<div><div class="timeline__title">' + esc(ev.message) + '</div>'
      + '<div class="timeline__meta">' + esc(ev.rule_name) + ' · ' + App.fmt.dateTime(ev.ts)
      + ' · ' + App.fmt.ago(ev.ts) + '</div></div></div>';
  }

  async function loadEvents() {
    const { events, unacked } = await App.api('/api/v1/alerts/events?limit=100');
    $('event-count').textContent = events.length + ' events · ' + unacked + ' unread';
    $('events-empty').hidden = events.length > 0;
    $('events').innerHTML = events.map(eventItem).join('');
    $('ack-all').disabled = !unacked;
  }

  $('ack-all').addEventListener('click', async () => {
    try {
      await App.api('/api/v1/alerts/events/ack', { method: 'POST', body: {} });
    } catch (err) {
      App.toast(err.message, 'fault');
    }
    loadEvents();
    App.refreshStatus();
  });

  (async function init() {
    try {
      await loadCatalogue();
      await Promise.all([loadRules(), loadEvents()]);
    } catch (e) {
      App.toast('Could not load alerts: ' + e.message, 'fault');
    }
    App.every(loadEvents, 30000);
    App.every(loadRules, 60000);
  })();
})();
