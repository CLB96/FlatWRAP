// pattern2d.js - render del patrón aplanado en canvas 2D (Fases 4/5)
const Pattern2D = {
  lastPatches: null,   // para re-render al togglear el heatmap
  // Modo tolerancia: pinta verde/rojo según los límites de la TDS del material.
  tolerance: { on: false, warp: 15, weft: 15, comp: 15 },

  show(patches) {
    this.lastPatches = patches;
    document.getElementById('pattern2d').classList.remove('hidden');
    this.render();
  },

  render() {
    const patches = this.lastPatches;
    if (!patches) return;
    const tol = this.tolerance;
    const tolMode = tol.on && patches[0] && patches[0].s1;
    const heatmap = !tolMode && document.getElementById('chkHeatmap').checked;
    const canvas = document.getElementById('patternCanvas');
    canvas.width = canvas.clientWidth * devicePixelRatio;
    canvas.height = canvas.clientHeight * devicePixelRatio;
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Bounding box global (los parches ya vienen acomodados en fila)
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const p of patches) for (const [x, y] of p.uv) {
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    }
    const pad = 60 * devicePixelRatio;
    const s = Math.min(
      (canvas.width - pad * 2) / (maxX - minX || 1),
      (canvas.height - pad * 2) / (maxY - minY || 1)
    );
    const tx = x => pad + (x - minX) * s;
    const ty = y => canvas.height - pad - (y - minY) * s;

    for (const p of patches) {
      if (tolMode) {
        // Modo TOLERANCIA: verde = dentro del material (TDS), rojo = lo excede.
        let okCount = 0;
        for (let f = 0; f < p.faces.length; f++) {
          const [a, b, c] = p.faces[f];
          const pass = this._facePass(p.s1[f], p.s2[f], tol);
          if (pass) okCount++;
          ctx.fillStyle = pass ? 'rgba(46,204,113,0.85)' : 'rgba(231,76,60,0.9)';
          ctx.beginPath();
          ctx.moveTo(tx(p.uv[a][0]), ty(p.uv[a][1]));
          ctx.lineTo(tx(p.uv[b][0]), ty(p.uv[b][1]));
          ctx.lineTo(tx(p.uv[c][0]), ty(p.uv[c][1]));
          ctx.closePath();
          ctx.fill();
        }
        p._okPct = Math.round(100 * okCount / p.faces.length);
      } else if (heatmap) {
        // Mapa de tensión por triángulo: azul = compresión, rojo = tensión
        const strain = p.strain || p.distortion.map(d => (d - 1) * 100);
        for (let f = 0; f < p.faces.length; f++) {
          const [a, b, c] = p.faces[f];
          ctx.fillStyle = this._strainColor(strain[f]);
          ctx.beginPath();
          ctx.moveTo(tx(p.uv[a][0]), ty(p.uv[a][1]));
          ctx.lineTo(tx(p.uv[b][0]), ty(p.uv[b][1]));
          ctx.lineTo(tx(p.uv[c][0]), ty(p.uv[c][1]));
          ctx.closePath();
          ctx.fill();
        }
      } else {
        ctx.strokeStyle = 'rgba(88,166,255,0.45)';
        ctx.lineWidth = 1;
        for (const [a, b, c] of p.faces) {
          ctx.beginPath();
          ctx.moveTo(tx(p.uv[a][0]), ty(p.uv[a][1]));
          ctx.lineTo(tx(p.uv[b][0]), ty(p.uv[b][1]));
          ctx.lineTo(tx(p.uv[c][0]), ty(p.uv[c][1]));
          ctx.closePath();
          ctx.stroke();
        }
      }

      // Etiqueta compacta sobre cada patrón (evita solape cuando hay varios)
      const cx = p.uv.reduce((acc, q) => acc + q[0], 0) / p.uv.length;
      const yTop = Math.max(...p.uv.map(q => q[1]));
      const metric = tolMode
        ? `${p._okPct}% dentro de tolerancia`
        : (p.tension_pct != null)
          ? `+${p.tension_pct}% / −${p.compression_pct}%`
          : `${p.mean_distortion_pct}%`;
      ctx.fillStyle = '#e6edf3';
      ctx.font = `${12 * devicePixelRatio}px Segoe UI`;
      ctx.textAlign = 'center';
      ctx.fillText(
        `P${p.patch_id} · ${p.width_mm}×${p.height_mm} mm · ${metric}`,
        tx(cx), ty(yTop) - 10 * devicePixelRatio
      );
    }

    this._drawScaleBar(ctx, canvas, s);
    if (tolMode) this._drawTolLegend(ctx, canvas);
    else if (heatmap) this._drawLegend(ctx, canvas);
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
    const x = canvas.width - 220 * devicePixelRatio;
    const y = canvas.height - 30 * devicePixelRatio;
    const sq = 12 * devicePixelRatio;
    ctx.font = `${11 * devicePixelRatio}px Segoe UI`;
    ctx.textAlign = 'left';
    ctx.fillStyle = 'rgba(46,204,113,0.9)';
    ctx.fillRect(x, y, sq, sq);
    ctx.fillStyle = '#8b949e';
    ctx.fillText('dentro del material', x + sq + 6 * devicePixelRatio, y + sq);
    ctx.fillStyle = 'rgba(231,76,60,0.95)';
    ctx.fillRect(x + 130 * devicePixelRatio, y, sq, sq);
    ctx.fillStyle = '#8b949e';
    ctx.fillText('excede', x + 130 * devicePixelRatio + sq + 6 * devicePixelRatio, y + sq);
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

  _drawScaleBar(ctx, canvas, s) {
    // Barra de 100 mm reales para verificar escala a ojo
    const barMm = 100, barPx = barMm * s;
    const x = 30 * devicePixelRatio, y = canvas.height - 24 * devicePixelRatio;
    ctx.strokeStyle = '#e6edf3'; ctx.lineWidth = 2 * devicePixelRatio;
    ctx.beginPath();
    ctx.moveTo(x, y); ctx.lineTo(x + barPx, y);
    ctx.moveTo(x, y - 5 * devicePixelRatio); ctx.lineTo(x, y + 5 * devicePixelRatio);
    ctx.moveTo(x + barPx, y - 5 * devicePixelRatio); ctx.lineTo(x + barPx, y + 5 * devicePixelRatio);
    ctx.stroke();
    ctx.fillStyle = '#8b949e';
    ctx.font = `${11 * devicePixelRatio}px Segoe UI`;
    ctx.textAlign = 'center';
    ctx.fillText(`${barMm} mm`, x + barPx / 2, y - 8 * devicePixelRatio);
  },

  _drawLegend(ctx, canvas) {
    const w = 160 * devicePixelRatio, h = 10 * devicePixelRatio;
    const x = canvas.width - w - 30 * devicePixelRatio;
    const y = canvas.height - 30 * devicePixelRatio;
    const grad = ctx.createLinearGradient(x, 0, x + w, 0);
    grad.addColorStop(0.0, 'rgb(0,0,230)');     // compresión
    grad.addColorStop(0.5, 'rgb(210,210,210)'); // neutro
    grad.addColorStop(1.0, 'rgb(230,0,0)');     // tensión
    ctx.fillStyle = grad;
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = '#8b949e';
    ctx.font = `${11 * devicePixelRatio}px Segoe UI`;
    ctx.textAlign = 'left';
    ctx.fillText('−10% compresión', x, y - 4 * devicePixelRatio);
    ctx.textAlign = 'right';
    ctx.fillText('tensión +10%', x + w, y - 4 * devicePixelRatio);
  },

  hide() {
    document.getElementById('pattern2d').classList.add('hidden');
  },
};
