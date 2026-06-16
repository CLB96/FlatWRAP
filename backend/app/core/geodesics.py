"""Fase 2 - Costuras como líneas RECTAS sobre la superficie. FUNCIONAL.

La costura que el usuario VE es una RECTA entre los puntos que clickeó, pegada
a la superficie (no zigzaguea por aristas ni se va por el ruido del teselado):
así puede trazar líneas limpias en las zonas que le importan. Cuanto más puntos
ponga, más sigue la costura su trazo; entre dos puntos va derecho.

Para el CORTE interno (segmentación, que opera sobre aristas) se mantiene en
paralelo el camino más corto por aristas (Dijkstra): el contorno del corte
puede quedar un poco "sucio" (sigue aristas), pero la LÍNEA guía es recta.

Nota: antes se usaba la geodésica exacta (potpourri3d/geometry-central), pero
ese solver EXIGE una malla manifold y lanza en los teselados de STEP reales
(arista duplicada) → toda la costura caía al zigzag por aristas. La recta
pegada a la superficie es robusta y es lo que el usuario espera.
"""

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import dijkstra

from app.core.session import SESSION, Seam


class SeamError(Exception):
    """Error de usuario al trazar una costura (vértices inválidos, etc.)."""


# Cache indexado por versión de malla (no por id(mesh): ver Session.mesh_version)
_graph_cache: dict = {"version": None, "graph": None}

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
    # Línea guía RECTA para DIBUJAR (pegada a la superficie, entre los clics)
    polyline = _straight_surface_polyline(mesh, waypoints)
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
