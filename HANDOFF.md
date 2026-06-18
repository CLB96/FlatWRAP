# FlatWrap — Handoff para continuar

> App 3D→patrón 2D para **premium wrapping** de piezas automotrices.
> Estado: **EN VIVO**, estable, 30 tests verdes. Este doc es para retomar en una sesión nueva.

---

## 🎯 TAREA INMEDIATA (lo que sigue)

**HECHO (MEJORA-22): el corte de costuras REALES ya sigue la geodésica suave, sin
zigzag.** Camino A implementado en `backend/app/core/export_dxf.py`. Lo que sigue
ahora son los pendientes de "Otros pendientes" (auto-re-pegar parches de unión,
`FLATWRAP_PASSWORD`, sesión-por-navegador). Detalle del fix abajo por si hay que
ajustarlo.

### Qué se hizo (Camino A — mapear la geodésica al UV 2D)
- Por cada punto de `Seam.polyline` (3D, geodésica pp3d suave), se halla el triángulo
  del parche que lo contiene (baricéntrica) y se aplica esa misma baricéntrica al UV
  2D de ese triángulo → posición 2D suave. En `export_dxf._bary_map_point` /
  `_map_polyline_to_uv` / `_real_seams_2d` / `_smooth_real_seam_runs`.
- En el contorno (`_patch_polygon`) el tramo de costura REAL (jagged, por aristas) se
  REEMPLAZA por esa polilínea 2D suave. Las de UNIÓN (`straighten=True`) se siguen
  enderezando a recta (Camino B, intacto). Solo aplica con UV por-vértice
  (relax/project; guarda `len(uv)==len(mesh.vertices)`; en 'unfold' no aplica).
- **CLAVE / por qué NO se usó la restricción a caras del edge_path**: la búsqueda del
  triángulo contenedor barre TODAS las caras del parche cercanas a la geodésica (con
  prefiltro por bbox), NO solo las del corte por aristas. En mallas irregulares (STEP
  real) el edge_path Dijkstra zigzaguea a escala de triángulo PERO sigue de cerca a la
  geodésica → el mapeo cae perfecto (dev ~0). En una GRILLA axis-aligned el edge_path
  diverge muchísimo de una diagonal (artefacto de grilla) → por eso el test usa una
  lámina plana IRREGULAR (Delaunay), no el medio cilindro.
- Tests nuevos (2, en `tests/test_pipeline.py`): `test_real_seam_cut_follows_geodesic_
  not_jagged` (sigue la recta, quita el zigzag de alta frecuencia) y
  `test_real_seam_contour_not_straightened_like_union` (conserva la curva, no la
  colapsa a recta como la de unión). 30/30 verdes.
- **OJO**: NO mueve físicamente el corte a la geodésica (eso requeriría re-cortar la
  malla = el imprint 3D de `seam_cut.py`, revertido por frágil). El corte sigue en la
  frontera del parche (edge_path); el suavizado lo "limpia" hacia la geodésica con
  clamping al triángulo del parche. En mallas densas reales coinciden ~1 triángulo.

---

## 🏗️ Cómo correr / desplegar

- **Local**: `cd backend; .\venv\Scripts\python.exe -m uvicorn app.main:app --port 8000`
  → http://localhost:8000  (estado SESSION en RAM; se pierde al reiniciar → recargar STL).
  En esta máquina se levanta con la herramienta **preview** (launch.json, name "flatwrap").
- **Tests**: `cd backend; .\venv\Scripts\python.exe -m pytest tests/ -q`  (28 pasan).
  Usar `$env:PYTHONIOENCODING="utf-8"` en PowerShell por los caracteres acentuados.
- **Repo**: github.com/CLB96/FlatWRAP (rama main). `gh` autenticado como CLB96.
- **Producción EN VIVO**: Railway → **https://flatwrap.up.railway.app** (auto-deploy en
  cada push a main; ~1-2 min de build). Falta poner env `FLATWRAP_PASSWORD` (hoy ABIERTO).
- **Frontend**: sin build; scripts con `?v=N` para cache-bust. **Al editar JS/CSS subir
  N** en index.html (hoy en **v20**).
