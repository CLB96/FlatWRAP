# FlatWrap

**3D → Patrón 2D para Premium Wrapping** (door panels, instrument panels, armrests).

Las piezas son **plásticos sólidos con espesor** (2–3.5 mm). El flujo:

1. Carga un STL/OBJ de la pieza.
2. **Selecciona la Cara A** (la superficie show donde vive el patrón) con
   clic + relleno por ángulo: un clic toma toda la cara suave y se detiene
   en el canto donde dobla al espesor. Esto es el **patrón primario**.
3. **Marca costuras (opcional)**: una costura que cruza la Cara A la divide
   (un círculo con costura al medio → 2 medias lunas). Sin costura, la
   Cara A se mantiene como un solo patrón.
4. **Genera los patrones**: aplana cada uno con ARAP minimizando distorsión.
5. **Exporta DXF 1:1 (mm)** — verás tantos patrones como haya generado la
   división por costuras.

**Patrón de referencia geométrico** (material idealizado ~1mm sin backing,
sin compensación de estiramiento).

## Arquitectura

```
flatwrap/
├── backend/                  # FastAPI + geometría (corre local)
│   ├── requirements.txt
│   └── app/
│       ├── main.py           # entry point: sirve API + frontend
│       ├── api/routes.py     # endpoints de las 5 fases
│       ├── models/schemas.py # contratos request/response
│       └── core/
│           ├── session.py      # estado en memoria (malla, cara A, costuras, parches)
│           ├── mesh_io.py      # ✅ Carga/limpieza/decimación
│           ├── selection.py    # ✅ Cara A: relleno por ángulo (scipy)
│           ├── geodesics.py    # ✅ Costuras: camino más corto sobre la malla (scipy)
│           ├── segmentation.py # ✅ Subdivisión de la cara A por costuras (scipy)
│           ├── flattening.py   # ✅ ARAP + distorsión + escala 1:1 (libigl)
│           └── export_dxf.py   # ✅ DXF 1:1 con margen y muescas (ezdxf + shapely)
├── frontend/                 # Three.js (lo sirve el backend)
│   ├── index.html
│   ├── css/style.css
│   └── js/
│       ├── api.js            # cliente HTTP
│       ├── viewer.js         # escena 3D, controles CAD, overlay de caras
│       ├── selection.js      # selección de la cara A (flood + pincel)
│       ├── seams.js          # marcado de costuras
│       ├── pattern2d.js      # vista del patrón en canvas (heatmap, escala)
│       └── main.js           # orquestador UI
└── samples/                  # STLs de prueba (make_samples.py) y DXFs
```

## Instalación (Windows, PowerShell)

```powershell
cd "C:\ruta\al\proyecto\flatwrap\backend"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

> Nota: `libigl` y `potpourri3d` instalan wheels precompilados en
> Windows x64 / Python 3.10–3.12 — no requieren compilador.

## Ejecutar

```powershell
cd "C:\ruta\al\proyecto\flatwrap\backend"
.\venv\Scripts\Activate.ps1
uvicorn app.main:app --reload --port 8000
```

Abrir **http://localhost:8000** — el backend sirve también el frontend.

## Controles del visor 3D

| Acción | Control |
|--------|---------|
| Orbitar | arrastre con botón izquierdo |
| Paneo | botón derecho o central |
| Zoom al cursor | rueda del mouse |
| Recentrar pivote de órbita | doble clic sobre la pieza |
| Vistas estándar | botones Iso/Frente/Lado/Planta o teclas 5/1/3/7 |
| Encuadrar pieza | botón ⛶ o tecla F |
| Eje vertical Z↔Y | botón "Z ↑" (Solid Edge exporta Z arriba) |
| Salir del modo costura | Esc |
| Deshacer último punto de costura | Ctrl+Z |

En modo costura, orbitar no marca puntos: solo un clic limpio
(sin arrastre) agrega un punto sobre la superficie.

## Selección de la Cara A

Un clic con **relleno por ángulo** (slider "Ángulo de borde", por defecto
35°) toma toda la superficie suave de la Cara A y se detiene en los cantos
vivos. Afina con el **pincel +/−** (arrastre con botón izquierdo; el derecho
sigue paneando) o usa **Todo** si el STL ya es solo la superficie show.

## Regla de costuras → patrones

La **Cara A seleccionada es el patrón primario**. Las costuras lo subdividen:

`n_patrones = componentes conexas` de la Cara A tras cortar por las costuras:

- Sin costuras → la Cara A se mantiene como **1 patrón**.
- Costura en **loop cerrado** → siempre separa (1 patrón → 2).
- Costura **abierta** → solo separa si sus extremos llegan al borde de la
  Cara A o a otra costura. Si queda flotando, no divide y la app lo reporta
  como warning.
- k costuras bien colocadas → k+1 patrones (o más si se cruzan).

## Exportar STL desde Solid Edge

- Tolerancia de conversión (desviación): **0.05–0.1 mm**
- Unidades: **mm**
- Exporta la **pieza sólida completa** (con espesor): dentro de FlatWrap
  seleccionas la Cara A. No hace falta aislar la superficie en el CAD.

## Roadmap

| Paso | Alcance | Estado |
|------|---------|--------|
| 1 | Cargar STL + visor 3D (controles CAD) + info de malla | ✅ Funcional |
| 2 | Seleccionar Cara A (relleno por ángulo + pincel) | ✅ Funcional |
| 3 | Costuras sobre superficie (clic → camino más corto) | ✅ Funcional |
| 4 | Subdivisión de la Cara A + aplanado ARAP + heatmap | ✅ Funcional |
| 5 | DXF 1:1 con margen de costura y muescas emparejadas | ✅ Funcional |

## Tests

```powershell
cd backend
.\venv\Scripts\python.exe -m pytest tests\ -v
```

Los tests usan un medio cilindro como oráculo geométrico: es una
superficie desarrollable cuyo patrón exacto es un rectángulo de
π·r × L, así que cualquier desviación delata un error del algoritmo.
También verifican que el relleno por ángulo aísle la Cara A en una pieza
sólida (sin tomar paredes ni reverso) y que una costura divida el patrón.

`samples/make_samples.py` regenera los STLs de prueba:
- `medio_cilindro.stl` — oráculo desarrollable
- `silla_montar.stl` — doble curvatura (distorsión inevitable, heatmap rojo)
- `panel_plastico.stl` — pieza sólida con espesor 3 mm (Cara A + paredes + reverso)
