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

  async getGeometry() {
    const r = await fetch(`${this.base}/mesh/geometry`);
    if (!r.ok) throw new Error('No hay malla cargada');
    return r.json();
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

  async segment(selectedFaces) {
    const r = await fetch(`${this.base}/segment`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ selected_faces: selectedFaces }),
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
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'flatwrap_pattern.dxf';
    a.click();
  },
};
