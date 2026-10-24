/* Visual effects shared by Solar Monitor and IoT Control: the WebGL aurora
 * behind every page, pointer-driven 3D tilt, and a lift on values that change.
 * Keep both copies identical.
 *
 * Purely decorative. Nothing here is needed to read or operate the UI, and all
 * of it switches off for prefers-reduced-motion or without WebGL2.
 */
(function () {
  'use strict';

  const App = window.App;
  const fx = App.fx = {};
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const finePointer = matchMedia('(hover: hover) and (pointer: fine)');

  // ------------------------------------------------------------ aurora (WebGL2)
  const VERT = `#version 300 es
    in vec2 p; out vec2 uv;
    void main() { uv = p * 0.5 + 0.5; gl_Position = vec4(p, 0.0, 1.0); }`;

  // Layered value noise, domain-warped into slow ribbons of light.
  const FRAG = `#version 300 es
    precision highp float;
    in vec2 uv; out vec4 o;
    uniform float t; uniform vec2 res; uniform vec2 ptr;
    uniform vec3 c0; uniform vec3 c1; uniform vec3 c2; uniform vec3 bg; uniform float gain;
    float h(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
    float n(vec2 p) {
      vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
      return mix(mix(h(i), h(i + vec2(1, 0)), f.x), mix(h(i + vec2(0, 1)), h(i + vec2(1, 1)), f.x), f.y);
    }
    float fbm(vec2 p) { float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++) { v += a * n(p); p *= 2.02; a *= 0.5; } return v; }
    void main() {
      vec2 p = uv * vec2(res.x / res.y, 1.0) * 1.6;
      p += (ptr - 0.5) * 0.12;
      vec2 q = vec2(fbm(p + t * 0.03), fbm(p + vec2(5.2, 1.3) - t * 0.025));
      float f = fbm(p + 2.2 * q + vec2(0.0, t * 0.02));
      float band1 = smoothstep(0.25, 0.0, abs(uv.y - 0.78 + 0.22 * (f - 0.5) + 0.06 * sin(uv.x * 3.0 + t * 0.12)));
      float band2 = smoothstep(0.30, 0.0, abs(uv.y - 0.25 + 0.25 * (q.x - 0.5)));
      vec3 col = bg;
      col = mix(col, c0, band1 * 0.55 * gain * f * 1.6);
      col = mix(col, c1, band2 * 0.38 * gain * q.y * 1.6);
      col = mix(col, c2, smoothstep(0.55, 1.0, f) * 0.22 * gain);
      col += (h(uv * res + t) - 0.5) * 0.012;      // dither
      o = vec4(col, 1.0);
    }`;

  function cssColor(name) {
    const probe = document.createElement('i');
    probe.style.color = 'var(' + name + ')';
    probe.style.display = 'none';
    document.body.appendChild(probe);
    const m = getComputedStyle(probe).color.match(/[\d.]+/g) || [0, 0, 0];
    probe.remove();
    return [m[0] / 255, m[1] / 255, m[2] / 255];
  }

  function aurora() {
    if (reduced.matches) return;
    const canvas = document.createElement('canvas');
    canvas.className = 'fx-canvas';
    canvas.setAttribute('aria-hidden', 'true');
    const gl = canvas.getContext('webgl2', { antialias: false, alpha: false, powerPreference: 'low-power' });
    if (!gl) return;

    const compile = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src);
      gl.compileShader(s);
      return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null;
    };
    const vs = compile(gl.VERTEX_SHADER, VERT), fs = compile(gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return;
    const prog = gl.createProgram();
    gl.attachShader(prog, vs); gl.attachShader(prog, fs); gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) return;
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);

    const u = name => gl.getUniformLocation(prog, name);
    const U = { t: u('t'), res: u('res'), ptr: u('ptr'), c0: u('c0'), c1: u('c1'), c2: u('c2'), bg: u('bg'), gain: u('gain') };

    function palette() {
      const dark = getComputedStyle(document.documentElement).colorScheme.includes('dark');
      gl.uniform3fv(U.c0, cssColor('--accent'));
      gl.uniform3fv(U.c1, cssColor('--accent-2'));
      gl.uniform3fv(U.c2, cssColor('--brand-2'));
      gl.uniform3fv(U.bg, cssColor('--bg'));
      gl.uniform1f(U.gain, dark ? 1.0 : 0.55);
    }

    // Rendered small and scaled up: the image is all soft gradients anyway.
    const SCALE = 0.33;
    function resize() {
      canvas.width = Math.max(64, Math.round(innerWidth * SCALE));
      canvas.height = Math.max(64, Math.round(innerHeight * SCALE));
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.uniform2f(U.res, canvas.width, canvas.height);
    }

    const ptr = { x: 0.5, y: 0.5, tx: 0.5, ty: 0.5 };
    addEventListener('pointermove', e => { ptr.tx = e.clientX / innerWidth; ptr.ty = 1 - e.clientY / innerHeight; }, { passive: true });

    document.body.prepend(canvas);
    resize();
    palette();
    addEventListener('resize', resize);
    document.addEventListener('themechange', () => requestAnimationFrame(palette));
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', palette);

    const t0 = performance.now();
    let last = 0;
    function frame(now) {
      requestAnimationFrame(frame);
      if (document.hidden || now - last < 33) return;   // ~30 fps is plenty
      last = now;
      ptr.x += (ptr.tx - ptr.x) * 0.04;
      ptr.y += (ptr.ty - ptr.y) * 0.04;
      gl.uniform1f(U.t, (now - t0) / 1000);
      gl.uniform2f(U.ptr, ptr.x, ptr.y);
      gl.drawArrays(gl.TRIANGLES, 0, 3);
      if (!canvas.classList.contains('is-ready')) canvas.classList.add('is-ready');
    }
    requestAnimationFrame(frame);
  }

  // ------------------------------------------------------------------- 3D tilt
  /** Elements with .tilt lean toward the pointer; max angle via data-tilt. */
  function tilt() {
    if (reduced.matches || !finePointer.matches) return;
    let active = null;

    function release(el) {
      el.classList.remove('is-tilting');
      el.style.setProperty('--rx', '0deg');
      el.style.setProperty('--ry', '0deg');
    }

    document.addEventListener('pointermove', e => {
      const el = e.target.closest && e.target.closest('.tilt');
      if (active && active !== el) { release(active); active = null; }
      if (!el) return;
      active = el;
      const r = el.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
      const max = Number(el.dataset.tilt) || 6;
      el.classList.add('is-tilting');
      el.style.setProperty('--ry', ((x - 0.5) * 2 * max).toFixed(2) + 'deg');
      el.style.setProperty('--rx', ((0.5 - y) * 2 * max).toFixed(2) + 'deg');
      el.style.setProperty('--mx', (x * 100).toFixed(1) + '%');
      el.style.setProperty('--my', (y * 100).toFixed(1) + '%');
    }, { passive: true });

    document.documentElement.addEventListener('pointerleave', () => { if (active) { release(active); active = null; } });
  }

  // ------------------------------------------------------- changing numbers
  /** Briefly animate an element whose text just changed. */
  fx.pop = function (el) {
    if (!el || reduced.matches) return;
    el.classList.remove('is-updated');
    void el.offsetWidth;  // restart the animation
    el.classList.add('is-updated');
  };

  function watchValues() {
    const seen = new WeakMap();
    const observer = new MutationObserver(records => {
      const touched = new Set();
      records.forEach(r => {
        const el = (r.target.nodeType === 1 ? r.target : r.target.parentElement);
        const host = el && el.closest('[data-live], .kpi__value');
        if (host) touched.add(host);
      });
      touched.forEach(host => {
        const text = host.textContent;
        const before = seen.get(host);
        seen.set(host, text);
        if (before !== undefined && before !== text && !host.querySelector('.skeleton')) fx.pop(host);
      });
    });
    document.querySelectorAll('[data-live], .kpi__value').forEach(el => seen.set(el, el.textContent));
    observer.observe(document.body, { subtree: true, childList: true, characterData: true });
  }

  /** Count a number up to its new value (used by the hero readouts). */
  fx.countTo = function (el, value, format, ms) {
    if (!el) return;
    const to = Number(value);
    const from = Number(el.dataset.value);
    el.dataset.value = String(to);
    if (!Number.isFinite(to) || !Number.isFinite(from) || reduced.matches || from === to) {
      el.innerHTML = format(to);
      return;
    }
    const start = performance.now(), dur = ms || 900;
    cancelAnimationFrame(el._raf);
    const step = now => {
      const k = Math.min(1, (now - start) / dur);
      const e = 1 - Math.pow(1 - k, 4);
      el.innerHTML = format(from + (to - from) * e);
      if (k < 1) el._raf = requestAnimationFrame(step);
    };
    el._raf = requestAnimationFrame(step);
  };

  fx.reduced = () => reduced.matches;

  function init() {
    aurora();
    tilt();
    watchValues();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