- **Notas**: el screenshot del visor se cuelga en mallas pesadas (>30k) — verificar con
  `preview_eval` numérico. La pieza de prueba del usuario: `samples/C1YC armrest fr.stp`
  (27MB, STEP; carga ~12s; Cara A ≈ 35k caras con MUCHOS huecos reales).

---

## 📦 Arquitectura (backend/app/core)
- `mesh_io.py` — carga STL/OBJ/PLY/GLB/3MF/OFF + STEP/STP (cascadio→glb, ×1000). NO
  decima. `fix_normals` al cargar. NO quita caras non-manifold de SESSION.mesh (eso se
  hace en copia para geodésicas — MEJORA-17).
- `selection.py` — flood Cara A: `angle_deg` (canto) + `side_deg` (no cruzar reverso,
  default 85). Pincel círculo/esfera. Respeta costuras.
- `geodesics.py` — costura = **GEODÉSICA exacta** (pp3d) sobre copia MANIFOLD
  (`_manifold_copy` + vmap; pp3d no acepta non-manifold). edge_path Dijkstra para corte.
- `segmentation.py` — divide Cara A por costuras (componentes conexas). `_fill_selection_holes`.
- `flattening.py` — `relax`=SLIM (DEFAULT, init Tutte uniforme + converge), `unfold`,
  `project`. **Rellena huecos interiores SOLO para el solve** (MEJORA-18, all-or-nothing).
  Expone s1/s2 por cara (tolerancia). Mapa de tensión.
- `seam_cut.py` — imprint geodésico (split triángulos). **NO integrado** (frágil en 3D);
  útil su mapeo punto→triángulo para la tarea de arriba.
- `export_dxf.py` — DXF 1:1, capas CORTE/COSTURA/MUESCA/TEXTO. **Camino B**: endereza
  el tramo de costura de las costuras de UNIÓN (`straighten=True`).

## ✨ Frontend (frontend/js)
- `viewer.js` — Three.js. Luz PAREJA (MEJORA-15). **Feature edges** (aristas de cambio
  de ángulo, toggle + slider). `pattern2d.js` — mapa 2D + **modo tolerancia** (verde/rojo
  vs warp/weft/compr de la TDS). `seams.js` — lista de costuras con toggle "unión (recta)".

---

## 📜 Historial de mejoras (resumen; detalle en memoria `flatwrap-unfold-plan.md`)
- M12: relax aplana discos que envuelven (Tutte + converger).
- M13: flood curvatura-aware (REVERTIDO en M14).
- M14: costura RECTA pegada a superficie (revert del imprint frágil).
- M15: luz pareja.
- M16: malla manifold + geodésica exacta restaurada.
- M17: feature edges en visor + decouple manifold (no muta SESSION.mesh).
- M18: contracción de relax = HUECOS → rellenar para el solve.
- M19: **modo tolerancia** material (warp/weft/compresión TDS → verde/rojo). Default 15%.
- M20: **Camino B** — costura recta en el DXF (enderezar tramo de costura).
- M21: **straighten por-costura** (unión vs real). Endpoint `/api/seams/{id}/straighten`.
- M22: **costura REAL = geodésica suave en el DXF** (Camino A). Mapea `Seam.polyline`
  al UV 2D del parche por baricéntrica y reemplaza el tramo jagged del contorno. Solo
  backend/export_dxf.py; sin cambios de frontend. 30/30 tests. Ver arriba.
- **→ SIGUE**: pendientes menores (auto-re-pegar parches de unión, FLATWRAP_PASSWORD,
  sesión-por-navegador). Ver "Otros pendientes".

## ⏳ Otros pendientes (menores, acordados)
- Auto-RE-PEGAR los 2 parches de una costura de UNIÓN en UN solo patrón en el DXF
  (hoy el usuario los une a mano en AutoCAD).
- Poner `FLATWRAP_PASSWORD` en Railway (la app está abierta).
- Sesión-por-navegador para ≥5 usuarios simultáneos (hoy SESSION global = 1 a la vez).
