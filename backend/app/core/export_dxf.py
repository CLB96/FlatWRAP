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


def _patch_polygon(
    uv: np.ndarray, faces: np.ndarray, seam_local: set[int] | None = None
) -> tuple[Polygon, list]:
    """Polígono shapely (exterior + agujeros) del parche en UV.

    Si se pasa `seam_local`, los tramos de costura del borde se enderezan a
    rectas (para que dos parches vecinos casen limpios en AutoCAD).
    """
    loops = _boundary_loops(faces)
    if not loops:
        raise ExportError("Un parche no tiene borde: no se puede exportar")
    if seam_local:
        loops = [_straighten_seam_run(loop, seam_local) for loop in loops]
        loops = [loop for loop in loops if len(loop) >= 3]
    rings = [uv[loop] for loop in loops]
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
    # Enderezar la costura (Camino B) solo cuando el UV está indexado por vértice
    # original (relax/project): ahí el borde local mapea 1:1 a la malla. En
    # 'unfold' el net reindexa, así que no se endereza (no aplica).
    seam_local = None
    if len(uv) == len(np.asarray(patch["mesh"].vertices)):
        seam_local = _seam_local_vertices(patch)
    polygon, rings = _patch_polygon(uv, faces, seam_local)

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
