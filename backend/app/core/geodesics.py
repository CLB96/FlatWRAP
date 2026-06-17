"""Fase 2 - Costuras como GEODÉSICAS rectas sobre la superficie. FUNCIONAL.

La costura que el usuario VE es la GEODÉSICA exacta entre sus clics (la línea más
recta sobre la superficie, que cruza triángulos): recta en lo plano, círculo
máximo en lo redondo — como la espera el usuario. Requiere malla manifold;
`mesh_io` ya quita las pocas caras no-manifold del teselado de STEP que antes
hacían fallar el solver. Si aun así falla, cae a una RECTA pegada a la superficie.

Para el CORTE interno (segmentación, que opera sobre aristas) se mantiene en
paralelo el camino más corto por aristas (Dijkstra). El corte exacto por la
línea (partir triángulos) es trabajo aparte (ver app/core/seam_cut.py).
"""

import numpy as np
import potpourri3d as pp3d
import scipy.sparse as sp
from scipy.sparse.csgraph import dijkstra

from app.core.session import SESSION, Seam


class SeamError(Exception):
    """Error de usuario al trazar una costura (vértices inválidos, etc.)."""


# Caches indexados por versión de malla (no por id(mesh): ver Session.mesh_version)
_graph_cache: dict = {"version": None, "graph": None}
_geo_cache: dict = {"version": None, "solver": None, "vmap": None}


