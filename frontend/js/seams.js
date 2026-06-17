// seams.js - costuras por clics con línea en vivo sobre la superficie.
//
// Flujo: "Crear costura" -> clic punto por punto; una línea va uniendo los
// puntos sobre la superficie (camino más corto) en vivo -> "Terminar"
// registra la costura y DEJA visibles sus puntos + su línea. Una costura
// nueva puede UNIRSE a una existente (los puntos se imantan a sus vértices)
// para delimitar bien las secciones.
const Seams = {
  active: false,           // modo "crear costura" activo
  currentClicks: [],       // vertex_ids de la costura en construcción
  markers: [],             // esferas de los clics en curso
  liveLine: null,          // polilínea de vista previa (en vivo)
  // seam_id -> { line, markers:[], chain:[{id,pos:[x,y,z]}] }
  seamObjs: new Map(),
  pointSizeMm: 2,          // tamaño del punto de clic (regulable)

  start() {
    if (typeof Selection !== 'undefined') Selection.setMode(null);  // modos excluyentes
    this.active = true;
    this.cancelCurrent();
    this._syncButtons();
    document.getElementById('viewport').classList.add('seam-cursor');
    setStatus('Crear costura: clic punto por punto (se une a costuras cercanas)');
  },

  stop() {
    this.active = false;
    this.cancelCurrent();
    this._syncButtons();
    document.getElementById('viewport').classList.remove('seam-cursor');
  },

  toggle() { this.active ? this.stop() : this.start(); },

  _syncButtons() {
    document.getElementById('btnSeamMode').classList.toggle('active', this.active);
    ['btnFinishSeam', 'btnCancelSeam', 'btnCloseSeam'].forEach(id => {
      document.getElementById(id).disabled = !this.active;
    });
  },

  async handleClick(event) {
    if (!this.active) return;
    let hit = Viewer.pickVertex(event);
    if (!hit) return;
    const snapped = this._snap(hit);   // imantar a una costura existente cercana
    // Ignorar si cae en el mismo vértice que el punto anterior (malla densa)
    if (this.currentClicks.length && snapped.vertexId === this.currentClicks[this.currentClicks.length - 1]) {
      return setStatus('Ese punto coincide con el anterior — marca uno separado');
    }
    this.currentClicks.push(snapped.vertexId);
    this.addMarker(snapped.point, this.markers);
    await this.redrawLive(false);
    setStatus(`${this.currentClicks.length} punto(s)${snapped.joined ? ' · unido a costura' : ''} · Terminar para cerrar`);
  },

  // Imanta el clic al vértice más cercano de una costura ya hecha (si está
  // dentro del radio de snap) para que las costuras se conecten.
  _snap(hit) {
    const snapD = Math.max(Viewer.meshRadius() * 0.025, 1);
    let best = null, bestD = snapD * snapD;
    for (const obj of this.seamObjs.values()) {
      for (const cp of obj.chain) {
        const dx = cp.pos[0] - hit.point.x, dy = cp.pos[1] - hit.point.y, dz = cp.pos[2] - hit.point.z;
        const d = dx * dx + dy * dy + dz * dz;
        if (d < bestD) { bestD = d; best = cp; }
      }
    }
    if (best) return { vertexId: best.id, point: new THREE.Vector3(...best.pos), joined: true };
    return { vertexId: hit.vertexId, point: hit.point, joined: false };
  },

  // Traza el camino actual sobre la superficie (vista previa, sin registrar)
  async redrawLive(closed) {
    this._removeLiveLine();
    if (this.currentClicks.length < 2) return;
    const r = await API.previewSeam(this.currentClicks, closed);
    this.liveLine = this._makeLine(r.polyline);
    Viewer.scene.add(this.liveLine);
  },

  _makeLine(polyline) {
    const pts = polyline.map(p => new THREE.Vector3(...p));
    const line = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(pts),
      new THREE.LineBasicMaterial({ color: 0xf0883e })
    );
    line.renderOrder = 1;
    return line;
  },

  _makeMarker(point) {
    const m = new THREE.Mesh(
      new THREE.SphereGeometry(1, 12, 12),  // radio base 1 -> se escala en mm
      new THREE.MeshBasicMaterial({ color: 0xf0883e, depthTest: false })
    );
    m.position.copy(point);
    m.scale.setScalar(this.pointSizeMm);
    m.renderOrder = 2;
    return m;
  },

  addMarker(point, into) {
    const m = this._makeMarker(point);
    Viewer.scene.add(m);
    into.push(m);
  },

  setPointSize(mm) {
    this.pointSizeMm = mm;
    this.markers.forEach(m => m.scale.setScalar(mm));
    for (const obj of this.seamObjs.values()) obj.markers.forEach(m => m.scale.setScalar(mm));
    document.getElementById('pointValue').textContent = `${mm} mm`;
  },

  undoLastPoint() {
    if (!this.currentClicks.length) return;
    this.currentClicks.pop();
    const marker = this.markers.pop();
    if (marker) Viewer.scene.remove(marker);
    this.redrawLive(false);
    setStatus(`${this.currentClicks.length} punto(s)`);
  },

  // Vértices+posición de la cadena de aristas (para imantar costuras nuevas).
  // Viene directo del backend (seam.chain = [[id,x,y,z], ...]); independiente
  // de la polilínea geodésica de dibujo.
  _chainOf(seam) {
    return (seam.chain || []).map(c => ({ id: c[0], pos: [c[1], c[2], c[3]] }));
  },

  async finish(closed = false) {
    const minPoints = closed ? 3 : 2;
    if (this.currentClicks.length < minPoints) {
      return setStatus(`Marca al menos ${minPoints} puntos`, true);
    }
    const seam = await API.createSeam(this.currentClicks, closed);
    // Línea final + MANTENER los puntos visibles (son los marcadores actuales)
    const line = this._makeLine(seam.polyline);
    Viewer.scene.add(line);
    this.seamObjs.set(seam.seam_id, {
      line,
      markers: this.markers,        // los puntos quedan visibles
      chain: this._chainOf(seam),
    });
    this.markers = [];              // ya no son temporales; no los borres
    this.currentClicks = [];
    this._removeLiveLine();
    this.refreshList();
    setStatus(`Costura #${seam.seam_id} creada — los puntos quedan visibles`);
  },

  cancel() {
    if (!this.currentClicks.length) return setStatus('No hay costura en curso');
    this.cancelCurrent();
    setStatus('Costura cancelada');
  },

  // Limpia SOLO la costura en construcción (no las ya registradas)
  cancelCurrent() {
    this.markers.forEach(m => Viewer.scene.remove(m));
    this.markers = [];
    this.currentClicks = [];
    this._removeLiveLine();
  },

  _removeLiveLine() {
    if (this.liveLine) {
      Viewer.scene.remove(this.liveLine);
      this.liveLine.geometry.dispose();
      this.liveLine = null;
    }
  },

  // Borra TODO (al cargar una malla nueva): costuras registradas + en curso
  resetAll() {
    this.stop();
    for (const obj of this.seamObjs.values()) this._removeSeamObjs(obj);
    this.seamObjs.clear();
    this.refreshList();
  },

  _removeSeamObjs(obj) {
    Viewer.scene.remove(obj.line);
    obj.markers.forEach(m => Viewer.scene.remove(m));
  },

  async remove(seamId) {
    await API.deleteSeam(seamId);
    const obj = this.seamObjs.get(seamId);
    if (obj) this._removeSeamObjs(obj);
    this.seamObjs.delete(seamId);
    this.refreshList();
    // Las costuras cambiaron: los parches quedan inválidos -> volver a la cara A
    if (Selection.faces.size) Viewer.showSelection([...Selection.faces]);
    else Viewer.clearOverlay();
    document.getElementById('segmentInfo').textContent = '';
  },

  // Marca una costura como de UNIÓN (corte recto en el DXF) o REAL (conserva
  // la curva). Por defecto REAL: nunca pierde la geometría de diseño.
  async setStraighten(seamId, on) {
    (this._union ||= new Set());
    if (on) this._union.add(seamId); else this._union.delete(seamId);
    try { await API.setSeamStraighten(seamId, on); }
    catch (e) { setStatus(e.message, true); }
  },

  refreshList() {
    const ul = document.getElementById('seamList');
    ul.innerHTML = '';
    for (const id of this.seamObjs.keys()) {
      const checked = (this._union && this._union.has(id)) ? 'checked' : '';
      const li = document.createElement('li');
      li.innerHTML = `Costura #${id}
        <label class="seam-union" title="Costura de UNIÓN: su corte sale RECTO en el DXF para casar parches en AutoCAD. Sin marcar = costura REAL (conserva la curva de diseño).">
          <input type="checkbox" ${checked} onchange="Seams.setStraighten(${id}, this.checked)"> unión (recta)
        </label>
        <button onclick="Seams.remove(${id})">✕</button>`;
      ul.appendChild(li);
    }
  },
};
