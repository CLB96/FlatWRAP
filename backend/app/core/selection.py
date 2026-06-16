"""Selección de la CARA A (la superficie show de la pieza). FUNCIONAL.

Las piezas son plásticos sólidos con espesor (2-3.5 mm): la malla incluye
la cara vista, el reverso y las paredes del canto. El patrón 2D solo se
genera desde la CARA A, así que el primer paso del flujo es aislarla.

Selección por "relleno por ángulo" (flood fill): el usuario hace clic en
un triángulo de la cara A y la selección se propaga por adyacencia
mientras el ángulo diédrico entre caras vecinas sea suave, deteniéndose
en los BORDES VIVOS (el canto donde la cara A dobla hacia la pared del
espesor). Es el equivalente a "seleccionar cara tangente" de un CAD.

La selección final puede afinarse con pincel (agregar/quitar triángulos)
desde el frontend; este módulo solo provee el cálculo del flood, que es
lo que necesita la conectividad de la malla.
"""

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

from app.core.session import SESSION

# Ángulo por defecto: piezas de interior automotriz suelen tener cantos
# claramente marcados (>45°). 35° captura la cara A sin saltar el borde.
DEFAULT_ANGLE_DEG = 35.0

# El flood NO cruza a caras que miran más de esto respecto al clic: frena en
# el canto/pared aunque sea un filete SUAVE (que el ángulo diédrico no detiene)
# y nunca salta al reverso. Generoso para permitir una Cara A bien curvada.
DEFAULT_SIDE_DEG = 85.0


class SelectionError(Exception):
    """Error de usuario al seleccionar (cara fuera de rango, etc.)."""


# Cache de la adyacencia suave (sin costuras), indexada por (versión de malla,
# versión de costuras, ángulo) — no por id(mesh): ver Session.mesh_version.
_adj_cache: dict = {"key": None, "adj": None}


def _seam_edge_keys(mesh) -> np.ndarray:
    """Aristas de costura como claves enteras (para excluirlas del flood)."""
    n = len(mesh.vertices)
    keys = []
    for seam in SESSION.seams.values():
        for a, b in seam.edge_path:
            keys.append(min(a, b) * n + max(a, b))
    return np.array(keys, dtype=np.int64)


def _smooth_adjacency(mesh, angle_deg: float) -> np.ndarray:
    """Adyacencias cara-cara SUAVES (ángulo diédrico bajo) y que no cruzan
    una costura. Cacheada: depende solo de la malla, costuras y ángulo."""
    key = (SESSION.mesh_version, SESSION.seams_version, angle_deg)
    if _adj_cache["key"] == key:
        return _adj_cache["adj"]

    adjacency = mesh.face_adjacency                  # (k, 2) caras vecinas
    angles = mesh.face_adjacency_angles              # (k,) radianes
    mask = angles < np.radians(angle_deg)

    seam_keys = _seam_edge_keys(mesh)
    if seam_keys.size:
        n = len(mesh.vertices)
        shared = np.sort(mesh.face_adjacency_edges, axis=1)
        shared_keys = shared[:, 0].astype(np.int64) * n + shared[:, 1]
        mask &= ~np.isin(shared_keys, seam_keys)     # no cruzar costuras

    adj = adjacency[mask]
    _adj_cache.update(key=key, adj=adj)
    return adj


def flood_select(
    seed_face: int,
    angle_deg: float = DEFAULT_ANGLE_DEG,
    side_deg: float = DEFAULT_SIDE_DEG,
) -> dict:
    """Cara A bajo el clic: crece por la superficie suave pero SIN cruzar al
    reverso ni a las paredes.

    Combina dos barreras: (1) ángulo diédrico (cantos vivos) y (2) orientación
    de la cara respecto al clic (no incluye caras que miran a más de `side_deg`
    del seed). La (2) frena en filetes SUAVES que la (1) no detiene, y evita
    saltar al reverso del sólido — el problema típico en piezas cerradas.
    """
    mesh = SESSION.mesh
    if mesh is None:
        raise SelectionError("No hay malla cargada")
    if not 0 <= seed_face < len(mesh.faces):
        raise SelectionError(f"Cara {seed_face} fuera de rango")

    adj = _smooth_adjacency(mesh, angle_deg)

    # Filtro de orientación: solo caras del mismo lado que el seed
    normals = np.asarray(mesh.face_normals)
    same_side = (normals @ normals[seed_face]) > np.cos(np.radians(side_deg))
    keep = adj[same_side[adj[:, 0]] & same_side[adj[:, 1]]]

    n_faces = len(mesh.faces)
    graph = sp.csr_matrix(
        (np.ones(len(keep)), (keep[:, 0], keep[:, 1])),
        shape=(n_faces, n_faces),
    )
    _, labels = connected_components(graph, directed=False)
    region = np.flatnonzero(labels == labels[seed_face])

    return {
        "faces": region.tolist(),
        "n_faces": int(region.size),
        "area_cm2": round(float(mesh.area_faces[region].sum()) / 100.0, 1),
    }


def selection_info(face_ids: list[int]) -> dict:
    """Área y conteo de una selección arbitraria (para feedback en vivo)."""
    mesh = SESSION.mesh
    if mesh is None:
        raise SelectionError("No hay malla cargada")
    if not face_ids:
        return {"n_faces": 0, "area_cm2": 0.0}
    faces = np.asarray(face_ids, dtype=np.int64)
    if faces.min() < 0 or faces.max() >= len(mesh.faces):
        raise SelectionError("Selección con caras fuera de rango")
    return {
        "n_faces": int(faces.size),
        "area_cm2": round(float(mesh.area_faces[faces].sum()) / 100.0, 1),
    }
