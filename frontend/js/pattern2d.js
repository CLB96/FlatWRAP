// pattern2d.js - vista 2D del patrón aplanado (Fases 4/5)
//
// - Zoom con la rueda (anclado al cursor), paneo arrastrando, doble clic = encuadrar.
// - La geometría se cachea en Path2D agrupada por color: en cada cuadro de
//   zoom/paneo solo se aplica una transformación (fluido incluso con >100k
//   triángulos). Las caches se reconstruyen solo si cambian los datos o el modo.
// - Contorno de corte opcional (margen, muescas, costuras) con la MISMA
//   geometría que el DXF: llega de /api/export/preview (ver setCut).
const Pattern2D = {
  lastPatches: null,   // parches aplanados (uv/faces/strain/s1/s2...)
  cut: null,           // geometría de corte (vista previa) o null
  cutOn: false,        // ¿mostrar el contorno de corte?
  // Modo tolerancia: pinta verde/rojo según los límites de la TDS del material.
  tolerance: { on: false, warp: 15, weft: 15, comp: 15 },

  // Transformación modelo(mm) -> canvas(px de dispositivo):  X = tx + s*x ; Y = ty - s*y
  view: { s: 1, tx: 0, ty: 0 },
  MIN_ZOOM: 0.2,       // relativo al encuadre
  MAX_ZOOM: 400,

  _fitScale: 1,
  _dirtyFit: true,     // hay que encuadrar en el próximo render
  _userMoved: false,   // el usuario ya hizo zoom/paneo (no re-encuadrar solo)
  _faceCache: null,
  _cutCache: null,
  BIG_FACES: 20000,    // desde aquí: bitmap en caché mientras se hace zoom/paneo
  _bm: null,           // OffscreenCanvas/canvas con el último dibujo completo
  _bmView: null,       // vista con la que se dibujó _bm
  _settle: 0,
  _drag: null,
  _raf: 0,
  _inited: false,

  // ------------------------------------------------------------ ciclo de vida
  init() {
    if (this._inited) return;
    this._inited = true;
    const cv = document.getElementById('patternCanvas');
    cv.addEventListener('wheel', (e) => this._onWheel(e), { passive: false });
    cv.addEventListener('pointerdown', (e) => this._onDown(e));
    cv.addEventListener('pointermove', (e) => this._onMove(e));
    cv.addEventListener('pointerup', (e) => this._onUp(e));
    cv.addEventListener('pointercancel', (e) => this._onUp(e));
    cv.addEventListener('pointerleave', () => this._setCoords(null));
    cv.addEventListener('dblclick', () => this.fit());
  },

  show(patches) {
    this.init();
    this.lastPatches = patches;
    this.cut = null;            // la geometría de corte anterior ya no aplica
    this._cutCache = null;
    this._faceCache = null;
    this._dirtyFit = true;
    this._userMoved = false;
    document.getElementById('pattern2d').classList.remove('hidden');
    this.render();
  },

  hide() {
    document.getElementById('pattern2d').classList.add('hidden');
    this._setCoords(null);
  },

  // Geometría de corte (de /api/export/preview). null = quitarla.
  setCut(cut) {
    this.cut = cut;
    this._cutCache = null;
    if (!this.lastPatches) return;
    // Con el margen el contorno crece: re-encuadrar solo si el usuario no movió la vista
    if (this.cutOn && !this._userMoved) this.fit();
    else this.draw();
  },

  setCutOn(on) {
    this.cutOn = !!on;
    if (!this.lastPatches) return;
    if (!this._userMoved && this.cut) this.fit();
    else this.draw();
  },

  // Redibuja (toggles, redimensionado). No altera la vista salvo el 1er encuadre.
  render() {
    if (!this.lastPatches) return;
    this._syncCanvasSize();
    if (this._dirtyFit) return this.fit();
    this.draw();
  },

  // ------------------------------------------------------------------ vista
  _syncCanvasSize() {
    const cv = document.getElementById('patternCanvas');
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.round(cv.clientWidth * dpr));
    const h = Math.max(1, Math.round(cv.clientHeight * dpr));
    if (cv.width === w && cv.height === h) return false;
    if (!this._dirtyFit) {       // conservar el centro de la vista al redimensionar
      this.view.tx += (w - cv.width) / 2;
      this.view.ty += (h - cv.height) / 2;
    }
    cv.width = w;
    cv.height = h;
    return true;
  },

  // Bounding box del contenido (patrones + contorno de corte si está visible)
  _bounds() {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    const add = (x, y) => {
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    };
    for (const p of this.lastPatches || []) {
      const uv = p.uv;
      for (let i = 0; i < uv.length; i++) add(uv[i][0], uv[i][1]);
    }
    if (this.cutOn && this.cut) {
      for (const p of this.cut.patches) {
        for (const ring of p.cut_rings) for (let i = 0; i < ring.length; i++) add(ring[i][0], ring[i][1]);
      }
    }
    if (!isFinite(minX)) { minX = minY = 0; maxX = maxY = 1; }
    return { minX, minY, maxX, maxY };
  },

  // Encuadra todo el contenido en el canvas
  fit() {
    if (!this.lastPatches) return;
    this._syncCanvasSize();
    const cv = document.getElementById('patternCanvas');
    const dpr = window.devicePixelRatio || 1;
    const b = this._bounds();
    const w = (b.maxX - b.minX) || 1, h = (b.maxY - b.minY) || 1;
    const pad = 60 * dpr;
    const s = Math.max(Math.min((cv.width - pad * 2) / w, (cv.height - pad * 2) / h), 1e-6);
    this._fitScale = s;
    this.view = {
      s,
      tx: (cv.width - w * s) / 2 - b.minX * s,
      ty: (cv.height + h * s) / 2 + b.minY * s,
    };
    this._dirtyFit = false;
    this._userMoved = false;
    this.draw();
  },

  _zoomAt(mx, my, k) {
    const v = this.view;
    const ns = Math.min(Math.max(v.s * k, this._fitScale * this.MIN_ZOOM), this._fitScale * this.MAX_ZOOM);
    const kk = ns / v.s;
    if (kk === 1) return;
    v.tx = mx - (mx - v.tx) * kk;   // el punto bajo el cursor no se mueve
    v.ty = my - (my - v.ty) * kk;
    v.s = ns;
    this._userMoved = true;
    this._schedule();
  },

  // Coalesce varios eventos en un solo dibujo por cuadro
  _schedule() {
    if (!this._raf) {
      this._raf = requestAnimationFrame(() => { this._raf = 0; this.draw(true); });
    }
    // Mallas grandes: al dejar de mover, redibujar nítido
    clearTimeout(this._settle);
    this._settle = setTimeout(() => this.draw(), 140);
  },

  // ------------------------------------------------------------ interacción
  _pointer(e) {
    const cv = document.getElementById('patternCanvas');
    const rect = cv.getBoundingClientRect();
    const dpr = cv.width / (rect.width || 1);
    return { x: (e.clientX - rect.left) * dpr, y: (e.clientY - rect.top) * dpr, dpr };
  },

  _onWheel(e) {
    if (!this.lastPatches) return;
    e.preventDefault();
    let dy = e.deltaY;
    if (e.deltaMode === 1) dy *= 16; else if (e.deltaMode === 2) dy *= 100;
    const p = this._pointer(e);
    this._zoomAt(p.x, p.y, Math.exp(-dy * 0.0015));
  },

  _onDown(e) {
    if (!this.lastPatches || (e.button !== 0 && e.button !== 1)) return;
    const cv = e.currentTarget;
    cv.setPointerCapture(e.pointerId);
    cv.classList.add('dragging');
    this._drag = { x: e.clientX, y: e.clientY, tx: this.view.tx, ty: this.view.ty };
    e.preventDefault();
  },

  _onMove(e) {
    const p = this._pointer(e);
    if (this._drag) {
      this.view.tx = this._drag.tx + (e.clientX - this._drag.x) * p.dpr;
      this.view.ty = this._drag.ty + (e.clientY - this._drag.y) * p.dpr;
      this._userMoved = true;
      this._schedule();
    }
    this._setCoords(this.lastPatches ? {
      x: (p.x - this.view.tx) / this.view.s,
      y: (this.view.ty - p.y) / this.view.s,
    } : null);
  },

  _onUp(e) {
    if (!this._drag) return;
    this._drag = null;
    e.currentTarget.classList.remove('dragging');
  },

  _setCoords(pt) {
    const el = document.getElementById('coords2d');
    if (!el) return;
    el.textContent = pt ? `x ${pt.x.toFixed(1)} · y ${pt.y.toFixed(1)} mm` : '';
  },

  // ------------------------------------------------------------------ caches
  // Modo de relleno vigente: 'tol' (verde/rojo), 'heat' (mapa de tensión) o 'wire'
  _mode() {
    const tol = this.tolerance;
    if (tol.on && this.lastPatches[0] && this.lastPatches[0].s1) {
      return `tol:${tol.warp}:${tol.weft}:${tol.comp}`;
    }
    return document.getElementById('chkHeatmap').checked ? 'heat' : 'wire';
  },

  _ensureFaces() {
    const mode = this._mode();
    if (this._faceCache && this._faceCache.mode === mode) return this._faceCache;
    const kind = mode.startsWith('tol') ? 'tol' : mode;
    const tol = this.tolerance;
    const OK = 'rgba(46,204,113,0.85)', BAD = 'rgba(231,76,60,0.9)';
    const groups = [];
    for (const p of this.lastPatches) {
      const uv = p.uv, faces = p.faces;
      const buckets = new Map();           // color -> Path2D (un solo fill por color)
      const wire = kind === 'wire' ? new Path2D() : null;
      const strain = kind === 'heat' ? (p.strain || p.distortion.map((d) => (d - 1) * 100)) : null;
      let okCount = 0;
      for (let f = 0; f < faces.length; f++) {
        const a = uv[faces[f][0]], b = uv[faces[f][1]], c = uv[faces[f][2]];
        let path = wire;
        if (kind === 'tol') {
          const pass = this._facePass(p.s1[f], p.s2[f], tol);
          if (pass) okCount++;
          const color = pass ? OK : BAD;
          path = buckets.get(color);
          if (!path) buckets.set(color, (path = new Path2D()));
        } else if (kind === 'heat') {
          // Cuantizado a 41 niveles (pasos de 0.5%): imperceptible, 1 fill por nivel
          const q = Math.round(Math.max(-1, Math.min(1, strain[f] / 10)) * 20);
          const color = this._strainColor(q * 0.5);
          path = buckets.get(color);
          if (!path) buckets.set(color, (path = new Path2D()));
        }
        // OJO: sin closePath() por triángulo; en Chromium es O(n²) (40k tri = 6 s).
        // lineTo al inicio cierra el triángulo igual (relleno y borde).
        path.moveTo(a[0], a[1]);
        path.lineTo(b[0], b[1]);
        path.lineTo(c[0], c[1]);
        path.lineTo(a[0], a[1]);
      }
      // Ancla de la etiqueta: centro en x y borde superior (sin Math.max(...arr),
      // que revienta la pila con parches de >100k vértices)
      let sx = 0, top = -Infinity;
      for (let i = 0; i < uv.length; i++) { sx += uv[i][0]; if (uv[i][1] > top) top = uv[i][1]; }
      const metric = kind === 'tol'
        ? `${Math.round(100 * okCount / faces.length)}% dentro de tolerancia`
        : (p.tension_pct != null)
          ? `+${p.tension_pct}% / −${p.compression_pct}%`
          : `${p.mean_distortion_pct}%`;
      groups.push({
        fills: [...buckets.entries()],
        wire,
        anchor: { x: sx / uv.length, y: top },
        text: `P${p.patch_id} · ${p.width_mm}×${p.height_mm} mm · ${metric}`,
      });
    }
    const nFaces = this.lastPatches.reduce((a, p) => a + p.faces.length, 0);
    this._faceCache = { mode, kind, groups, nFaces };
    return this._faceCache;
  },

  _ensureCut() {
    if (this._cutCache) return this._cutCache;
    const ringPath = (path, ring) => {
      for (let i = 0; i < ring.length; i++) {
        if (i === 0) path.moveTo(ring[i][0], ring[i][1]); else path.lineTo(ring[i][0], ring[i][1]);
      }
      path.closePath();
    };
    const cut = new Path2D(), seam = new Path2D(), notch = new Path2D();
    const labels = [];
    for (const p of this.cut.patches) {
      for (const r of p.cut_rings) ringPath(cut, r);
      for (const r of p.seam_rings) ringPath(seam, r);
      for (const n of p.notches) {
        notch.moveTo(n.start[0], n.start[1]);
        notch.lineTo(n.end[0], n.end[1]);
        // Misma posición de etiqueta que el DXF: 1 largo de muesca más allá del extremo
        labels.push({
          x: n.end[0] + (n.end[0] - n.start[0]),
          y: n.end[1] + (n.end[1] - n.start[1]),
          text: n.label,
        });
      }
    }
    this._cutCache = { cut, seam, notch, labels };
    return this._cutCache;
  },

  // ------------------------------------------------------------------ dibujo
  draw(fast = false) {
    const cv = document.getElementById('patternCanvas');
    const ctx = cv.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, cv.width, cv.height);
    if (!this.lastPatches) return;

    const fc = this._ensureFaces();
    const { s, tx, ty } = this.view;
    const showCut = this.cutOn && this.cut;
    const big = fc.nFaces > this.BIG_FACES;
    let cc = showCut ? this._ensureCut() : null;

    if (big && fast && this._bm && this._bmView && this._bm.width === cv.width && this._bm.height === cv.height) {
      // Interacción: reutilizar el último dibujo transformado (barato, ligeramente borroso)
      const v0 = this._bmView, k = s / v0.s;
      ctx.setTransform(k, 0, 0, k, tx - k * v0.tx, ty - k * v0.ty);
      ctx.drawImage(this._bm, 0, 0);
    } else {
      let target = ctx;
      if (big) {
        if (!this._bm || this._bm.width !== cv.width || this._bm.height !== cv.height) {
          this._bm = typeof OffscreenCanvas !== 'undefined'
            ? new OffscreenCanvas(cv.width, cv.height)
            : Object.assign(document.createElement('canvas'), { width: cv.width, height: cv.height });
        }
        target = this._bm.getContext('2d');
        target.setTransform(1, 0, 0, 1, 0, 0);
        target.clearRect(0, 0, cv.width, cv.height);
      }
      this._drawModel(target, fc, cc, s, tx, ty, dpr);
      if (big) {
        this._bmView = { s, tx, ty };
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.drawImage(this._bm, 0, 0);
      }
    }

    // --- espacio de pantalla (px): textos, escala y leyendas
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.setLineDash([]);
    ctx.textAlign = 'center';
    ctx.fillStyle = '#e6edf3';
    ctx.font = `${12 * dpr}px Segoe UI`;
    for (const g of fc.groups) {
      ctx.fillText(g.text, tx + s * g.anchor.x, ty - s * g.anchor.y - 10 * dpr);
    }
    if (cc && s / dpr >= 1.2) {       // etiquetas de muescas solo si hay espacio
      ctx.fillStyle = '#3fb950';
      ctx.font = `${10 * dpr}px Segoe UI`;
      for (const l of cc.labels) ctx.fillText(l.text, tx + s * l.x, ty - s * l.y);
    }

    this._drawScaleBar(ctx, cv, s, dpr);
    if (fc.kind === 'tol') this._drawTolLegend(ctx, cv);
    else if (fc.kind === 'heat') this._drawLegend(ctx, cv);
    if (showCut) this._drawCutLegend(ctx, cv, dpr);
  },

  // Dibuja en espacio del modelo (mm): rellenos + contorno de corte
  _drawModel(ctx, fc, cc, s, tx, ty, dpr) {
    ctx.setTransform(s, 0, 0, -s, tx, ty);
    ctx.lineJoin = 'round';
    for (const g of fc.groups) {
      if (fc.kind === 'wire') {
        ctx.strokeStyle = 'rgba(88,166,255,0.45)';
        ctx.lineWidth = dpr / s;
        ctx.stroke(g.wire);
      } else {
        for (const [color, path] of g.fills) { ctx.fillStyle = color; ctx.fill(path); }
      }
    }
    if (cc) {
      ctx.setLineDash([]);
      ctx.strokeStyle = '#ffd33d';                    // CORTE (con margen)
      ctx.lineWidth = 1.6 * dpr / s;
      ctx.stroke(cc.cut);
      ctx.setLineDash([7 * dpr / s, 4 * dpr / s]);    // COSTURA neta
      ctx.strokeStyle = '#ff7b72';
      ctx.lineWidth = 1.2 * dpr / s;
      ctx.stroke(cc.seam);
      ctx.setLineDash([]);
      ctx.strokeStyle = '#3fb950';                    // MUESCAS
      ctx.lineWidth = 1.8 * dpr / s;
      ctx.stroke(cc.notch);
    }
  },

  // ¿La cara cabe en el material? Asume orientación óptima: el estiramiento
  // mayor (σ1) va a la dirección que más tolera (max(warp,weft)); el otro (σ2)
  // a la otra; la compresión contra su propio límite.
  _facePass(s1, s2, tol) {
    const hi = Math.max(tol.warp, tol.weft);
    const lo = Math.min(tol.warp, tol.weft);
    const tens1 = (s1 - 1) * 100, tens2 = (s2 - 1) * 100;  // σ1≥σ2
    const okTens = Math.max(0, tens1) <= hi && Math.max(0, tens2) <= lo;
    const okComp = Math.max(0, -tens2) <= tol.comp && Math.max(0, -tens1) <= tol.comp;
    return okTens && okComp;
  },

  _drawTolLegend(ctx, canvas) {
    const dpr = window.devicePixelRatio || 1;
    const x = canvas.width - 220 * dpr;
    const y = canvas.height - 30 * dpr;
    const sq = 12 * dpr;
    ctx.font = `${11 * dpr}px Segoe UI`;
    ctx.textAlign = 'left';
    ctx.fillStyle = 'rgba(46,204,113,0.9)';
    ctx.fillRect(x, y, sq, sq);
    ctx.fillStyle = '#8b949e';
    ctx.fillText('dentro del material', x + sq + 6 * dpr, y + sq);
    ctx.fillStyle = 'rgba(231,76,60,0.95)';
    ctx.fillRect(x + 130 * dpr, y, sq, sq);
    ctx.fillStyle = '#8b949e';
    ctx.fillText('excede', x + 130 * dpr + sq + 6 * dpr, y + sq);
  },

  // Mapa divergente: azul = compresión, gris = neutro, rojo = tensión.
  // El valor es % de deformación con signo; satura a ±10%.
  _strainColor(pct) {
    const t = Math.max(-1, Math.min(1, pct / 10));
    const r = t > 0 ? 230 : Math.round(230 * (1 + t));
    const b = t < 0 ? 230 : Math.round(230 * (1 - t));
    const g = Math.round(210 * (1 - Math.abs(t)));
    return `rgba(${r},${g},${b},0.85)`;
  },

  // Barra de escala con longitud "redonda" que se adapta al zoom (80-200 px aprox.)
  _drawScaleBar(ctx, canvas, s, dpr) {
    const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000];
    const target = (140 * dpr) / s;          // mm que ocupan ~140 px de pantalla
    let barMm = steps[0];
    for (const st of steps) if (st <= target) barMm = st;
    const barPx = barMm * s;
    const x = 30 * dpr, y = canvas.height - 24 * dpr;
    ctx.strokeStyle = '#e6edf3'; ctx.lineWidth = 2 * dpr;
    ctx.beginPath();
    ctx.moveTo(x, y); ctx.lineTo(x + barPx, y);
    ctx.moveTo(x, y - 5 * dpr); ctx.lineTo(x, y + 5 * dpr);
    ctx.moveTo(x + barPx, y - 5 * dpr); ctx.lineTo(x + barPx, y + 5 * dpr);
    ctx.stroke();
    ctx.fillStyle = '#8b949e';
    ctx.font = `${11 * dpr}px Segoe UI`;
    ctx.textAlign = 'center';
    ctx.fillText(`${barMm} mm`, x + barPx / 2, y - 8 * dpr);
  },

  _drawLegend(ctx, canvas) {
    const dpr = window.devicePixelRatio || 1;
    const w = 240 * dpr, h = 10 * dpr;
    const x = canvas.width - w - 30 * dpr;
    const y = canvas.height - 30 * dpr;
    const grad = ctx.createLinearGradient(x, 0, x + w, 0);
    grad.addColorStop(0.0, 'rgb(0,0,230)');     // compresión
    grad.addColorStop(0.5, 'rgb(210,210,210)'); // neutro
    grad.addColorStop(1.0, 'rgb(230,0,0)');     // tensión
    ctx.fillStyle = grad;
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = '#8b949e';
    ctx.font = `${11 * dpr}px Segoe UI`;
    ctx.textAlign = 'left';
    ctx.fillText('−10% compresión', x, y - 4 * dpr);
    ctx.textAlign = 'right';
    ctx.fillText('tensión +10%', x + w, y - 4 * dpr);
  },

  // Leyenda del contorno de corte (arriba de la escala, centrada)
  _drawCutLegend(ctx, canvas, dpr) {
    const allow = this.cut && this.cut.seam_allowance_mm != null ? this.cut.seam_allowance_mm : '';
    const items = [
      ['#ffd33d', [], `corte (+${allow} mm)`],
      ['#ff7b72', [6 * dpr, 3 * dpr], 'costura'],
      ['#3fb950', [], 'muescas'],
    ];
    ctx.font = `${11 * dpr}px Segoe UI`;
    ctx.textAlign = 'left';
    let x = canvas.width / 2 - 120 * dpr;
    const y = canvas.height - 40 * dpr;
    for (const [color, dash, label] of items) {
      ctx.setLineDash(dash);
      ctx.strokeStyle = color;
      ctx.lineWidth = 2 * dpr;
      ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x + 18 * dpr, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#8b949e';
      ctx.fillText(label, x + 24 * dpr, y + 4 * dpr);
      x += (30 + label.length * 6.2) * dpr;
    }
  },
};
