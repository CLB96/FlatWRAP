// viewer.js - escena Three.js con controles tipo CAD.
//
// UX de cámara (pensado para usuarios de Solid Edge):
//   - Arrastre izquierdo  = orbitar   ·  derecho/medio = paneo
//   - Rueda               = zoom HACIA EL CURSOR (no al centro)
//   - Doble clic          = recentrar el pivote de órbita en ese punto
//   - Botones de vista    = Iso / Frente / Lado / Planta / Encuadrar
//   - Eje arriba Z (convención CAD) con toggle a Y
//   - Teclas: F encuadrar · 1 frente · 3 lado · 7 planta · 5 iso
const Viewer = {
  scene: null, camera: null, renderer: null, controls: null,
  mesh: null,            // THREE.Mesh actual (indexada, para picking)
  overlay: null,         // THREE.Mesh sobrepuesta: cara A / parches coloreados
  brushSphere: null,     // guía esfera del pincel (modo volumen)
  brushDisc: null,       // guía disco del pincel (modo superficie)
  grid: null, axes: null,
  upAxis: 'z',           // convención CAD (Solid Edge exporta Z arriba)
  raycaster: new THREE.Raycaster(),
  _faceCentroids: null,     // centroides de cada cara (selección por radio)
  _faceNormals: null,       // normales de cada cara (pincel círculo)
  _targetAnim: null,        // animación del pivote (doble clic)

  init() {
    const el = document.getElementById('viewport');
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0d1117);

    this.camera = new THREE.PerspectiveCamera(50, innerWidth / innerHeight, 1, 50000);
    this.camera.position.set(400, -400, 300);

    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setSize(innerWidth, innerHeight);
    this.renderer.setPixelRatio(devicePixelRatio);
    el.appendChild(this.renderer.domElement);

    this._applyUpAxis();
    this._createControls();

    // Iluminación PAREJA (sin sombras fuertes): la prioridad es ver con
    // claridad qué está y qué no seleccionado como Cara A, no el realismo.
    // Ambiente sube todo el piso de luz; hemisférico con suelo claro evita
    // las caras inferiores oscuras; varias direccionales de relleno desde
    // octantes opuestos eliminan cualquier zona en penumbra.
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0xb8c2cc, 0.7));
    const fills = [
      [1, 2, 1.5], [-1.5, -1, 1], [0, -2, -1], [1.5, 0.5, -1.5],
    ];
    for (const [x, y, z] of fills) {
      const d = new THREE.DirectionalLight(0xffffff, 0.32);
      d.position.set(x, y, z);
      this.scene.add(d);
    }

    // Zoom al cursor (reemplaza el dolly por defecto de OrbitControls)
    this.renderer.domElement.addEventListener('wheel', (e) => {
      e.preventDefault();
      this._zoomToCursor(e);
    }, { passive: false });

    // Doble clic: recentrar pivote de órbita en el punto clickeado
    this.renderer.domElement.addEventListener('dblclick', (e) => {
      const hit = this._raycast(e);
      if (hit) this._animateTarget(hit.point);
    });

    addEventListener('resize', () => {
      this.camera.aspect = innerWidth / innerHeight;
      this.camera.updateProjectionMatrix();
      this.renderer.setSize(innerWidth, innerHeight);
    });

    const animate = () => {
      requestAnimationFrame(animate);
      this._updateTargetAnim();
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    };
    animate();
  },

  // --- Controles -----------------------------------------------------
  // OrbitControls captura camera.up al construirse: para cambiar el eje
  // de arriba hay que recrearlos conservando el target.
  _createControls() {
    const target = this.controls ? this.controls.target.clone() : new THREE.Vector3();
    if (this.controls) this.controls.dispose();

    const c = new THREE.OrbitControls(this.camera, this.renderer.domElement);
    c.enableDamping = true;
    c.dampingFactor = 0.12;
    c.rotateSpeed = 0.85;
    c.panSpeed = 1.0;
    c.screenSpacePanning = true;   // paneo plano a la pantalla (como CAD)
    c.enableZoom = false;          // el zoom lo maneja _zoomToCursor
    c.mouseButtons = {
      LEFT: THREE.MOUSE.ROTATE,
      MIDDLE: THREE.MOUSE.PAN,
      RIGHT: THREE.MOUSE.PAN,
    };
    c.target.copy(target);
    c.update();
    this.controls = c;
  },

  _applyUpAxis() {
    this.camera.up.set(0, this.upAxis === 'y' ? 1 : 0, this.upAxis === 'z' ? 1 : 0);
  },

  // Activa/desactiva la órbita con botón izquierdo (el pincel lo necesita libre)
  setOrbitLeft(enabled) {
    this.controls.mouseButtons.LEFT = enabled ? THREE.MOUSE.ROTATE : null;
  },

  toggleUpAxis() {
    this.upAxis = this.upAxis === 'z' ? 'y' : 'z';
    this._applyUpAxis();
    this._createControls();
    this._rebuildHelpers();
    this.setView('iso');
    return this.upAxis;
  },

  // --- Zoom al cursor ------------------------------------------------
  _zoomToCursor(event) {
    const factor = Math.pow(0.95, -Math.sign(event.deltaY) * 1.4);
    const hit = this._raycast(event);
    // Punto de anclaje: la superficie bajo el cursor, o el plano del target
    let anchor;
    if (hit) {
      anchor = hit.point;
    } else {
      const ndc = this._ndc(event);
      this.raycaster.setFromCamera(ndc, this.camera);
      const dist = this.camera.position.distanceTo(this.controls.target);
      anchor = this.raycaster.ray.at(dist, new THREE.Vector3());
    }
    const minDist = this.camera.near * 4;
    if (factor < 1 && this.camera.position.distanceTo(anchor) < minDist) return;

    // Escalar cámara y pivote alrededor del ancla: el zoom "va" al cursor
    this.camera.position.sub(anchor).multiplyScalar(factor).add(anchor);
    this.controls.target.sub(anchor).multiplyScalar(factor).add(anchor);
  },

  // --- Pivote animado (doble clic) ------------------------------------
  _animateTarget(point) {
    this._targetAnim = {
      from: this.controls.target.clone(),
      to: point.clone(),
      start: performance.now(),
      duration: 250,
    };
  },

  _updateTargetAnim() {
    const anim = this._targetAnim;
    if (!anim) return;
    const t = Math.min((performance.now() - anim.start) / anim.duration, 1);
    const ease = 1 - Math.pow(1 - t, 3);
    this.controls.target.lerpVectors(anim.from, anim.to, ease);
    if (t >= 1) this._targetAnim = null;
  },

  // --- Vistas estándar -------------------------------------------------
  setView(name) {
    if (!this.mesh) return;
    const geo = this.mesh.geometry;
    geo.computeBoundingSphere();
    const { center, radius } = geo.boundingSphere;
    const d = radius * 2.2;

    // Direcciones según el eje de arriba activo
    const dirs = this.upAxis === 'z'
      ? { iso: [1, -1, 0.7], front: [0, -1, 0], side: [1, 0, 0], top: [0, 0, 1] }
      : { iso: [1, 0.7, 1], front: [0, 0, 1], side: [1, 0, 0], top: [0, 1, 0] };
    const v = new THREE.Vector3(...(dirs[name] || dirs.iso)).normalize();
    // En vista cenital el "up" no puede ser paralelo a la dirección de vista
    if (name === 'top') v.add(new THREE.Vector3(0.001, 0.001, 0)).normalize();

    this.controls.target.copy(center);
    this.camera.position.copy(center).addScaledVector(v, d);
    this.camera.near = Math.max(radius / 100, 0.1);
    this.camera.far = radius * 100;
    this.camera.updateProjectionMatrix();
  },

  fit() { this.setView('iso'); },

  meshRadius() {
    if (!this.mesh) return 100;
    this.mesh.geometry.computeBoundingSphere();
    return this.mesh.geometry.boundingSphere.radius;
  },

  // --- Carga de malla ---------------------------------------------------
  // Construye la malla desde el payload del backend (vértices soldados:
  // los índices coinciden con los del backend -> clave para las costuras).
  loadGeometry({ vertices, faces }) {
    if (this.mesh) this.scene.remove(this.mesh);

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    geo.setIndex(Array.isArray(faces) ? faces : new THREE.BufferAttribute(faces, 1));
    geo.computeVertexNormals();

    const mat = new THREE.MeshStandardMaterial({
      color: 0x8aa9c9, metalness: 0.1, roughness: 0.75,
      side: THREE.DoubleSide,
      // Empuja la superficie hacia atrás para que las líneas de costura
      // dibujadas encima no parpadeen (z-fighting)
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
    });
    this.mesh = new THREE.Mesh(geo, mat);
    this.scene.add(this.mesh);
    this.clearOverlay();
    this.hideBrushCursor();
    this._computeCentroids();
    this._buildFeatureEdges();
    this._rebuildHelpers();
    this.setView('iso');
  },

  // Aristas donde CAMBIA el ángulo (cantos/creases): con luz pareja sin sombras,
  // marcan dónde acaba una curva y empieza una recta. EdgesGeometry solo dibuja
  // las aristas cuyo ángulo diédrico supera el umbral (la superficie lisa no).
  _buildFeatureEdges() {
    if (this.featureEdges) {
      this.scene.remove(this.featureEdges);
      this.featureEdges.geometry.dispose();
      this.featureEdges = null;
    }
    if (!this.mesh) return;
    const thr = this.edgeThresholdDeg ?? 22;
    const eg = new THREE.EdgesGeometry(this.mesh.geometry, thr);
    const lmat = new THREE.LineBasicMaterial({
      color: 0x0c1622, transparent: true, opacity: 0.6,
    });
    this.featureEdges = new THREE.LineSegments(eg, lmat);
    this.featureEdges.visible = this._edgesOn !== false;
    this.featureEdges.renderOrder = 1;
    this.scene.add(this.featureEdges);
  },

  setEdgesVisible(on) {
    this._edgesOn = on;
    if (this.featureEdges) this.featureEdges.visible = on;
  },

  setEdgeThreshold(deg) {
    this.edgeThresholdDeg = deg;
    this._buildFeatureEdges();
  },

  // Centroide y normal de cada cara, precalculados para el pincel
  _computeCentroids() {
    const idx = this.mesh.geometry.index.array;
    const pos = this.mesh.geometry.attributes.position.array;
    const nFaces = idx.length / 3;
    const c = new Float32Array(nFaces * 3);
    const nrm = new Float32Array(nFaces * 3);
    const v0 = new THREE.Vector3(), v1 = new THREE.Vector3(), v2 = new THREE.Vector3();
    const e1 = new THREE.Vector3(), e2 = new THREE.Vector3(), n = new THREE.Vector3();
    for (let f = 0; f < nFaces; f++) {
      const a = idx[f * 3], b = idx[f * 3 + 1], d = idx[f * 3 + 2];
      v0.set(pos[a * 3], pos[a * 3 + 1], pos[a * 3 + 2]);
      v1.set(pos[b * 3], pos[b * 3 + 1], pos[b * 3 + 2]);
      v2.set(pos[d * 3], pos[d * 3 + 1], pos[d * 3 + 2]);
      c[f * 3] = (v0.x + v1.x + v2.x) / 3;
      c[f * 3 + 1] = (v0.y + v1.y + v2.y) / 3;
      c[f * 3 + 2] = (v0.z + v1.z + v2.z) / 3;
      e1.subVectors(v1, v0); e2.subVectors(v2, v0);
      n.crossVectors(e1, e2).normalize();
      nrm[f * 3] = n.x; nrm[f * 3 + 1] = n.y; nrm[f * 3 + 2] = n.z;
    }
    this._faceCentroids = c;
    this._faceNormals = nrm;
  },

  faceNormal(f) {
    const n = this._faceNormals;
    return new THREE.Vector3(n[f * 3], n[f * 3 + 1], n[f * 3 + 2]);
  },

  // Ids de cara cuyo centroide cae dentro de `radius` (mm) del punto.
  // Si se pasa `normal` + `dotMin`, además exige que la cara mire al mismo
  // lado (pincel CÍRCULO: no atraviesa el espesor ni toma el reverso).
  facesWithinRadius(point, radius, normal = null, dotMin = -1) {
    const c = this._faceCentroids;
    const nrm = this._faceNormals;
    if (!c) return [];
    const r2 = radius * radius;
    const out = [];
    for (let f = 0; f < c.length / 3; f++) {
      const dx = c[f * 3] - point.x;
      const dy = c[f * 3 + 1] - point.y;
      const dz = c[f * 3 + 2] - point.z;
      if (dx * dx + dy * dy + dz * dz > r2) continue;
      if (normal) {
        const dot = nrm[f * 3] * normal.x + nrm[f * 3 + 1] * normal.y + nrm[f * 3 + 2] * normal.z;
        if (dot < dotMin) continue;
      }
      out.push(f);
    }
    return out;
  },

  // Rejilla y ejes de referencia, escalados a la pieza
  _rebuildHelpers() {
    if (this.grid) this.scene.remove(this.grid);
    if (this.axes) this.scene.remove(this.axes);
    if (!this.mesh) return;

    const geo = this.mesh.geometry;
    geo.computeBoundingBox();
    geo.computeBoundingSphere();
    const bb = geo.boundingBox;
    const r = geo.boundingSphere.radius;
    const center = geo.boundingSphere.center;

    this.grid = new THREE.GridHelper(r * 4, 20, 0x2d4a6b, 0x1b2736);
    if (this.upAxis === 'z') {
      this.grid.rotation.x = Math.PI / 2;
      this.grid.position.set(center.x, center.y, bb.min.z - r * 0.05);
    } else {
      this.grid.position.set(center.x, bb.min.y - r * 0.05, center.z);
    }
    this.scene.add(this.grid);

    this.axes = new THREE.AxesHelper(r * 0.6);
    this.axes.position.copy(this.grid.position);
    this.scene.add(this.axes);
  },

  // --- Picking -----------------------------------------------------------
  _ndc(event) {
    const rect = this.renderer.domElement.getBoundingClientRect();
    return new THREE.Vector2(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1
    );
  },

  _raycast(event) {
    if (!this.mesh) return null;
    this.raycaster.setFromCamera(this._ndc(event), this.camera);
    return this.raycaster.intersectObject(this.mesh)[0] || null;
  },

  // Id de la CARA (triángulo) bajo el cursor — para seleccionar la cara A
  pickFace(event) {
    const hit = this._raycast(event);
    return hit ? hit.faceIndex : null;
  },

  // Punto 3D bajo el cursor + cara — para el pincel por radio
  pickPoint(event) {
    const hit = this._raycast(event);
    return hit ? { point: hit.point.clone(), faceIndex: hit.faceIndex } : null;
  },

  // --- Cursor guía del pincel ---------------------------------------------
  // shape 'circle' -> disco pegado a la superficie (orientado a la normal)
  // shape 'sphere' -> esfera translúcida de volumen
  showBrushCursor(point, radiusMm, shape, normal = null) {
    if (shape === 'circle') {
      if (!this.brushDisc) {
        const geo = new THREE.CircleGeometry(1, 40);
        const mat = new THREE.MeshBasicMaterial({
          color: 0x2dd4bf, transparent: true, opacity: 0.3,
          side: THREE.DoubleSide, depthWrite: false,
        });
        this.brushDisc = new THREE.Mesh(geo, mat);
        this.brushDisc.renderOrder = 2;
        this.scene.add(this.brushDisc);
      }
      this.brushDisc.position.copy(point);
      if (normal) {
        this.brushDisc.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), normal);
        this.brushDisc.position.addScaledVector(normal, radiusMm * 0.02);  // evita z-fight
      }
      this.brushDisc.scale.setScalar(radiusMm);
      this.brushDisc.visible = true;
      if (this.brushSphere) this.brushSphere.visible = false;
      return;
    }

    if (!this.brushSphere) {
      const geo = new THREE.SphereGeometry(1, 20, 16);
      const mat = new THREE.MeshBasicMaterial({
        color: 0x2dd4bf, transparent: true, opacity: 0.22, depthWrite: false,
      });
      this.brushSphere = new THREE.Mesh(geo, mat);
      this.brushSphere.renderOrder = 2;
      this.scene.add(this.brushSphere);
    }
    this.brushSphere.position.copy(point);
    this.brushSphere.scale.setScalar(radiusMm);
    this.brushSphere.visible = true;
    if (this.brushDisc) this.brushDisc.visible = false;
  },

  hideBrushCursor() {
    if (this.brushSphere) this.brushSphere.visible = false;
    if (this.brushDisc) this.brushDisc.visible = false;
  },

  // Vértice de malla más cercano al clic — para marcar costuras
  pickVertex(event) {
    const hit = this._raycast(event);
    if (!hit) return null;
    const idx = this.mesh.geometry.index.array;
    const pos = this.mesh.geometry.attributes.position;
    const candidates = [idx[hit.faceIndex * 3], idx[hit.faceIndex * 3 + 1], idx[hit.faceIndex * 3 + 2]];
    let best = candidates[0], bestD = Infinity;
    for (const v of candidates) {
      const d = hit.point.distanceToSquared(new THREE.Vector3().fromBufferAttribute(pos, v));
      if (d < bestD) { bestD = d; best = v; }
    }
    return { vertexId: best, point: hit.point.clone() };
  },

  // --- Overlay de caras (cara A / parches) --------------------------------
  // Malla sobrepuesta NO indexada (cada cara con sus 3 vértices propios):
  // permite color por cara sin sangrado en los bordes de la selección.
  _patchPalette(n) {
    const colors = [];
    for (let i = 0; i < n; i++) {
      colors.push(new THREE.Color().setHSL((i * 0.37) % 1, 0.6, 0.55));
    }
    return colors;
  },

  // Construye/actualiza el overlay con las caras dadas y un color por cara.
  // colorOf(faceId) -> THREE.Color
  _buildOverlay(faceIds, colorOf) {
    this.clearOverlay();
    if (!this.mesh || !faceIds.length) return;

    const idx = this.mesh.geometry.index.array;
    const pos = this.mesh.geometry.attributes.position.array;
    const positions = new Float32Array(faceIds.length * 9);
    const colors = new Float32Array(faceIds.length * 9);

    for (let i = 0; i < faceIds.length; i++) {
      const f = faceIds[i];
      const c = colorOf(f);
      for (let k = 0; k < 3; k++) {
        const v = idx[f * 3 + k];
        const o = i * 9 + k * 3;
        positions[o] = pos[v * 3];
        positions[o + 1] = pos[v * 3 + 1];
        positions[o + 2] = pos[v * 3 + 2];
        colors[o] = c.r; colors[o + 1] = c.g; colors[o + 2] = c.b;
      }
    }

    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    geo.computeVertexNormals();

    const mat = new THREE.MeshStandardMaterial({
      vertexColors: true, metalness: 0.0, roughness: 0.6,
      side: THREE.DoubleSide,
      polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -1,
    });
    this.overlay = new THREE.Mesh(geo, mat);
    this.overlay.renderOrder = 1;
    this.scene.add(this.overlay);
  },

  // Resalta la cara A seleccionada (un solo color)
  showSelection(faceIds) {
    const teal = new THREE.Color(0x2dd4bf);
    this._buildOverlay(faceIds, () => teal);
  },

  // Colorea los parches resultantes de la segmentación (-1 = fuera de cara A)
  showPatches(facePatch, nPatches) {
    const palette = this._patchPalette(nPatches);
    const faceIds = [];
    for (let f = 0; f < facePatch.length; f++) {
      if (facePatch[f] >= 0) faceIds.push(f);
    }
    this._buildOverlay(faceIds, (f) => palette[facePatch[f]]);
  },

  clearOverlay() {
    if (this.overlay) {
      this.scene.remove(this.overlay);
      this.overlay.geometry.dispose();
      this.overlay.material.dispose();
      this.overlay = null;
    }
  },
};
