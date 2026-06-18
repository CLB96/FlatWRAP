"""Fase 5 - Exportación DXF escala 1:1 (mm). FUNCIONAL.

Capas generadas:
    CORTE      -> contorno con margen de costura (línea continua)
    COSTURA    -> línea de costura neta (discontinua)
    MUESCAS    -> marcas de alineación de 4 mm + número
    TEXTO      -> id de parche, dimensiones, fecha

Las muescas se colocan a lo largo de cada costura, a intervalos de
longitud de arco 3D idénticos en AMBOS parches que comparten la costura,
con el mismo número (s{costura}.{n}) — así las piezas casan al coser.

ezdxf con $INSUNITS=4 (mm) -> compatible con plotters de corte y
digitalización manual a escala.
"""

from datetime import date
from pathlib import Path

import ezdxf
import numpy as np
from shapely.geometry import Point, Polygon

from app.core.session import SESSION

OUTPUT_DIR = Path(__file__).resolve().parents[3] / "samples"
NOTCH_LENGTH_MM = 4.0
TEXT_HEIGHT_MM = 8.0
LABEL_HEIGHT_MM = 3.0


class ExportError(Exception):
    """No se puede exportar (faltan parches aplanados, etc.)."""


# ----------------------------------------------------------- geometría 2D
def _boundary_loops(faces: np.ndarray) -> list[list[int]]:
    """Loops de borde (vértices locales ordenados) de una malla de parche.

    Camina los bordes CONSUMIENDO cada arista una sola vez, así siempre
    termina aunque el borde sea no-manifold (vértices con >2 aristas de
    borde, T-junctions) — situación habitual en piezas reales que antes
    colgaba el exportador en un bucle infinito.
    """
    edges = np.sort(
        np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]),
        axis=1,
    )
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    boundary_edges = uniq[counts == 1]
    if len(boundary_edges) == 0:
        return []

    # vértice -> lista de (vecino, id_arista)
    incident: dict[int, list[tuple[int, int]]] = {}
    for ei, (a, b) in enumerate(boundary_edges):
        incident.setdefault(int(a), []).append((int(b), ei))
        incident.setdefault(int(b), []).append((int(a), ei))

    used = [False] * len(boundary_edges)
    loops = []
    for start_ei in range(len(boundary_edges)):
        if used[start_ei]:
            continue
        a, b = int(boundary_edges[start_ei][0]), int(boundary_edges[start_ei][1])
        used[start_ei] = True
        loop = [a, b]
        current = b
        while current != a:
            nxt = next(((o, ei) for o, ei in incident[current] if not used[ei]), None)
            if nxt is None:
                break  # cadena de borde abierta: la dejamos como está
            used[nxt[1]] = True
            current = nxt[0]
            if current != a:
                loop.append(current)
        if len(loop) >= 3:
            loops.append(loop)
    return loops


def _seam_local_vertices(patch: dict) -> set[int]:
    """Vértices LOCALES del parche que están sobre alguna costura.

    El borde del parche a lo largo de una costura está formado por estos
    vértices; sirve para enderezar ese tramo a una recta (Camino B).
    """
    seam_globals: set[int] = set()
    for seam in SESSION.seams.values():
        if not getattr(seam, "straighten", False):
            continue   # costura REAL: conserva su curva (no se endereza)
        for a, b in seam.edge_path:
            seam_globals.add(int(a))
            seam_globals.add(int(b))
    if not seam_globals:
        return set()
    g2l = {int(g): i for i, g in enumerate(np.asarray(patch["orig_vertex_ids"]))}
    return {g2l[g] for g in seam_globals if g in g2l}


def _straighten_seam_run(loop: list[int], seam_local: set[int]) -> list[int]:
    """Colapsa cada tramo de costura del borde a una RECTA: quita los vértices
    de costura INTERIORES (ambos vecinos también costura), dejando solo los
    extremos del tramo. Así el contorno va derecho de un extremo al otro de la
    costura, sin importar por dónde zigzaguean los triángulos.
    """
    n = len(loop)
    if n < 3 or not any(v in seam_local for v in loop):
        return loop
    keep = []
    for i in range(n):
        v = loop[i]
        if v not in seam_local:
            keep.append(v)
            continue
        prev_s = loop[(i - 1) % n] in seam_local
        next_s = loop[(i + 1) % n] in seam_local
        if not (prev_s and next_s):   # extremo de tramo: conservar
            keep.append(v)
    return keep if len(keep) >= 3 else loop


