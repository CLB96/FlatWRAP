# FlatWrap — Handoff para continuar

> App 3D→patrón 2D para **premium wrapping** de piezas automotrices.
> Estado: **EN VIVO**, estable, 28 tests verdes. Este doc es para retomar en una sesión nueva.

---

## 🎯 TAREA INMEDIATA (lo que sigue)

**Suavizar el corte de las costuras REALES (no-unión) para que sigan la curva
geodésica dibujada, sin zigzag.**

- **Síntoma**: una costura marcada como REAL (checkbox "unión (recta)" SIN marcar)
  conserva su forma, pero su corte en el DXF sale **zigzagueado** (sigue las aristas
  de la malla) en zonas donde el usuario dibujó una **curva suave**.
- **Objetivo** (sketch del usuario = línea roja): el corte debe seguir la **línea
  geodésica suave** que él trazó al clickear, no el zigzag de los triángulos.

### Datos clave del problema
- La costura dibujada (lo que el usuario VE y quiere) = `Seam.polyline` — puntos 3D
  de la **geodésica exacta** (pp3d), suave. Está en `backend/app/core/session.py`.
- El CORTE actual sigue `Seam.edge_path` — cadena de aristas de malla (Dijkstra),
  **jagged**. La segmentación corta por ahí.
- `Seam.straighten` (bool, default False): True = costura de UNIÓN (se endereza a
  recta en el DXF, MEJORA-21). False = REAL (hoy queda jagged → ESTE es el problema).

### Enfoques para resolverlo (recomendado: A)
**A) Mapear la geodésica `polyline` al UV 2D del parche (la solución correcta).**
   - Por cada punto de `Seam.polyline` (3D, sobre la superficie), hallar el triángulo
     del parche que lo contiene y su coordenada baricéntrica → aplicar la MISMA
     baricéntrica al UV 2D del triángulo → posición 2D suave.
   - Reemplazar el tramo de costura jagged del contorno (en `export_dxf`) por esta
     polilínea 2D suave (solo para costuras REALES; las de unión ya van rectas).
   - **REUSAR `backend/app/core/seam_cut.py`**: ya tiene la infraestructura que mapea
     cada punto de la geodésica pp3d a su triángulo/arista (`_corridor_segment`,
     `imprint_geodesic`, `_classify_points`, `_global_point_edge`). Eso resuelve el
     "qué triángulo contiene cada punto" sin rtree. NO está integrado (se revirtió el
     imprint 3D porque modificar la malla era frágil), pero el mapeo punto→triángulo
     SÍ sirve para esto (que es solo 2D, no toca la malla → mucho más robusto).
   - Ventaja: opera en la SALIDA 2D, no en la topología → robusto (igual que Camino B).

**B) Suavizar/simplificar el contorno jagged (pragmático, más simple).**
   - Douglas–Peucker sobre el tramo de costura + conservar los waypoints clickeados
     (`Seam.vertex_ids`). Quita el zigzag de alta frecuencia pero NO sigue la
     geodésica exacta. Más rápido, menos fiel.

### Dónde tocar (Camino A)
- `backend/app/core/export_dxf.py`: hoy `_seam_local_vertices` + `_straighten_seam_run`
  enderezan costuras de UNIÓN. Agregar, para costuras REALES, un reemplazo del tramo
  de costura del contorno por la geodésica mapeada a 2D.
- Necesitas el UV por vértice del parche (`patch["uv"]` indexado por vértice original
  en relax/project; ver guarda `len(uv)==len(mesh.vertices)`).
- Mapeo 3D→2D: baricéntrica del punto de `polyline` en su triángulo del parche →
  combinar los 3 UV de ese triángulo.
- Tests: agregar uno que verifique que el contorno de una costura REAL sigue la
  geodésica (p.ej. distancia máx del contorno a la polilínea < umbral) y que NO se
  endereza a recta.

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
- **→ SIGUE**: suavizar el corte de costuras REALES a la geodésica (sin zigzag). Ver arriba.

## ⏳ Otros pendientes (menores, acordados)
- Auto-RE-PEGAR los 2 parches de una costura de UNIÓN en UN solo patrón en el DXF
  (hoy el usuario los une a mano en AutoCAD).
- Poner `FLATWRAP_PASSWORD` en Railway (la app está abierta).
- Sesión-por-navegador para ≥5 usuarios simultáneos (hoy SESSION global = 1 a la vez).
