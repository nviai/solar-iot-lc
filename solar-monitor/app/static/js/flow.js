/* Energy-flow diagram: solar, grid and battery around the house, as a small
 * 3D scene.
 *
 * The four nodes are small models on a tilted floor — solar panels on a roof,
 * a high-voltage pylon, a battery cabinet and the house — and energy streams
 * between them along lit tracks. The models react to the data: the panels
 * glint while producing, the cabinet's gauge shows the state of charge, the
 * windows light up with the load. The labels are billboards that
 * counter-rotate so they always face the viewer while the floor sways.
 *
 * An edge is drawn only when power is actually moving. Direction never relies
 * on colour or animation alone: each track carries an arrowhead and a value
 * plate, both legible with motion off. The scene is plain CSS 3D transforms
 * and SVG; no WebGL is needed.
 */
(function () {
  'use strict';

  const NS = 'http://www.w3.org/2000/svg';
  const App = window.App;

  function el(name, attrs, parent, text) {
    const node = document.createElementNS(NS, name);
    for (const k in attrs) if (attrs[k] != null) node.setAttribute(k, attrs[k]);
    if (text != null) node.textContent = text;
    if (parent) parent.appendChild(node);
    return node;
  }

  function div(cls, parent, html) {
    const d = document.createElement('div');
    d.className = cls;
    if (html != null) d.innerHTML = html;
    if (parent) parent.appendChild(d);
    return d;
  }

  // ------------------------------------------------------------ 3D models
  // Every model is built from flat faces placed with CSS 3D transforms, in
  // floor coordinates: x right, y toward the viewer, z up.

  function face(parent, cls, css, html) {
    const f = div('m3__f ' + cls, parent, html);
    f.style.cssText = css;
    return f;
  }

  /** A w × d × h box centred on (x, y), its base at height z. */
  function box(parent, cls, o) {
    const { w, d, h } = o;
    const b = div('m3 ' + cls, parent);
    b.style.cssText = 'left:' + ((o.x || 0) - w / 2) + 'px;top:' + ((o.y || 0) - d / 2) + 'px;width:' + w
      + 'px;height:' + d + 'px;transform:translateZ(' + (o.z || 0) + 'px)';
    face(b, 'm3__f--top', 'left:0;top:0;width:' + w + 'px;height:' + d + 'px;transform:translateZ(' + h + 'px)');
    face(b, 'm3__f--front', 'left:0;top:' + (d - h) + 'px;width:' + w + 'px;height:' + h
      + 'px;transform-origin:50% 100%;transform:rotateX(-90deg)', o.front);
    face(b, 'm3__f--back', 'left:0;top:0;width:' + w + 'px;height:' + h
      + 'px;transform-origin:50% 0;transform:rotateX(90deg)');
    face(b, 'm3__f--right', 'left:' + (w - h) + 'px;top:0;width:' + h + 'px;height:' + d
      + 'px;transform-origin:100% 50%;transform:rotateY(90deg)', o.right);
    face(b, 'm3__f--left', 'left:0;top:0;width:' + h + 'px;height:' + d
      + 'px;transform-origin:0 50%;transform:rotateY(-90deg)');
    return b;
  }

  /** A gable roof over a w × d footprint at height z, ridge along x. */
  function roof(parent, cls, o) {
    const { w, d, rise } = o, over = o.over || 0;
    const half = d / 2, len = Math.hypot(half, rise);
    const deg = Math.atan2(rise, half) * 180 / Math.PI;
    const r = div('m3 ' + cls, parent);
    r.style.cssText = 'left:' + (-w / 2) + 'px;top:' + (-d / 2) + 'px;width:' + w + 'px;height:' + d
      + 'px;transform:translateZ(' + o.z + 'px)';
    const slope = (side, top, origin, angle) => face(r, 'm3__f--slope m3__f--slope-' + side,
      'left:' + (-over) + 'px;top:' + top + 'px;width:' + (w + 2 * over) + 'px;height:' + len
      + 'px;transform-origin:' + origin + ';transform:rotateX(' + angle + 'deg)', o.slope);
    slope('front', d - len, '50% 100%', -deg);
    slope('back', 0, '50% 0', deg);
    face(r, 'm3__f--gable', 'left:0;top:0;width:' + rise + 'px;height:' + d
      + 'px;transform-origin:0 50%;transform:rotateY(-90deg);clip-path:polygon(0 0,0 100%,100% 50%)');
    face(r, 'm3__f--gable m3__f--gable-right', 'left:' + (w - rise) + 'px;top:0;width:' + rise + 'px;height:' + d
      + 'px;transform-origin:100% 50%;transform:rotateY(90deg);clip-path:polygon(100% 0,100% 100%,0 50%)');
    return r;
  }

  /** Lattice transmission tower, as SVG for the crossed billboard planes. */
  function pylonSvg(withWires) {
    const W = 150, H = 128, cx = 75;
    let d = 'M' + (cx - 24) + ' ' + H + ' L' + (cx - 8) + ' 14 L' + cx + ' 0 L' + (cx + 8) + ' 14 L' + (cx + 24) + ' ' + H;
    // Legs taper from ±24 at the foot to ±8 at the waist; X-bracing between levels.
    const levels = [128, 100, 76, 56, 40, 26, 14];
    for (let i = 0; i < levels.length - 1; i++) {
      const y1 = levels[i], y2 = levels[i + 1];
      const a = 8 + (24 - 8) * (y1 - 14) / (H - 14), b = 8 + (24 - 8) * (y2 - 14) / (H - 14);
      d += ' M' + (cx - a) + ' ' + y1 + ' L' + (cx + b) + ' ' + y2 + ' M' + (cx + a) + ' ' + y1 + ' L' + (cx - b) + ' ' + y2;
      d += ' M' + (cx - b) + ' ' + y2 + ' L' + (cx + b) + ' ' + y2;
    }
    // Cross-arms with hanging insulators.
    const arms = [[24, 34], [44, 26]];
    arms.forEach(([y, half]) => {
      d += ' M' + (cx - half - 8) + ' ' + y + ' L' + (cx + half + 8) + ' ' + y;
      d += ' M' + (cx - half - 8) + ' ' + y + ' L' + (cx - 10) + ' ' + (y - 7) + ' M' + (cx + half + 8) + ' ' + y + ' L' + (cx + 10) + ' ' + (y - 7);
      d += ' M' + (cx - half - 6) + ' ' + y + ' v7 M' + (cx + half + 6) + ' ' + y + ' v7';
    });
    let wires = '';
    if (withWires) {
      arms.forEach(([y, half]) => {
        const l = cx - half - 6, r = cx + half + 6, yy = y + 7;
        wires += '<path class="m3__wire" d="M0 ' + (yy + 10) + ' Q' + (l / 2) + ' ' + (yy + 16) + ' ' + l + ' ' + yy + '"/>'
          + '<path class="m3__wire" d="M' + r + ' ' + yy + ' Q' + ((r + W) / 2) + ' ' + (yy + 16) + ' ' + W + ' ' + (yy + 10) + '"/>';
      });
    }
    return '<svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="xMidYMax meet">' + wires
      + '<path class="m3__steel" d="' + d + '"/></svg>';
  }

  /** Build one model under a node; returns the height its label sits at. */
  const MODELS = {
    pv(host) {
      box(host, 'm3--wall', { w: 96, d: 70, h: 18 });
      roof(host, 'm3--panels', {
        w: 96, d: 70, rise: 26, z: 18, over: 5,
        slope: '<i class="m3__cells"></i><i class="m3__glint"></i>',
      });
      return 50;
    },
    grid(host) {
      // Two crossed planes read as a solid tower from any angle of the sway.
      div('m3__plane', host, pylonSvg(true));
      div('m3__plane m3__plane--cross', host, pylonSvg(false));
      return 128;
    },
    battery(host) {
      const front = '<i class="m3__vents"></i><i class="m3__gauge"><b></b></i><i class="m3__led"></i>';
      box(host, 'm3--cabinet', { x: -17, w: 32, d: 36, h: 80, front });
      box(host, 'm3--cabinet', { x: 17, w: 32, d: 36, h: 80, front });
      box(host, 'm3--plinth', { w: 74, d: 42, h: 5 });
      return 84;
    },
    home(host) {
      box(host, 'm3--house', {
        w: 64, d: 52, h: 40,
        front: '<i class="m3__win"></i><i class="m3__door"></i><i class="m3__win"></i>',
        right: '<i class="m3__win m3__win--side"></i>',
      });
      roof(host, 'm3--roof', { w: 64, d: 52, rise: 24, z: 40, over: 5 });
      box(host, 'm3--chimney', { x: 18, y: -8, w: 9, d: 9, h: 22, z: 44 });
      return 68;
    },
  };

  /** Floor is a 420 × 420 plane; nodes sit on it at these points. */
  const SIZE = 420;
  const NODES = {
    pv:      { x: 196, y: 64,  label: 'Solar',   color: 'var(--s-pv)',   icon: 'sun' },
    grid:    { x: 372, y: 196, label: 'Grid',    color: 'var(--s-grid)', icon: 'bolt' },
    battery: { x: 22,  y: 236, label: 'Battery', color: 'var(--s-bat)',  icon: 'battery' },
    home:    { x: 214, y: 352, label: 'Home',    color: 'var(--s-load)', icon: 'home' },
  };
  const PAD = 48;

  /** Quadratic track between two nodes, bowed slightly, plus the point at
   *  ``t`` along it where its value plate goes. */
  function edgePath(a, b, t) {
    const dx = b.x - a.x, dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len, uy = dy / len;
    const p1 = { x: a.x + ux * PAD, y: a.y + uy * PAD };
    const p2 = { x: b.x - ux * PAD, y: b.y - uy * PAD };
    const bow = len * 0.14;
    const c = { x: (p1.x + p2.x) / 2 + uy * bow, y: (p1.y + p2.y) / 2 - ux * bow };
    return {
      d: 'M' + p1.x.toFixed(1) + ',' + p1.y.toFixed(1) + ' Q' + c.x.toFixed(1) + ',' + c.y.toFixed(1)
        + ' ' + p2.x.toFixed(1) + ',' + p2.y.toFixed(1),
      mx: (1 - t) * (1 - t) * p1.x + 2 * t * (1 - t) * c.x + t * t * p2.x,
      my: (1 - t) * (1 - t) * p1.y + 2 * t * (1 - t) * c.y + t * t * p2.y,
    };
  }

  App.flowDiagram = function (container) {
    container.classList.add('iso');
    const viewport = div('iso__viewport', container);
    const world = div('iso__world', viewport);
    world.style.setProperty('--size', SIZE + 'px');
    div('iso__floor', world);
    div('iso__rings', world);
    const linkHost = div('iso__links', world);
    const nodeHost = div('iso__nodes', world);
    const plateHost = div('iso__nodes', world);

    // The models are built once; each render only updates their state.
    const nodes = {};
    Object.keys(NODES).forEach(key => {
      const nd = NODES[key];
      const node = div('iso__node iso__node--' + key, nodeHost);
      div('iso__halo', node);
      const model = div('iso__model', node);
      const top = MODELS[key](model);
      node.style.cssText = '--x:' + nd.x + 'px;--y:' + nd.y + 'px;--h:' + top + 'px;--c:' + nd.color;
      nodes[key] = { node, label: div('iso__label', node) };
    });

    // Fit the fixed-size world to the card at any width.
    const fit = () => {
      const w = container.clientWidth;
      viewport.style.setProperty('--fit', Math.min(1.45, Math.max(0.5, w / (w < 600 ? 450 : 500))).toFixed(3));
    };
    new ResizeObserver(fit).observe(container);
    fit();

    // Lean the scene toward the pointer, on top of its slow idle sway.
    if (!(App.fx && App.fx.reduced())) {
      container.addEventListener('pointermove', e => {
        const r = container.getBoundingClientRect();
        world.style.setProperty('--lean-x', (((e.clientY - r.top) / r.height - 0.5) * -10).toFixed(2) + 'deg');
        world.style.setProperty('--lean-z', (((e.clientX - r.left) / r.width - 0.5) * 14).toFixed(2) + 'deg');
      });
      container.addEventListener('pointerleave', () => {
        world.style.setProperty('--lean-x', '0deg');
        world.style.setProperty('--lean-z', '0deg');
      });
    }

    function render(data) {
      linkHost.innerHTML = '';
      plateHost.innerHTML = '';

      if (!data) {
        Object.values(nodes).forEach(n => { n.node.classList.remove('is-active'); n.label.innerHTML = ''; });
        div('iso__empty', plateHost, 'No data yet.');
        container.setAttribute('aria-label', 'No energy-flow data yet.');
        return;
      }

      const pv = data.pv_power || 0;
      const load = data.load_power || 0;
      const gridImport = data.grid_import || 0;
      const gridExport = data.grid_export || 0;
      const charge = data.battery_charge || 0;
      const discharge = data.battery_discharge || 0;

      // edge: [from, to, watts, colour key]
      const edges = [
        ['pv', 'home', Math.max(0, Math.min(pv, load)), 'pv'],
        ['pv', 'grid', gridExport, 'pv'],
        ['pv', 'battery', charge, 'battery'],
        ['grid', 'home', gridImport, 'grid'],
        ['battery', 'home', discharge, 'battery'],
      ];

      // --- tracks on the floor ------------------------------------------
      const svg = el('svg', { viewBox: '0 0 ' + SIZE + ' ' + SIZE, class: 'iso__svg' }, linkHost);
      const defs = el('defs', {}, svg);
      Object.keys(NODES).forEach(k => {
        const m = el('marker', {
          id: 'iso-arrow-' + k, viewBox: '0 0 10 10', refX: 7, refY: 5,
          markerWidth: 9, markerHeight: 9, markerUnits: 'userSpaceOnUse', orient: 'auto',
        }, defs);
        el('path', { d: 'M0,1 L9,5 L0,9 z', fill: NODES[k].color }, m);
      });
      const glow = el('filter', { id: 'iso-glow', x: '-50%', y: '-50%', width: '200%', height: '200%' }, defs);
      el('feGaussianBlur', { stdDeviation: 3 }, glow);

      const plates = [];
      edges.forEach(([from, to, watts, key], index) => {
        if (watts < 20) return;   // below this the line is noise, not a flow
        // The battery sits close to the panels and its tall label would cover a
        // centred plate, so plates on its tracks slide toward the other end.
        const t = to === 'battery' ? 0.25 : from === 'battery' ? 0.75 : 0.5;
        const path = edgePath(NODES[from], NODES[to], t);
        const color = NODES[key].color;
        const width = Math.max(2.5, Math.min(7, 2.5 + Math.log10(watts / 20 + 1) * 2.2));
        // Bigger flows stream faster and carry more packets.
        const dur = Math.max(0.9, 2.8 - Math.log10(watts / 100 + 1) * 1.1);
        const packets = Math.max(2, Math.min(6, Math.round(Math.log10(watts / 50 + 1) * 3)));

        const g = el('g', { class: 'iso__edge', style: 'color:' + color }, svg);
        const id = 'iso-path-' + index;
        el('path', { id, d: path.d, class: 'iso__track', 'stroke-width': width + 6 }, g);
        el('path', { d: path.d, class: 'iso__beam', 'stroke-width': width, filter: 'url(#iso-glow)' }, g);
        el('path', {
          d: path.d, class: 'iso__dash', 'stroke-width': width * 0.55,
          style: '--flow-dur:' + (dur * 0.6).toFixed(2) + 's',
          'marker-end': 'url(#iso-arrow-' + key + ')',
        }, g);
        if (!(App.fx && App.fx.reduced())) {
          for (let i = 0; i < packets; i++) {
            const dot = el('circle', { r: Math.min(5, width * 0.7), class: 'iso__packet' }, g);
            const motion = el('animateMotion', {
              dur: dur.toFixed(2) + 's', repeatCount: 'indefinite',
              begin: (-(dur / packets) * i).toFixed(2) + 's', rotate: 'auto',
            }, dot);
            el('mpath', { href: '#' + id }, motion);
          }
        }
        plates.push({ x: path.mx, y: path.my, text: App.fmt.powerText(watts), color });
      });

      // --- value plates (billboards over the track midpoints) --------------
      plates.forEach(p => {
        const plate = div('iso__plate', plateHost, App.escape(p.text));
        plate.style.cssText = '--x:' + p.x.toFixed(1) + 'px;--y:' + p.y.toFixed(1) + 'px;--c:' + p.color;
      });

      // --- nodes ------------------------------------------------------------
      const values = {
        pv: pv,
        grid: Math.max(gridExport, gridImport),
        battery: Math.max(charge, discharge),
        home: load,
      };
      const subs = {
        pv: pv >= 20 ? 'Producing' : 'Idle',
        grid: gridExport > gridImport ? 'Exporting' : gridImport > 0 ? 'Importing' : 'Idle',
        battery: charge > discharge ? 'Charging' : discharge > 0 ? 'Discharging' : 'Idle',
        home: 'Consuming',
      };
      const soc = data.soc != null ? Math.max(0, Math.min(100, data.soc)) : null;
      const scale = Math.max(4000, ...Object.values(values));

      Object.keys(NODES).forEach(key => {
        const nd = NODES[key];
        const { node, label } = nodes[key];
        node.classList.toggle('is-active', values[key] >= 20);
        // 0–1 strength that drives glow intensity, window light and so on.
        node.style.setProperty('--power', Math.min(1, Math.sqrt(values[key] / scale)).toFixed(3));
        if (key === 'battery') {
          node.style.setProperty('--soc', ((soc ?? 0) / 100).toFixed(3));
          node.dataset.flow = charge > discharge ? 'charge' : discharge > 0 ? 'discharge' : 'idle';
          node.dataset.level = soc == null ? '' : soc < 15 ? 'critical' : soc < 30 ? 'low' : 'ok';
        }
        if (key === 'grid') node.dataset.flow = gridExport > gridImport ? 'export' : gridImport > 0 ? 'import' : 'idle';

        let extra = subs[key];
        if (key === 'battery' && soc != null) extra = Math.round(soc) + '% · ' + extra;
        label.innerHTML = '<span class="iso__icon">' + App.icon(nd.icon) + '</span>'
          + '<span class="iso__name">' + nd.label + '</span>'
          + '<strong class="iso__value">' + App.fmt.powerHtml(values[key]) + '</strong>'
          + '<span class="iso__sub">' + App.escape(extra) + '</span>';
      });

      container.setAttribute('aria-label', 'Energy flow: solar ' + App.fmt.powerText(pv) + ', home ' + App.fmt.powerText(load)
        + ', grid ' + subs.grid.toLowerCase() + ' ' + App.fmt.powerText(values.grid)
        + ', battery ' + subs.battery.toLowerCase() + (soc != null ? ' at ' + Math.round(soc) + '%' : '') + '.');
    }

    container.setAttribute('role', 'img');
    return { render: render };
  };
})();