def _manifold_copy(mesh):
    """Copia MANIFOLD (sin las caras que tocan una arista de >2 caras) + remap
    de vértices (id original -> id en la copia, o -1 si quedó sin referenciar).

    Se construye SOLO para el solver de geodésicas; la malla completa (SESSION
    .mesh) no se toca, para no meter huecos en el aplanado.
    """
    from collections import Counter

    V = np.asarray(mesh.vertices)
    F = np.asarray(mesh.faces)
    counts = Counter(map(tuple, np.sort(mesh.edges, axis=1)))
    bad = {e for e, c in counts.items() if c > 2}
    if bad:
        keep = np.ones(len(F), dtype=bool)
        for fi, (a, b, c) in enumerate(F):
            for x, y in ((a, b), (b, c), (c, a)):
                if (min(x, y), max(x, y)) in bad:
                    keep[fi] = False
                    break
        F = F[keep]
    used = np.unique(F)
    remap = np.full(len(V), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return V[used], remap[F], remap


def _geodesic_solver(mesh):
    """Solver de geodésicas exactas (EdgeFlip) sobre una copia manifold,
    cacheado por malla. Devuelve (solver, vmap) o (None, None)."""
    if _geo_cache["version"] == SESSION.mesh_version:
        return _geo_cache["solver"], _geo_cache["vmap"]
    solver, vmap = None, None
    try:
        V2, F2, remap = _manifold_copy(mesh)
        solver = pp3d.EdgeFlipGeodesicSolver(
            np.asarray(V2, dtype=np.float64), np.asarray(F2, dtype=np.int64)
        )
        vmap = remap
    except Exception:
        solver, vmap = None, None
    _geo_cache.update(version=SESSION.mesh_version, solver=solver, vmap=vmap)
    return solver, vmap


def _geodesic_polyline(mesh, waypoints: list[int]) -> list[list[float]]:
    """Geodésica exacta que pasa por los waypoints (para DIBUJAR). Cae a la recta
    pegada a la superficie si el solver no está disponible o un waypoint quedó
    fuera de la copia manifold."""
    solver, vmap = _geodesic_solver(mesh)
    if solver is None:
        return _straight_surface_polyline(mesh, waypoints)
    try:
        mapped = [int(vmap[int(w)]) for w in waypoints]
        if any(m < 0 for m in mapped):
            return _straight_surface_polyline(mesh, waypoints)
        poly: list[list[float]] = []
        for a, b in zip(mapped, mapped[1:]):
            seg = np.asarray(solver.find_geodesic_path(v_start=a, v_end=b))
            if seg.size == 0:
                raise ValueError("geodésica vacía")
            poly.extend(seg.tolist() if not poly else seg.tolist()[1:])
        return poly
    except Exception:
        return _straight_surface_polyline(mesh, waypoints)

# Densidad de muestreo de la recta (mm entre puntos). Suficiente para que se
# pegue a la superficie en zonas curvas sin saturar la polilínea.
SEAM_SAMPLE_MM = 4.0


def _straight_surface_polyline(mesh, waypoints: list[int]) -> list[list[float]]:
    """Recta entre los puntos clickeados, densificada y PEGADA a la superficie.

    Entre cada par de waypoints traza el segmento recto en 3D, lo densifica y
    proyecta cada muestra al punto más cercano de la malla: queda recto en
    planta pero apoyado en la superficie. Si la proyección no está disponible,
    devuelve la recta tal cual (cae con elegancia).
    """
    V = np.asarray(mesh.vertices)
    chunks: list[np.ndarray] = []
    for a, b in zip(waypoints, waypoints[1:]):
        pa, pb = V[int(a)], V[int(b)]
        dist = float(np.linalg.norm(pb - pa))
        n = max(int(dist / SEAM_SAMPLE_MM), 1)
        ts = np.linspace(0.0, 1.0, n + 1)[:, None]
        seg = pa[None, :] * (1.0 - ts) + pb[None, :] * ts
        chunks.append(seg if not chunks else seg[1:])
    pts = np.vstack(chunks) if chunks else V[[int(waypoints[0])]]
    try:
        on_surface, _, _ = mesh.nearest.on_surface(pts)
        return np.asarray(on_surface).tolist()
    except Exception:
        return pts.tolist()


def _vertex_graph(mesh) -> sp.csr_matrix:
    """Grafo disperso vértice-vértice con pesos = longitud de arista."""
    if _graph_cache["version"] == SESSION.mesh_version:
        return _graph_cache["graph"]

    edges = mesh.edges_unique
    weights = mesh.edges_unique_length
    n = len(mesh.vertices)
    graph = sp.csr_matrix(
        (
            np.concatenate([weights, weights]),
            (
                np.concatenate([edges[:, 0], edges[:, 1]]),
                np.concatenate([edges[:, 1], edges[:, 0]]),
            ),
        ),
        shape=(n, n),
    )
    _graph_cache["version"] = SESSION.mesh_version
    _graph_cache["graph"] = graph
    return graph


def _shortest_path(graph: sp.csr_matrix, start: int, end: int) -> list[int]:
    """Cadena de vértices del camino más corto start -> end. ROBUSTO: nunca
    lanza — si los puntos están en zonas desconectadas (cracks del STL),
    los puentea con un segmento directo en vez de fallar."""
    distances, predecessors = dijkstra(
        graph, indices=start, return_predecessors=True
    )
    if not np.isfinite(distances[end]):
        return [start, end]  # desconectado: puente directo (no rompe la costura)

    path = [end]
    while path[-1] != start:
        path.append(int(predecessors[path[-1]]))
    path.reverse()
    return path


def _path_payload(
    vertex_ids: list[int], closed: bool
) -> tuple[list[int], list[tuple[int, int]], list[list[float]]]:
    """Calcula la cadena de vértices, aristas y polilínea 3D de la costura.

    No registra nada en la sesión: lo usan tanto la vista previa en vivo
    como el registro final de la costura.
    """
    mesh = SESSION.mesh
    n_vertices = len(mesh.vertices)
    for v in vertex_ids:
        if not 0 <= v < n_vertices:
            raise SeamError(f"Vértice {v} fuera de rango")

    # Quitar puntos repetidos consecutivos (clics que caen en el mismo vértice
    # en mallas densas) para no generar tramos de longitud cero.
    waypoints = [int(vertex_ids[0])]
    for v in vertex_ids[1:]:
        if int(v) != waypoints[-1]:
            waypoints.append(int(v))
    if closed and len(waypoints) >= 2 and waypoints[0] != waypoints[-1]:
        waypoints.append(waypoints[0])

    if len(waypoints) < 2:
        # Un solo punto único: aún no hay tramo que trazar
        chain = [waypoints[0]]
        return chain, [], mesh.vertices[chain].tolist()

    graph = _vertex_graph(mesh)

    # Cadena por aristas (para el CORTE), entre clics consecutivos
    chain: list[int] = []
    for a, b in zip(waypoints, waypoints[1:]):
        segment = _shortest_path(graph, a, b)
        chain.extend(segment if not chain else segment[1:])

    edge_path = [(chain[i], chain[i + 1]) for i in range(len(chain) - 1)]
    # Línea guía: geodésica exacta sobre la superficie (recta), fallback a recta
    polyline = _geodesic_polyline(mesh, waypoints)
    return chain, edge_path, polyline


def preview_seam(vertex_ids: list[int], closed: bool = False) -> dict:
    """Traza la geodésica actual SIN registrar la costura (vista previa)."""
    _, _, polyline = _path_payload(vertex_ids, closed)
    return {"polyline": polyline, "closed": closed}


def create_seam(vertex_ids: list[int], closed: bool = False) -> dict:
    """Conecta los clics con la geodésica sobre la superficie y la registra.

    Devuelve la polilínea geodésica (dibujo), la cadena de aristas (corte) y
    la cadena de vértices con su posición 3D (para imantar costuras nuevas).
    """
    chain, edge_path, polyline = _path_payload(vertex_ids, closed)
    mesh = SESSION.mesh

    seam = Seam(
        seam_id=0,  # lo asigna la sesión
        vertex_ids=list(vertex_ids),
        edge_path=edge_path,
        polyline=polyline,
        closed=closed,
    )
    seam_id = SESSION.add_seam(seam)
    return {
        "seam_id": seam_id,
        "polyline": polyline,
        "edge_path": [list(e) for e in edge_path],
        "chain": [[int(v), *mesh.vertices[v].tolist()] for v in chain],
        "closed": closed,
    }