# ------------------------------------- Camino A: costura REAL = geodésica suave
def _bary_map_point(P, A, B, C, uvA, uvB, uvC):
    """Mapea el punto 3D `P` al 2D usando coords baricéntricas en el triángulo
    candidato que mejor lo contiene (sobre el plano y dentro del triángulo).

    Vectorizado sobre los triángulos candidatos. Devuelve el punto 2D: la MISMA
    baricéntrica de P en su triángulo 3D aplicada a los 3 UV de ese triángulo.
    Como un punto de la geodésica vive sobre la superficie, el triángulo correcto
    tiene distancia-al-plano ~0 y baricéntrica ≥ 0; los demás se penalizan.
    """
    v0, v1, ap = B - A, C - A, P - A
    d00 = np.einsum("ij,ij->i", v0, v0)
    d01 = np.einsum("ij,ij->i", v0, v1)
    d11 = np.einsum("ij,ij->i", v1, v1)
    d20 = np.einsum("ij,ij->i", ap, v0)
    d21 = np.einsum("ij,ij->i", ap, v1)
    denom = d00 * d11 - d01 * d01
    denom = np.where(np.abs(denom) < 1e-18, 1e-18, denom)
    wb = (d11 * d20 - d01 * d21) / denom        # peso del vértice B
    wc = (d00 * d21 - d01 * d20) / denom        # peso del vértice C
    wa = 1.0 - wb - wc                          # peso del vértice A
    normal = np.cross(v0, v1)
    nn = np.linalg.norm(normal, axis=1)
    nn = np.where(nn < 1e-18, 1e-18, nn)
    plane_dist = np.abs(np.einsum("ij,ij->i", ap, normal)) / nn
    outside = np.maximum(0, -wa) + np.maximum(0, -wb) + np.maximum(0, -wc)
    # outside es fracción (adimensional); a mm con una constante >> tamaño pieza
    k = int(np.argmin(plane_dist + outside * 1e4))
    a, b, c = max(wa[k], 0.0), max(wb[k], 0.0), max(wc[k], 0.0)
    s = a + b + c
    a, b, c = a / s, b / s, c / s               # s>0 siempre (suman 1)
    return a * uvA[k] + b * uvB[k] + c * uvC[k]


def _map_polyline_to_uv(seam, patch: dict, uv: np.ndarray):
    """Geodésica 3D de la costura (`seam.polyline`) mapeada al UV 2D del parche.

    Solo válido cuando uv está indexado por vértice local (relax/project):
    ahí uv[v] es el 2D del vértice local v y las caras locales indexan uv.
    Devuelve Nx2 (suave) o None si no aplica (otra indexación / vacío).

    El triángulo que contiene cada punto se busca entre TODAS las caras del
    parche cercanas a la geodésica (no solo las del corte por aristas): en mallas
    irregulares la geodésica suave se aleja del edge_path jagged varias aristas,
    y restringir a las del corte arrastraría los puntos de vuelta al zigzag.
    """
    V = np.asarray(patch["mesh"].vertices, dtype=float)
    F = np.asarray(patch["mesh"].faces)
    if len(V) != len(uv) or len(F) == 0:
        return None
    poly3d = np.asarray(seam.polyline, dtype=float)
    if len(poly3d) < 2:
        return None

    # Prefiltro por bounding box (con margen de varias aristas) para no testear
    # las decenas de miles de caras del parche en cada punto.
    edge_len = float(np.median(np.linalg.norm(V[F[:, 0]] - V[F[:, 1]], axis=1)))
    margin = max(5.0 * edge_len, 1e-6)
    lo, hi = poly3d.min(axis=0) - margin, poly3d.max(axis=0) + margin
    in_box = np.all((V >= lo) & (V <= hi), axis=1)
    cand = np.flatnonzero(in_box[F].any(axis=1))
    if cand.size == 0:
        cand = np.arange(len(F))

    tri = F[cand]
    A, B, C = V[tri[:, 0]], V[tri[:, 1]], V[tri[:, 2]]
    uvA, uvB, uvC = uv[tri[:, 0]], uv[tri[:, 1]], uv[tri[:, 2]]
    return np.asarray(
        [_bary_map_point(P, A, B, C, uvA, uvB, uvC) for P in poly3d], dtype=float
    )


