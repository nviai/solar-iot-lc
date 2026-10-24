/* Minimal dependency-free SVG charts.
 *
 * Mark specs follow the house rules: 2px lines, ~10% area washes, >=8px
 * markers with a 2px surface ring, <=24px bars with a 4px rounded data-end and
 * a 2px surface gap between neighbours, hairline recessive gridlines. Every
 * chart ships a crosshair/hover tooltip and a legend carrying live values.
 */
(function () {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const App = window.App;

  function el(name, attrs, parent) {
    const node = document.createElementNS(NS, name);
    for (const k in attrs) {
      if (attrs[k] !== null && attrs[k] !== undefined) node.setAttribute(k, attrs[k]);
    }
    if (parent) parent.appendChild(node);
    return node;
  }

  /** Round a range out to human tick values. */
  function niceScale(min, max, ticks) {
    if (min === max) { min -= 1; max += 1; }
    const raw = (max - min) / (ticks || 5);
    const mag = Math.pow(10, Math.floor(Math.log10(Math.abs(raw) || 1)));
    const norm = raw / mag;
    const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
    const lo = Math.floor(min / step) * step;
    const hi = Math.ceil(max / step) * step;
    const out = [];
    for (let v = lo; v <= hi + step / 2; v += step) out.push(Math.round(v / step) * step);
    return { min: lo, max: hi, ticks: out };
  }

  function makeTooltip(container) {
    const tip = document.createElement('div');
    tip.className = 'chart-tooltip';
    tip.dataset.visible = 'false';
    container.appendChild(tip);
    return tip;
  }

  function placeTooltip(tip, container, x, y) {
    const cw = container.clientWidth;
    const tw = tip.offsetWidth;
    let left = x + 14;
    if (left + tw > cw - 4) left = x - tw - 14;
    if (left < 4) left = 4;
    tip.style.left = left + 'px';
    tip.style.top = Math.max(4, y - tip.offsetHeight - 12) + 'px';
  }

  function tooltipRows(rows) {
    return rows.map(r =>
      '<div class="chart-tooltip__row">'
      + '<span class="chart-tooltip__key" style="background:' + r.color + '"></span>'
      + '<span class="chart-tooltip__label">' + r.label + '</span>'
      + '<span class="chart-tooltip__value">' + r.value + '</span>'
      + '</div>').join('');
  }

  function emptyState(container, message) {
    container.innerHTML = '<div class="chart__empty">' + message + '</div>';
  }

  // ====================================================================
  // Line / area chart
  // ====================================================================
  App.lineChart = function (container, config) {
    const state = { hidden: new Set(), config: config, tip: null };

    function render() {
      const cfg = state.config;
      const width = container.clientWidth || 640;
      const height = cfg.height || 280;

      container.querySelectorAll('svg').forEach(n => n.remove());
      if (!cfg.x || !cfg.x.length) {
        container.querySelectorAll('.chart-tooltip').forEach(n => n.remove());
        state.tip = null;
        emptyState(container, cfg.emptyMessage || 'No data yet.');
        return;
      }
      const emptyNode = container.querySelector('.chart__empty');
      if (emptyNode) emptyNode.remove();
      if (!state.tip) state.tip = makeTooltip(container);

      const m = { top: 12, right: 14, bottom: 26, left: 52 };
      const iw = Math.max(10, width - m.left - m.right);
      const ih = Math.max(10, height - m.top - m.bottom);

      const active = cfg.series.filter(s => !state.hidden.has(s.key));
      let lo = Infinity, hi = -Infinity;
      active.forEach(s => s.values.forEach(v => {
        if (v === null || v === undefined || Number.isNaN(v)) return;
        if (v < lo) lo = v;
        if (v > hi) hi = v;
      }));
      if (lo === Infinity) { lo = 0; hi = 1; }
      // Always anchor to zero so power sign is readable against a baseline.
      lo = Math.min(lo, 0);
      hi = Math.max(hi, 0);
      const scale = niceScale(lo, hi, 5);

      const n = cfg.x.length;
      const xAt = i => m.left + (n === 1 ? iw / 2 : (i / (n - 1)) * iw);
      const yAt = v => m.top + ih - ((v - scale.min) / (scale.max - scale.min)) * ih;

      const svg = el('svg', {
        viewBox: '0 0 ' + width + ' ' + height,
        role: 'img',
        'aria-label': cfg.ariaLabel || 'Line chart',
      }, container);

      // --- gridlines (hairline, solid, recessive) ---
      const grid = el('g', { class: 'chart__grid' }, svg);
      scale.ticks.forEach(t => {
        el('line', { x1: m.left, x2: m.left + iw, y1: yAt(t), y2: yAt(t) }, grid);
        el('text', {
          x: m.left - 8, y: yAt(t) + 4, class: 'chart__tick', 'text-anchor': 'end',
        }, svg).textContent = cfg.yTick ? cfg.yTick(t) : t;
      });
      // Zero baseline gets the axis colour so sign is legible.
      if (scale.min < 0 && scale.max > 0) {
        el('line', {
          x1: m.left, x2: m.left + iw, y1: yAt(0), y2: yAt(0), class: 'chart__axis',
          stroke: 'var(--axis)', 'stroke-width': 1,
        }, svg);
      }

      // --- x ticks ---
      const tickCount = Math.max(2, Math.min(7, Math.floor(iw / 90)));
      const step = Math.max(1, Math.floor((n - 1) / (tickCount - 1)) || 1);
      for (let i = 0; i < n; i += step) {
        // Pin the outermost labels inside the plot so they never clip.
        const anchor = i === 0 ? 'start' : i + step >= n && n > 1 ? 'end' : 'middle';
        el('text', {
          x: xAt(i), y: height - 6, class: 'chart__tick', 'text-anchor': anchor,
        }, svg).textContent = cfg.xTick ? cfg.xTick(cfg.x[i], i) : cfg.x[i];
      }

      // --- series ---
      active.forEach(s => {
        const pts = [];
        s.values.forEach((v, i) => {
          if (v === null || v === undefined || Number.isNaN(v)) return;
          pts.push([xAt(i), yAt(v)]);
        });
        if (!pts.length) return;

        if (cfg.area) {
          const base = yAt(Math.max(scale.min, 0));
          const d = 'M' + pts[0][0] + ',' + base
            + pts.map(p => 'L' + p[0] + ',' + p[1]).join('')
            + 'L' + pts[pts.length - 1][0] + ',' + base + 'Z';
          el('path', { d: d, class: 'chart__area', fill: s.color }, svg);
        }
        el('path', {
          d: 'M' + pts.map(p => p[0] + ',' + p[1]).join('L'),
          class: 'chart__line',
          stroke: s.color,
        }, svg);
      });

      // --- crosshair + hover layer ---
      const hover = el('g', { style: 'display:none' }, svg);
      const crosshair = el('line', {
        y1: m.top, y2: m.top + ih, class: 'chart__crosshair',
      }, hover);
      const dots = active.map(s => el('circle', {
        r: 4, fill: s.color, class: 'chart__dot',
      }, hover));

      const overlay = el('rect', {
        x: m.left, y: m.top, width: iw, height: ih,
        fill: 'transparent', style: 'cursor:crosshair',
      }, svg);

      function moveTo(clientX) {
        const box = svg.getBoundingClientRect();
        const px = (clientX - box.left) * (width / box.width);
        let idx = Math.round(((px - m.left) / iw) * (n - 1));
        idx = Math.max(0, Math.min(n - 1, idx));

        hover.style.display = '';
        crosshair.setAttribute('x1', xAt(idx));
        crosshair.setAttribute('x2', xAt(idx));

        const rows = [];
        active.forEach((s, k) => {
          const v = s.values[idx];
          const dot = dots[k];
          if (v === null || v === undefined || Number.isNaN(v)) {
            dot.style.display = 'none';
          } else {
            dot.style.display = '';
            dot.setAttribute('cx', xAt(idx));
            dot.setAttribute('cy', yAt(v));
            rows.push({ color: s.color, label: s.label, value: cfg.format ? cfg.format(v) : v });
          }
        });

        state.tip.innerHTML =
          '<div class="chart-tooltip__title">'
          + (cfg.xLabel ? cfg.xLabel(cfg.x[idx], idx) : cfg.x[idx]) + '</div>'
          + tooltipRows(rows);
        state.tip.dataset.visible = 'true';
        placeTooltip(state.tip, container, xAt(idx) * (container.clientWidth / width), m.top + ih / 2);
      }

      overlay.addEventListener('pointermove', e => moveTo(e.clientX));
      overlay.addEventListener('pointerdown', e => moveTo(e.clientX));
      overlay.addEventListener('pointerleave', () => {
        hover.style.display = 'none';
        state.tip.dataset.visible = 'false';
      });
    }

    // Legend: identity is never colour-alone, and the live values here are the
    // relief for the light-mode sub-3:1 series (yellow / aqua).
    function renderLegend() {
      const cfg = state.config;
      if (!cfg.legend) return;
      const host = typeof cfg.legend === 'string'
        ? document.querySelector(cfg.legend) : cfg.legend;
      if (!host) return;
      host.innerHTML = '';
      cfg.series.forEach(s => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'legend__item';
        btn.setAttribute('aria-pressed', state.hidden.has(s.key) ? 'false' : 'true');
        const last = [...s.values].reverse().find(v => v !== null && v !== undefined && !Number.isNaN(v));
        btn.innerHTML =
          '<span class="legend__key" style="background:' + s.color + '"></span>'
          + '<span>' + s.label + '</span>'
          + '<span class="legend__value">'
          + (last === undefined ? '—' : (cfg.format ? cfg.format(last) : last)) + '</span>';
        btn.addEventListener('click', () => {
          if (state.hidden.has(s.key)) state.hidden.delete(s.key);
          else if (state.hidden.size < cfg.series.length - 1) state.hidden.add(s.key);
          renderLegend();
          render();
        });
        host.appendChild(btn);
      });
    }

    function draw() { render(); renderLegend(); }

    draw();

    if (window.ResizeObserver) {
      let w = container.clientWidth;
      new ResizeObserver(() => {
        if (Math.abs(container.clientWidth - w) > 2) { w = container.clientWidth; render(); }
      }).observe(container);
    }
    document.addEventListener('themechange', draw);

    return {
      update(next) { state.config = Object.assign({}, state.config, next); draw(); },
    };
  };

  // ====================================================================
  // Grouped bar chart
  // ====================================================================
  App.barChart = function (container, config) {
    const state = { config: config, tip: null };

    function render() {
      const cfg = state.config;
      const width = container.clientWidth || 640;
      const height = cfg.height || 260;

      container.querySelectorAll('svg').forEach(n => n.remove());
      if (!cfg.categories || !cfg.categories.length) {
        container.querySelectorAll('.chart-tooltip').forEach(n => n.remove());
        state.tip = null;
        emptyState(container, cfg.emptyMessage || 'No data yet.');
        return;
      }
      const emptyNode = container.querySelector('.chart__empty');
      if (emptyNode) emptyNode.remove();
      if (!state.tip) state.tip = makeTooltip(container);

      const m = { top: 12, right: 14, bottom: 26, left: 52 };
      const iw = Math.max(10, width - m.left - m.right);
      const ih = Math.max(10, height - m.top - m.bottom);

      let hi = 0;
      cfg.series.forEach(s => s.values.forEach(v => { if (v > hi) hi = v; }));
      const scale = niceScale(0, hi || 1, 4);
      const yAt = v => m.top + ih - ((v - scale.min) / (scale.max - scale.min)) * ih;

      const groups = cfg.categories.length;
      const bandWidth = iw / groups;
      const GAP = 2;               // surface gap between touching bars
      const inner = bandWidth * 0.72;
      const barW = Math.min(24, Math.max(3, (inner - GAP * (cfg.series.length - 1)) / cfg.series.length));

      const svg = el('svg', {
        viewBox: '0 0 ' + width + ' ' + height,
        role: 'img',
        'aria-label': cfg.ariaLabel || 'Bar chart',
      }, container);

      const grid = el('g', { class: 'chart__grid' }, svg);
      scale.ticks.forEach(t => {
        el('line', { x1: m.left, x2: m.left + iw, y1: yAt(t), y2: yAt(t) }, grid);
        el('text', {
          x: m.left - 8, y: yAt(t) + 4, class: 'chart__tick', 'text-anchor': 'end',
        }, svg).textContent = cfg.yTick ? cfg.yTick(t) : t;
      });

      const labelEvery = Math.ceil(groups / Math.max(2, Math.floor(iw / 60)));

      cfg.categories.forEach((cat, gi) => {
        const groupX = m.left + gi * bandWidth;
        const totalW = cfg.series.length * barW + (cfg.series.length - 1) * GAP;
        const startX = groupX + (bandWidth - totalW) / 2;

        if (gi % labelEvery === 0) {
          el('text', {
            x: groupX + bandWidth / 2, y: height - 6,
            class: 'chart__tick', 'text-anchor': 'middle',
          }, svg).textContent = cfg.xTick ? cfg.xTick(cat, gi) : cat;
        }

        cfg.series.forEach((s, si) => {
          const v = s.values[gi] || 0;
          const x = startX + si * (barW + GAP);
          const y = yAt(v);
          const h = Math.max(0, m.top + ih - y);
          // 4px rounded data-end, square at the baseline.
          const r = Math.min(4, barW / 2, h);
          const d = h <= 0 ? '' :
            'M' + x + ',' + (m.top + ih)
            + 'V' + (y + r)
            + 'a' + r + ',' + r + ' 0 0 1 ' + r + ',' + -r
            + 'h' + (barW - 2 * r)
            + 'a' + r + ',' + r + ' 0 0 1 ' + r + ',' + r
            + 'V' + (m.top + ih) + 'Z';
          if (!d) return;
          const bar = el('path', { d: d, fill: s.color }, svg);

          bar.addEventListener('pointerenter', () => {
            const rows = cfg.series.map(ss => ({
              color: ss.color, label: ss.label,
              value: cfg.format ? cfg.format(ss.values[gi] || 0) : (ss.values[gi] || 0),
            }));
            state.tip.innerHTML =
              '<div class="chart-tooltip__title">'
              + (cfg.xLabel ? cfg.xLabel(cat, gi) : cat) + '</div>' + tooltipRows(rows);
            state.tip.dataset.visible = 'true';
            placeTooltip(state.tip, container,
              (groupX + bandWidth / 2) * (container.clientWidth / width), y);
          });
          bar.addEventListener('pointerleave', () => { state.tip.dataset.visible = 'false'; });
        });
      });
    }

    function renderLegend() {
      const cfg = state.config;
      if (!cfg.legend) return;
      const host = typeof cfg.legend === 'string' ? document.querySelector(cfg.legend) : cfg.legend;
      if (!host) return;
      host.innerHTML = cfg.series.map(s =>
        '<span class="legend__item">'
        + '<span class="legend__key legend__key--block" style="background:' + s.color + '"></span>'
        + s.label + '</span>').join('');
    }

    function draw() { render(); renderLegend(); }
    draw();

    if (window.ResizeObserver) {
      let w = container.clientWidth;
      new ResizeObserver(() => {
        if (Math.abs(container.clientWidth - w) > 2) { w = container.clientWidth; render(); }
      }).observe(container);
    }
    document.addEventListener('themechange', draw);

    return { update(next) { state.config = Object.assign({}, state.config, next); draw(); } };
  };

  // ====================================================================
  // Sparkline — de-emphasised trend inside a stat tile
  // ====================================================================
  App.sparkline = function (container, values, color) {
    container.innerHTML = '';
    const clean = values.filter(v => v !== null && v !== undefined && !Number.isNaN(v));
    if (clean.length < 2) return;

    const w = container.clientWidth || 160;
    const h = container.clientHeight || 34;
    const lo = Math.min(0, ...clean);
    const hi = Math.max(...clean) || 1;
    const svg = el('svg', { viewBox: '0 0 ' + w + ' ' + h, 'aria-hidden': 'true' }, container);

    const pts = values.map((v, i) => [
      (i / (values.length - 1)) * w,
      h - 2 - ((v - lo) / ((hi - lo) || 1)) * (h - 4),
    ]);
    el('path', {
      d: 'M' + pts[0][0] + ',' + h + 'L' + pts.map(p => p[0] + ',' + p[1]).join('L')
        + 'L' + w + ',' + h + 'Z',
      fill: color, opacity: 0.10,
    }, svg);
    el('path', {
      d: 'M' + pts.map(p => p[0] + ',' + p[1]).join('L'),
      fill: 'none', stroke: color, 'stroke-width': 2,
      'stroke-linejoin': 'round', 'stroke-linecap': 'round',
    }, svg);
  };
})();
