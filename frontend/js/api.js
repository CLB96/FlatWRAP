// api.js - cliente de la API local de FlatWrap
const API = {
  base: '/api',

  async uploadMesh(file) {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`${this.base}/mesh`, { method: 'POST', body: fd });
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();
  },

  // Proyecto (.flatwrap): malla limpia + costuras + Cara A + ajustes
  async saveProject(selectedFaces, settings, name) {
    const r = await fetch(`${this.base}/project/save`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selected_faces: selectedFaces, settings, name }),
    });
    if (!r.ok) throw new Error((await r.json()).detail);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(await r.blob());
    a.download = (name.replace(/\.[^.]+$/, '') || 'proyecto') + '.flatwrap';
    a.click();
  },

  async loadProject(file) {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(`${this.base}/project/load`, { method: 'POST', body: fd });
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();
  },

  // Geometría binaria (Float32/Uint32): sin parsear JSON de millones de números
  async getGeometry() {
    const r = await fetch(`${this.base}/mesh/geometry.bin`);
    if (!r.ok) throw new Error('No hay malla cargada');
    const buf = await r.arrayBuffer();
    const [nV, nF] = new Uint32Array(buf, 0, 2);
    return {
      vertices: new Float32Array(buf, 8, nV * 3),
      faces: new Uint32Array(buf, 8 + nV * 12, nF * 3),
    };
  },

  async createSeam(vertexIds, closed = false) {
    const r = await fetch(`${this.base}/seams`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vertex_ids: vertexIds, closed }),
    });
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();
  },

  async previewSeam(vertexIds, closed = false) {
    const r = await fetch(`${this.base}/seams/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ vertex_ids: vertexIds, closed }),
    });
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();
  },

  async deleteSeam(id) {
    await fetch(`${this.base}/seams/${id}`, { method: 'DELETE' });
  },

  async setSeamStraighten(id, straighten) {
    const r = await fetch(
      `${this.base}/seams/${id}/straighten?straighten=${straighten}`,
      { method: 'POST' }
    );
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();
  },

  async floodSelect(faceId, angleDeg, sideDeg = 85) {
    const r = await fetch(`${this.base}/selection/flood`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ face_id: faceId, angle_deg: angleDeg, side_deg: sideDeg }),
    });
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();
  },

  async selectionInfo(faceIds) {
    const r = await fetch(`${this.base}/selection/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ face_ids: faceIds }),
    });
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();
  },

  async segment(selectedFaces, snapMm = 5) {
    const r = await fetch(`${this.base}/segment`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selected_faces: selectedFaces, snap_mm: snapMm }),
    });
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();
  },

  async flattenAll(method = 'unfold') {
    const r = await fetch(`${this.base}/flatten-all?method=${method}`, { method: 'POST' });
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();  // { patches: [...], failed: [...] }
  },

  async deletePatch(patchId) {
    const r = await fetch(`${this.base}/patches/${patchId}`, { method: 'DELETE' });
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();
  },

  async subdivideMesh() {
    const r = await fetch(`${this.base}/mesh/subdivide`, { method: 'POST' });
    if (!r.ok) throw new Error((await r.json()).detail);
    return r.json();
  },

  async exportDXF(opts = {}) {
    const r = await fetch(`${this.base}/export/dxf`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    });
    if (!r.ok) throw new Error((await r.json()).detail || 'Error al exportar DXF');
    // Parches que no se pudieron dibujar (nunca se omiten sin avisar)
    const skipped = (r.headers.get('X-FlatWrap-Skipped') || '').split(',').filter(Boolean);
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'flatwrap_pattern.dxf';
    a.click();
    return { skipped };
  },

  // Contorno de corte (misma geometría que el DXF) para la vista 2D
  async previewCut(opts = {}) {
    const r = await fetch(`${this.base}/export/preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opts),
    });
    if (!r.ok) throw new Error((await r.json()).detail || 'Error en la vista previa de corte');
    return r.json();
  },
};