def _real_seams_2d(patch: dict, uv: np.ndarray) -> list[dict]:
    """Costuras REALES (straighten=False) presentes en el parche, con su tramo de
    borde (vértices locales) y su geodésica suave ya mapeada a 2D.
    """
    orig = np.asarray(patch["orig_vertex_ids"])
    g2l = {int(g): i for i, g in enumerate(orig)}
    out = []
    for seam in SESSION.seams.values():
        if getattr(seam, "straighten", False):
            continue
        local = {g2l[int(v)] for ab in seam.edge_path for v in ab if int(v) in g2l}
        if len(local) < 2:
            continue
        poly2d = _map_polyline_to_uv(seam, patch, uv)
        if poly2d is None or len(poly2d) < 2:
            continue
        out.append({"local": local, "poly2d": poly2d})
    return out


def _orient_poly(poly2d: np.ndarray, p_start, p_end) -> list:
    """Orienta la polilínea suave para que vaya del extremo `p_start` al `p_end`
    del tramo de borde que reemplaza (la geodésica va waypoint0->waypointN)."""
    fwd = np.linalg.norm(poly2d[0] - p_start) + np.linalg.norm(poly2d[-1] - p_end)
    rev = np.linalg.norm(poly2d[0] - p_end) + np.linalg.norm(poly2d[-1] - p_start)
    return list(poly2d if fwd <= rev else poly2d[::-1])


def _smooth_real_seam_runs(loop: list[int], uv: np.ndarray, real_seams: list[dict]):
    """Sustituye en el borde cada tramo de costura REAL (jagged, por aristas) por
    su geodésica suave mapeada a 2D. Devuelve un anillo de PUNTOS 2D (Nx2)."""
    v2seam: dict[int, int] = {}
    for si, rs in enumerate(real_seams):
        for v in rs["local"]:
            v2seam.setdefault(v, si)
    if not any(v in v2seam for v in loop):
        return uv[loop]
    n = len(loop)
    start = next((i for i in range(n) if loop[i] not in v2seam), None)
    if start is None:        # el borde entero ES una costura cerrada
        return real_seams[v2seam[loop[0]]]["poly2d"]
    order = list(range(start, n)) + list(range(start))
    out: list = []
    i = 0
    while i < n:
        v = loop[order[i]]
        si = v2seam.get(v)
        if si is None:
            out.append(uv[v])
            i += 1
            continue
        run = []
        while i < n and v2seam.get(loop[order[i]]) == si:
            run.append(loop[order[i]])
            i += 1
        if len(run) >= 2:    # reemplazar el tramo jagged por la geodésica suave
            out.extend(_orient_poly(real_seams[si]["poly2d"], uv[run[0]], uv[run[-1]]))
        else:
            out.extend(uv[w] for w in run)
    return np.asarray(out, dtype=float)


def _patch_polygon(
    uv: np.ndarray,
    faces: np.ndarray,
    union_local: set[int] | None = None,
    real_seams: list[dict] | None = None,
) -> tuple[Polygon, list]:
    """Polígono shapely (exterior + agujeros) del parche en UV.

    - `union_local`: tramos de costura de UNIÓN del borde -> se enderezan a recta
      (Camino B; dos parches vecinos casan plano en AutoCAD).
    - `real_seams`: costuras REALES -> su tramo jagged del borde se reemplaza por
      la geodésica suave que dibujó el usuario (Camino A), sin zigzag.
    """
    loops = _boundary_loops(faces)
    if not loops:
        raise ExportError("Un parche no tiene borde: no se puede exportar")
    if union_local:
        loops = [_straighten_seam_run(loop, union_local) for loop in loops]
        loops = [loop for loop in loops if len(loop) >= 3]
    if real_seams:
        rings = [_smooth_real_seam_runs(loop, uv, real_seams) for loop in loops]
    else:
        rings = [uv[loop] for loop in loops]
    rings = [np.asarray(r, dtype=float) for r in rings if len(r) >= 3]
    if not rings:
        raise ExportError("Un parche no tiene borde válido: no se puede exportar")
    rings.sort(key=lambda r: -abs(Polygon(r).area))
    polygon = Polygon(rings[0], [r for r in rings[1:]])
    if not polygon.is_valid:
        polygon = polygon.buffer(0)  # reparar auto-intersecciones menores
    return polygon, rings


