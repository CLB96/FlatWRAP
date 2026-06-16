// selection.js - selección de la CARA A (superficie show de la pieza sólida).
//
// La pieza es un plástico con espesor: hay que aislar la cara A antes de
// generar patrones. Dos herramientas:
//   - "Seleccionar cara A": clic con relleno por ángulo (toma toda la
//     superficie suave hasta los bordes vivos). Orbitar sigue disponible.
//   - Pincel + / −: arrastrar con botón izquierdo para afinar a mano
//     (en modo pincel el botón izquierdo pinta; el derecho sigue paneando).
const Selection = {
  faces: new Set(),        // ids de cara seleccionados (la cara A)
  mode: null,              // 'flood' | 'add' | 'remove' | null
  angleDeg: 35,
  sideDeg: 85,             // no cruzar al reverso (orientación máx vs el clic)
  brushRadius: 6,          // radio del pincel en mm (ajustable)
  brushShape: 'circle',    // 'circle' (superficie) | 'sphere' (volumen)
  _painting: false,
  _rebuildQueued: false,

  // Para el pincel círculo: la cara debe mirar al mismo lado que la del clic
  // (dot >= 0.35 ≈ 70°). Excluye el reverso (dot<0) y las paredes (dot~0).
  CIRCLE_DOT_MIN: 0.35,

  setMode(mode) {
    this.mode = this.mode === mode ? null : mode;
    if (this.mode && typeof Seams !== 'undefined') Seams.stop();  // modos excluyentes

    const brush = this.mode === 'add' || this.mode === 'remove';
    Viewer.setOrbitLeft(!brush);  // en pincel, el botón izq. pinta (no orbita)
    document.getElementById('viewport').classList.toggle('seam-cursor', this.mode === 'flood');
    document.getElementById('viewport').classList.toggle('brush-cursor', brush);
    if (!brush) Viewer.hideBrushCursor();

    for (const [id, m] of [
      ['btnSelectFace', 'flood'], ['btnBrushAdd', 'add'], ['btnBrushRemove', 'remove'],
    ]) {
      document.getElementById(id).classList.toggle('active', this.mode === m);
    }

    if (this.mode === 'flood') setStatus('Clic sobre la cara A (se rellena por ángulo)');
    else if (brush) {
      const shape = this.brushShape === 'circle' ? 'círculo (superficie)' : 'esfera (volumen)';
      setStatus(`Pincel ${this.mode === 'add' ? '+' : '−'} ${shape}: arrastra sobre la pieza (radio ${this.brushRadius} mm)`);
    } else setStatus('');
  },

  setBrushRadius(mm) {
    this.brushRadius = mm;
    document.getElementById('brushValue').textContent = `${mm} mm`;
  },

  setBrushShape(shape) {
    this.brushShape = shape;
    Viewer.hideBrushCursor();
    for (const [id, s] of [['btnBrushCircle', 'circle'], ['btnBrushSphere', 'sphere']]) {
      document.getElementById(id).classList.toggle('active', shape === s);
    }
  },

  // Movimiento del cursor en modo pincel: actualiza la guía y pinta si arrastra
  brushMove(event) {
    const hit = Viewer.pickPoint(event);
    if (!hit) { Viewer.hideBrushCursor(); return; }
    const normal = Viewer.faceNormal(hit.faceIndex);
    Viewer.showBrushCursor(hit.point, this.brushRadius, this.brushShape, normal);
    if (this._painting) {
      // Círculo: filtra por normal (solo la superficie del clic, no el reverso)
      const faces = this.brushShape === 'circle'
        ? Viewer.facesWithinRadius(hit.point, this.brushRadius, normal, this.CIRCLE_DOT_MIN)
        : Viewer.facesWithinRadius(hit.point, this.brushRadius);
      if (this.mode === 'add') faces.forEach(f => this.faces.add(f));
      else faces.forEach(f => this.faces.delete(f));
      this.queueRecolor();
    }
  },

  async floodAt(event) {
    const f = Viewer.pickFace(event);
    if (f == null) return;
    try {
      const r = await API.floodSelect(f, this.angleDeg, this.sideDeg);
      r.faces.forEach(x => this.faces.add(x));
      this.recolor();
      this.commitInfo();
      setStatus(`Cara A: +${r.n_faces.toLocaleString()} caras`);
    } catch (e) { setStatus(e.message, true); }
  },

  // Reconstruir el overlay como mucho una vez por frame (pincel fluido)
  queueRecolor() {
    if (this._rebuildQueued) return;
    this._rebuildQueued = true;
    requestAnimationFrame(() => { this._rebuildQueued = false; this.recolor(); });
  },

  recolor() { Viewer.showSelection([...this.faces]); },

  setAngle(deg) {
    this.angleDeg = deg;
    document.getElementById('angleValue').textContent = `${deg}°`;
  },

  setSide(deg) {
    this.sideDeg = deg;
    document.getElementById('sideValue').textContent = deg >= 180 ? 'off' : `${deg}°`;
  },

  selectAll() {
    // Útil cuando el STL ya es solo la superficie de la cara A
    if (!Viewer.mesh) return;
    const n = Viewer.mesh.geometry.index.count / 3;
    this.faces = new Set(Array.from({ length: n }, (_, i) => i));
    this.recolor();
    this.commitInfo();
  },

  clear() {
    this.faces.clear();
    Viewer.clearOverlay();
    document.getElementById('selectionInfo').textContent = '';
  },

  reset() { this.faces.clear(); this.setMode(null); },

  // Conteo inmediato + área real (consulta al backend, solo al soltar)
  commitInfo() {
    const el = document.getElementById('selectionInfo');
    if (!this.faces.size) { el.textContent = ''; return; }
    el.textContent = `${this.faces.size.toLocaleString()} caras`;
    API.selectionInfo([...this.faces])
      .then(r => { el.textContent = `${r.n_faces.toLocaleString()} caras · ${r.area_cm2} cm²`; })
      .catch(() => {});
  },
};
