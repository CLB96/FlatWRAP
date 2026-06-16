// pattern2d.js - render del patrón aplanado en canvas 2D (Fases 4/5)
const Pattern2D = {
  lastPatches: null,   // para re-render al togglear el heatmap

  show(patches) {
    this.lastPatches = patches;
    document.getElementById('pattern2d').classList.remove('hidden');
    this.render();
  },

  render() {
    const patches = this.lastPatches;
    if (!patches) return;
    const heatmap = document.getElementById('chkHeatmap').checked;
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
      if (heatmap) {
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
      const metric = (p.tension_pct != null)
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
    if (heatmap) this._drawLegend(ctx, canvas);
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
