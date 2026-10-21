// main.js - orquestador: conecta UI <-> Viewer <-> Selection <-> API
const CLICK_TOLERANCE_PX = 6;   // menos movimiento que esto = clic, no arrastre

function setStatus(msg, isError = false) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.classList.toggle('error', isError);
}

window.addEventListener('DOMContentLoaded', () => {
  Viewer.init();

  // --- Fase 1: cargar malla
  document.getElementById('fileInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      setStatus('Cargando y limpiando malla…');
      const info = await API.uploadMesh(file);
      projectName = file.name;
      const geo = await API.getGeometry();
      Viewer.loadGeometry(geo);
      Selection.reset();
      Seams.resetAll();
      document.getElementById('selectionInfo').textContent = '';
      document.getElementById('segmentInfo').textContent = '';
      document.getElementById('patchList').innerHTML = '';
      const dims = info.bbox_max.map((v, i) => (v - info.bbox_min[i]).toFixed(0));
      document.getElementById('meshInfo').textContent =
        `${info.n_vertices.toLocaleString()} vértices · ${info.n_faces.toLocaleString()} caras\n` +
        `BBox: ${dims[0]} × ${dims[1]} × ${dims[2]} mm`;
      setStatus('Malla lista ✓ · selecciona la Cara A');
    } catch (err) { setStatus(err.message, true); }
    e.target.value = '';  // permite recargar el mismo archivo
  });

  // --- Proyecto: guardar / abrir (.flatwrap)
  let projectName = 'proyecto';
  const settingIds = ['inpAllowance', 'chkNotches', 'inpNotchSpacing', 'inpSnap',
    'inpWarp', 'inpWeft', 'inpComp', 'chkTolerance', 'chkHeatmap'];
  const readSettings = () => {
    const o = { method: flattenMethod };
    for (const id of settingIds) {
      const el = document.getElementById(id);
      o[id] = el.type === 'checkbox' ? el.checked : el.value;
    }
    return o;
  };
  const applySettings = (o) => {
    for (const id of settingIds) {
      if (!(id in o)) continue;
      const el = document.getElementById(id);
      if (el.type === 'checkbox') el.checked = !!o[id]; else el.value = o[id];
      el.dispatchEvent(new Event(el.type === 'checkbox' ? 'change' : 'input'));
    }
    if (o.method && window.setFlattenMethod) window.setFlattenMethod(o.method);
  };
  document.getElementById('btnSaveProject').addEventListener('click', async () => {
    try {
      await API.saveProject([...Selection.faces], readSettings(), projectName);
      setStatus('Proyecto guardado ✓');
    } catch (err) { setStatus(err.message, true); }
  });
  document.getElementById('projectInput').addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
      setStatus('Abriendo proyecto…');
      const res = await API.loadProject(file);
      Viewer.loadGeometry(await API.getGeometry());
      Selection.reset();
      Seams.resetAll();
      Pattern2D.hide();
      document.getElementById('segmentInfo').textContent = '';
      document.getElementById('patchList').innerHTML = '';
      res.seams.forEach((s) => Seams.restore(s));
      Seams.refreshList();
      Selection.faces = new Set(res.selected_faces);
      if (Selection.faces.size) { Selection.recolor(); Selection.commitInfo(); }
      projectName = res.name || file.name;
      const info = res.summary;
      const dims = info.bbox_max.map((v, i) => (v - info.bbox_min[i]).toFixed(0));
      document.getElementById('meshInfo').textContent =
        `${info.n_vertices.toLocaleString()} vértices · ${info.n_faces.toLocaleString()} caras\n` +
        `BBox: ${dims[0]} × ${dims[1]} × ${dims[2]} mm`;
      applySettings(res.settings || {});
      const w = res.warnings.length ? ` · ⚠ ${res.warnings.join(' ')}` : '';
      setStatus(`Proyecto abierto ✓ · ${res.seams.length} costura(s)${w}`, !!res.warnings.length);
    } catch (err) { setStatus(err.message, true); }
    e.target.value = '';
  });

  // --- Cámara: barra de vistas
  document.querySelectorAll('[data-view]').forEach(btn =>
    btn.addEventListener('click', () => Viewer.setView(btn.dataset.view)));
  document.getElementById('btnFit').addEventListener('click', () => Viewer.fit());
  document.getElementById('btnUpAxis').addEventListener('click', (e) => {
    const axis = Viewer.toggleUpAxis();
    e.target.textContent = axis === 'z' ? 'Z ↑' : 'Y ↑';
  });

  // --- Fase 2: selección de la Cara A
  document.getElementById('btnSelectFace').addEventListener('click', () => Selection.setMode('flood'));
  document.getElementById('btnBrushAdd').addEventListener('click', () => Selection.setMode('add'));
  document.getElementById('btnBrushRemove').addEventListener('click', () => Selection.setMode('remove'));
  document.getElementById('btnSelectAll').addEventListener('click', () => Selection.selectAll());
  document.getElementById('btnClearSel').addEventListener('click', () => Selection.clear());
  const angleSlider = document.getElementById('inpAngle');
  Selection.setAngle(parseInt(angleSlider.value, 10));
  angleSlider.addEventListener('input', (e) => Selection.setAngle(parseInt(e.target.value, 10)));
  const sideSlider = document.getElementById('inpSide');
  Selection.setSide(parseInt(sideSlider.value, 10));
  sideSlider.addEventListener('input', (e) => Selection.setSide(parseInt(e.target.value, 10)));
  const brushSlider = document.getElementById('inpBrush');
  Selection.setBrushRadius(parseInt(brushSlider.value, 10));
  brushSlider.addEventListener('input', (e) => {
    Selection.setBrushRadius(parseInt(e.target.value, 10));
    if (Selection.mode === 'add' || Selection.mode === 'remove') {
      setStatus(`Pincel: radio ${Selection.brushRadius} mm`);
    }
  });
  document.getElementById('btnBrushCircle').addEventListener('click', () => Selection.setBrushShape('circle'));
  document.getElementById('btnBrushSphere').addEventListener('click', () => Selection.setBrushShape('sphere'));

  // --- Fase 3: costuras (opcionales)
  document.getElementById('btnSeamMode').addEventListener('click', () => Seams.toggle());
  document.getElementById('btnFinishSeam').addEventListener('click', () =>
    Seams.finish(false).catch(e => setStatus(e.message, true)));
  document.getElementById('btnCloseSeam').addEventListener('click', () =>
    Seams.finish(true).catch(e => setStatus(e.message, true)));
  document.getElementById('btnCancelSeam').addEventListener('click', () => Seams.cancel());
  const pointSlider = document.getElementById('inpPointSize');
  Seams.setPointSize(parseFloat(pointSlider.value));
  pointSlider.addEventListener('input', (e) => Seams.setPointSize(parseFloat(e.target.value)));

  // --- Ruteo de puntero: flood (clic), pincel (arrastre), costura (clic)
  let downPos = null;
  const viewport = document.getElementById('viewport');
  const isBrush = () => Selection.mode === 'add' || Selection.mode === 'remove';
  viewport.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    downPos = { x: e.clientX, y: e.clientY };
    if (isBrush()) {
      Selection._painting = true;
      Selection.brushMove(e);  // pinta ya en el primer clic
    }
  });
  viewport.addEventListener('pointermove', (e) => {
    if (isBrush()) Selection.brushMove(e);  // mueve la guía y pinta si arrastra
  });
  const endPointer = (e) => {
    if (Selection._painting) {
      Selection._painting = false;
      Selection.commitInfo();
      downPos = null;
      return;
    }
    if (e.button !== 0 || !downPos) { downPos = null; return; }
    const moved = Math.hypot(e.clientX - downPos.x, e.clientY - downPos.y);
    if (moved < CLICK_TOLERANCE_PX) {
      if (Selection.mode === 'flood') Selection.floodAt(e);
      else if (Seams.active) Seams.handleClick(e).catch(err => setStatus(err.message, true));
    }
    downPos = null;
  };
  viewport.addEventListener('pointerup', endPointer);
  viewport.addEventListener('pointerleave', (e) => {
    Viewer.hideBrushCursor();
    endPointer(e);
  });

  // --- Atajos de teclado
  addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT') return;
    const key = e.key.toLowerCase();
    if (key === 'f') Viewer.fit();
    else if (key === '1') Viewer.setView('front');
    else if (key === '3') Viewer.setView('side');
    else if (key === '7') Viewer.setView('top');
    else if (key === '5') Viewer.setView('iso');
    else if (key === 'escape') { Selection.setMode(null); Seams.stop(); }
    else if (key === 'z' && (e.ctrlKey || e.metaKey) && Seams.active) {
      e.preventDefault();
      Seams.undoLastPoint();
    }
  });

  // --- Fase 4: método de aplanado + generar patrones (segmentar + aplanar)
  let flattenMethod = 'relax';
  const METHOD_BTN = { unfold: 'btnMethodUnfold', relax: 'btnMethodRelax', project: 'btnMethodProject' };
  const setMethod = (m) => {
    flattenMethod = m;
    for (const [name, id] of Object.entries(METHOD_BTN)) {
      document.getElementById(id).classList.toggle('active', m === name);
    }
  };
  window.setFlattenMethod = setMethod;   // lo usa "Abrir proyecto"
  document.getElementById('btnMethodUnfold').addEventListener('click', () => setMethod('unfold'));
  document.getElementById('btnMethodRelax').addEventListener('click', () => setMethod('relax'));
  document.getElementById('btnMethodProject').addEventListener('click', () => setMethod('project'));

  // Lista de patrones con botón eliminar (para descartar Cara B colada)
  const renderPatchList = (patches) => {
    const ul = document.getElementById('patchList');
    ul.innerHTML = '';
    for (const p of patches) {
      const li = document.createElement('li');
      const fail = p.error ? ' ⚠ no aplanó' : ` · ${p.width_mm}×${p.height_mm} mm`;
      li.innerHTML = `Patrón ${p.patch_id}${fail} ` +
        `<button onclick="deletePattern(${p.patch_id})">✕</button>`;
      ul.appendChild(li);
    }
  };

  // Opciones de exportación (compartidas por el DXF y la vista previa de corte)
  const exportOpts = () => ({
    seam_allowance_mm: parseFloat(document.getElementById('inpAllowance').value) || 10,
    add_notches: document.getElementById('chkNotches').checked,
    notch_spacing_mm: parseFloat(document.getElementById('inpNotchSpacing').value) || 50,
  });

  // Pide el contorno de corte al backend si el check está activo (con descarte
  // de respuestas viejas si el usuario cambia opciones rápido).
  let cutSeq = 0, cutTimer = 0;
  const refreshCut = async () => {
    const on = document.getElementById('chkCut').checked;
    Pattern2D.setCutOn(on);
    // Sin patrones aplanados no hay contorno que pedir (evita un 422 inútil)
    if (!on || !Pattern2D.lastPatches || document.getElementById('pattern2d').classList.contains('hidden')) return;
    const my = ++cutSeq;
    try {
      const cut = await API.previewCut(exportOpts());
      if (my !== cutSeq) return;
      Pattern2D.setCut(cut);
      if (cut.skipped && cut.skipped.length) {
        setStatus(`⚠ Sin contorno de corte en patrón(es): ${cut.skipped.map((s) => s.patch_id).join(', ')}`, true);
      }
    } catch (err) { if (my === cutSeq) setStatus(err.message, true); }
  };
  const refreshCutSoon = () => {
    clearTimeout(cutTimer);
    cutTimer = setTimeout(refreshCut, 250);
  };

  // Muestra el resultado del aplanado (2D + estado + lista). seg puede ser null
  // al re-aplanar tras borrar un patrón.
  const showFlattenResult = (res, nSegPatches) => {
    const ok = res.patches, failed = res.failed || [];
    Pattern2D.show(ok);
    if (document.getElementById('chkCut').checked) refreshCut();
    renderPatchList([...ok, ...failed]);

    const tag = { unfold: 'desplegado', relax: 'relajado', project: 'proyección' }[flattenMethod];
    let msg = `${ok.length} patrón(es) ✓ (${tag})`;
    let isWarn = false;
    const overlapped = ok.filter(p => p.overlap).map(p => p.patch_id);
    if (flattenMethod === 'relax' && ok.length) {
      const ten = Math.max(...ok.map(p => p.tension_pct));
      const comp = Math.max(...ok.map(p => p.compression_pct));
      msg += ` · tensión máx ${ten}% · compresión máx ${comp}%`;
    }
    if (overlapped.length) { msg += ` · ⚠ solape en ${overlapped.join(', ')}`; isWarn = true; }
    if (failed.length) {
      msg += ` · ⚠ ${failed.length} no aplanó`;
      isWarn = true;
      const detail = failed.map(f => `\n⚠ Patrón ${f.patch_id}: ${f.error}`).join('');
      document.getElementById('segmentInfo').textContent += detail;
    }
    setStatus(msg, isWarn);
  };

  document.getElementById('btnGenerate').addEventListener('click', async () => {
    try {
      if (!Selection.faces.size) return setStatus('Selecciona primero la Cara A', true);
      setStatus('Generando patrones…');
      const seg = await API.segment([...Selection.faces], parseFloat(document.getElementById('inpSnap').value) || 0);
      Viewer.showPatches(seg.face_patch, seg.n_patches);

      const warns = seg.warnings.length ? `\n⚠ ${seg.warnings.join('\n⚠ ')}` : '';
      if (seg.n_patches === 0) {
        document.getElementById('segmentInfo').textContent =
          'Ningún patrón ≥ 3 cm²: amplía la selección de la Cara A.' + warns;
        document.getElementById('patchList').innerHTML = '';
        return setStatus('Sin patrones válidos (todo ≤ 3 cm²)', true);
      }

      const label = `${seg.n_patches} patrón(es)` +
        (seg.n_patches === 1 ? ' (cara A sin dividir)' : ' (dividido por costuras)');
      document.getElementById('segmentInfo').textContent =
        label + '\n' + seg.patches.map(p => `· Patrón ${p.patch_id}: ${p.area_cm2} cm²`).join('\n') + warns;

      const res = await API.flattenAll(flattenMethod);
      showFlattenResult(res, seg.n_patches);
    } catch (err) { setStatus(err.message, true); }
  });

  // Eliminar un patrón (Cara B colada) y re-aplanar el resto
  window.deletePattern = async (id) => {
    try {
      const r = await API.deletePatch(id);
      Viewer.showPatches(r.face_patch, r.n_patches);
      if (r.n_patches === 0) {
        Pattern2D.hide();
        document.getElementById('patchList').innerHTML = '';
        return setStatus('Sin patrones (eliminaste todos)', true);
      }
      const res = await API.flattenAll(flattenMethod);
      showFlattenResult(res, r.n_patches);
      setStatus(`Patrón ${id} eliminado · quedan ${r.n_patches}`);
    } catch (err) { setStatus(err.message, true); }
  };

  // Refinar malla (más triángulos -> aplanado más suave)
  document.getElementById('btnSubdivide').addEventListener('click', async () => {
    try {
      setStatus('Refinando malla…');
      const info = await API.subdivideMesh();
      const geo = await API.getGeometry();
      Viewer.loadGeometry(geo);
      Selection.reset();
      Seams.resetAll();
      document.getElementById('selectionInfo').textContent = '';
      document.getElementById('segmentInfo').textContent = '';
      document.getElementById('patchList').innerHTML = '';
      document.getElementById('meshInfo').textContent =
        `${info.n_vertices.toLocaleString()} vértices · ${info.n_faces.toLocaleString()} caras (refinada)`;
      setStatus('Malla refinada ✓ · vuelve a seleccionar la Cara A');
    } catch (err) { setStatus(err.message, true); }
  });
  document.getElementById('chkEdges').addEventListener('change', (e) =>
    Viewer.setEdgesVisible(e.target.checked));
  const edgeAngle = document.getElementById('inpEdgeAngle');
  edgeAngle.addEventListener('input', (e) => {
    const deg = parseInt(e.target.value, 10);
    document.getElementById('edgeAngleValue').textContent = `${deg}°`;
    Viewer.setEdgeThreshold(deg);
  });
  document.getElementById('chkHeatmap').addEventListener('change', () => Pattern2D.render());
  const tolFields = document.getElementById('tolFields');
  const syncTol = () => {
    Pattern2D.tolerance.warp = parseFloat(document.getElementById('inpWarp').value) || 0;
    Pattern2D.tolerance.weft = parseFloat(document.getElementById('inpWeft').value) || 0;
    Pattern2D.tolerance.comp = parseFloat(document.getElementById('inpComp').value) || 0;
    Pattern2D.render();
  };
  document.getElementById('chkTolerance').addEventListener('change', (e) => {
    Pattern2D.tolerance.on = e.target.checked;
    tolFields.style.display = e.target.checked ? '' : 'none';
    syncTol();
  });
  ['inpWarp', 'inpWeft', 'inpComp'].forEach((id) =>
    document.getElementById(id).addEventListener('input', syncTol));
  document.getElementById('btnClose2d').addEventListener('click', () => Pattern2D.hide());
  document.getElementById('btnFit2d').addEventListener('click', () => Pattern2D.fit());
  document.getElementById('chkCut').addEventListener('change', refreshCut);
  ['inpAllowance', 'chkNotches', 'inpNotchSpacing'].forEach((id) =>
    document.getElementById(id).addEventListener('input', refreshCutSoon));
  addEventListener('resize', () => {
    if (!document.getElementById('pattern2d').classList.contains('hidden')) {
      Pattern2D.render();
    }
  });

  // --- Fase 5: exportar
  document.getElementById('btnExport').addEventListener('click', async () => {
    try {
      setStatus('Generando DXF…');
      const { skipped } = await API.exportDXF(exportOpts());
      if (skipped.length) {
        setStatus(`DXF descargado, pero SIN el/los patrón(es) ${skipped.join(', ')} (no se pudieron dibujar)`, true);
      } else {
        setStatus('DXF descargado ✓');
      }
    } catch (err) { setStatus(err.message, true); }
  });
});