def _interpolate_along(points: np.ndarray, lengths: np.ndarray, s: float):
    """Punto y dirección en la polilínea `points` a longitud de arco `s`.

    `lengths` son las longitudes acumuladas (mismo tamaño que points),
    medidas en 3D para que coincidan entre los dos parches de la costura.
    """
    i = int(np.searchsorted(lengths, s, side="right")) - 1
    i = max(0, min(i, len(points) - 2))
    seg = lengths[i + 1] - lengths[i]
    t = 0.0 if seg <= 0 else (s - lengths[i]) / seg
    point = points[i] + t * (points[i + 1] - points[i])
    direction = points[i + 1] - points[i]
    norm = np.linalg.norm(direction)
    return point, (direction / norm if norm > 0 else np.array([1.0, 0.0]))


def _seam_notches(
    seam, patch: dict, polygon: Polygon, spacing_mm: float
) -> list[dict]:
    """Muescas de la costura dentro de un parche, numeradas por estación."""
    chain = [v for pair in seam.edge_path for v in pair]
    chain = list(dict.fromkeys(chain))  # vértices en orden, sin repetidos
    if len(chain) < 2:
        return []

    # ¿La costura vive completa en este parche?
    orig_ids = patch["orig_vertex_ids"]
    positions = np.searchsorted(orig_ids, chain)
    if np.any(positions >= len(orig_ids)) or np.any(orig_ids[positions] != chain):
        return []

    # orig_to_uv mapea vértice local original -> 2D (en 'unfold' uv está
    # reindexado por el net, así que NO sirve indexar uv directamente).
    uv_chain = patch.get("orig_to_uv", patch["uv"])[positions]
    mesh_3d = np.asarray(SESSION.mesh.vertices)[chain]
    lengths_3d = np.concatenate(
        [[0.0], np.cumsum(np.linalg.norm(np.diff(mesh_3d, axis=0), axis=1))]
    )
    total = float(lengths_3d[-1])

    notches = []
    station, k = spacing_mm, 1
    while station < total - spacing_mm * 0.25:
        point, direction = _interpolate_along(uv_chain, lengths_3d, station)
        normal = np.array([-direction[1], direction[0]])
        # La muesca apunta hacia FUERA del parche (hacia el margen)
        if polygon.contains(Point(*(point + normal * 0.5))):
            normal = -normal
        notches.append(
            {
                "start": point,
                "end": point + normal * NOTCH_LENGTH_MM,
                "label": f"s{seam.seam_id}.{k}",
            }
        )
        station += spacing_mm
        k += 1
    return notches


# ----------------------------------------------------------------- DXF
def _setup_document() -> tuple:
    doc = ezdxf.new("R2010", setup=True)  # setup=True carga linetype DASHED
    doc.header["$INSUNITS"] = 4  # milímetros
    doc.layers.add("CORTE", color=7)
    doc.layers.add("COSTURA", color=1, linetype="DASHED")
    doc.layers.add("MUESCAS", color=3)
    doc.layers.add("TEXTO", color=4)
    return doc, doc.modelspace()


def _draw_ring(msp, ring: np.ndarray, layer: str) -> None:
    msp.add_lwpolyline(
        [(float(x), float(y)) for x, y in ring],
        close=True,
        dxfattribs={"layer": layer},
    )


def _draw_patch(msp, pid, patch, seam_allowance_mm, add_notches, notch_spacing_mm) -> None:
    """Dibuja un parche completo (costura, corte, muescas, etiqueta)."""
    uv = np.asarray(patch["uv"])
    # faces_2d indexa a uv (en 'unfold' el net duplica vértices en los cortes;
    # en project/arap es la malla original). Fallback a la malla por si acaso.
    faces = np.asarray(patch.get("faces_2d", patch["mesh"].faces))
    # Procesar costuras solo cuando el UV está indexado por vértice original
    # (relax/project): ahí el borde local mapea 1:1 a la malla y uv[v] es el 2D
    # del vértice v. En 'unfold' el net reindexa (no aplica). Costuras de UNIÓN
    # se enderezan a recta (Camino B); costuras REALES siguen la geodésica suave
    # dibujada por el usuario, sin zigzag (Camino A).
    union_local: set[int] = set()
    real_seams: list[dict] = []
    if len(uv) == len(np.asarray(patch["mesh"].vertices)):
        union_local = _seam_local_vertices(patch)
        real_seams = _real_seams_2d(patch, uv)
    polygon, rings = _patch_polygon(uv, faces, union_local, real_seams)

    # Línea de costura neta (discontinua)
    for ring in rings:
        _draw_ring(msp, ring, "COSTURA")

    # Contorno de corte con margen (mitre para conservar esquinas)
    offset = polygon.buffer(seam_allowance_mm, join_style="mitre", mitre_limit=2.0)
    geoms = getattr(offset, "geoms", [offset])
    for geom in geoms:
        _draw_ring(msp, np.asarray(geom.exterior.coords), "CORTE")
        for interior in geom.interiors:
            _draw_ring(msp, np.asarray(interior.coords), "CORTE")

    # Muescas de alineación emparejadas por costura
    if add_notches:
        for seam in SESSION.seams.values():
            for notch in _seam_notches(seam, patch, polygon, notch_spacing_mm):
                msp.add_line(
                    tuple(notch["start"]),
                    tuple(notch["end"]),
                    dxfattribs={"layer": "MUESCAS"},
                )
                msp.add_text(
                    notch["label"],
                    height=LABEL_HEIGHT_MM,
                    dxfattribs={"layer": "MUESCAS"},
                ).set_placement(tuple(notch["end"] + (notch["end"] - notch["start"])))

    # Etiqueta del parche en su centroide (ASCII: máxima compatibilidad CAM)
    result = patch.get("flatten_result", {})
    label = (
        f"PATRON {pid}  -  {result.get('width_mm', '?')} x "
        f"{result.get('height_mm', '?')} mm"
    )
    msp.add_text(
        label, height=TEXT_HEIGHT_MM, dxfattribs={"layer": "TEXTO"}
    ).set_placement((polygon.centroid.x, polygon.centroid.y))


def export_patterns(
    seam_allowance_mm: float = 10.0,
    add_notches: bool = True,
    notch_spacing_mm: float = 50.0,
) -> str:
    """Genera el DXF 1:1 con todos los parches aplanados."""
    patches = {
        pid: p for pid, p in SESSION.patches.items() if p.get("uv") is not None
    }
    if not patches:
        raise ExportError(
            "No hay parches aplanados: ejecuta 'Segmentar' y 'Aplanar todo' "
            "antes de exportar."
        )

    doc, msp = _setup_document()

    n = 0
    for pid, patch in sorted(patches.items()):
        # Un parche problemático (borde degenerado, polígono inválido) NO debe
        # tumbar ni colgar todo el export: lo saltamos y seguimos.
        try:
            _draw_patch(msp, pid, patch, seam_allowance_mm, add_notches, notch_spacing_mm)
            n += 1
        except Exception:
            continue

    if n == 0:
        raise ExportError(
            "No se pudo generar el contorno de ningún parche "
            "(bordes degenerados tras el aplanado)."
        )
    msp.add_text(
        f"FlatWrap - {n} patron(es) - escala 1:1 (mm) - "
        f"margen {seam_allowance_mm} mm - {date.today()}",
        height=TEXT_HEIGHT_MM,
        dxfattribs={"layer": "TEXTO"},
    ).set_placement((0, -seam_allowance_mm - TEXT_HEIGHT_MM * 3))

    OUTPUT_DIR.mkdir(exist_ok=True)
    path = OUTPUT_DIR / "flatwrap_pattern.dxf"
    doc.saveas(path)
    return str(path)
