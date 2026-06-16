"""Fase 3 - Subdivisión de la CARA A por costuras. FUNCIONAL.

MODELO (aclarado por Carlos):
- La CARA A seleccionada es el PATRÓN PRIMARIO.
- Sin costuras, ese patrón primario se mantiene entero (un círculo sigue
  siendo un círculo -> 1 patrón).
- Cada costura que cruza la cara A la divide: un círculo con una costura
  por el medio -> 2 medias lunas -> 2 patrones.

Formalmente, trabajando SOLO con las caras de la cara A:

    n_patrones = componentes conexas del grafo de adyacencia cara-cara
    (restringido a la cara A), tras remover las adyacencias que cruza
    una costura.

Una costura cuyos extremos no llegan al borde de la cara A (o a otra
costura) no separa nada y se reporta como warning.
"""

import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components

import trimesh

from app.core.session import SESSION


# Un componente con área <= a esto no se considera un patrón (triángulos
# sueltos o slivers de la selección). 3 cm² = 300 mm².
MIN_PATCH_AREA_MM2 = 300.0


class SegmentationError(Exception):
    """No se puede segmentar (falta seleccionar la cara A, etc.)."""


def _seam_edge_set(seams) -> set[tuple[int, int]]:
    """Aristas cortadas por las costuras dadas, como pares ordenados."""
    cut = set()
    for seam in seams:
        for a, b in seam.edge_path:
            cut.add((min(a, b), max(a, b)))
    return cut


def _components_within(
    mesh, selected_mask: np.ndarray, cut_edges: set[tuple[int, int]]
) -> tuple[int, np.ndarray, int]:
    """Componentes conexas restringidas a las caras de la selección.

    Descarta los componentes con área <= MIN_PATCH_AREA_MM2 (no son patrones,
    solo fragmentos). Devuelve (n_patches, labels, n_descartados) donde labels
    tiene longitud n_faces y vale -1 en las caras que no forman parte de
    ningún patrón (fuera de la cara A o en un fragmento descartado).
    """
    adjacency = mesh.face_adjacency
    shared = np.sort(mesh.face_adjacency_edges, axis=1)

    # Conservar solo adyacencias entre dos caras seleccionadas
    both_selected = selected_mask[adjacency[:, 0]] & selected_mask[adjacency[:, 1]]

    if cut_edges:
        n = len(mesh.vertices)
        shared_keys = shared[:, 0].astype(np.int64) * n + shared[:, 1]
        cut_keys = np.array(
            [a * n + b for a, b in sorted(cut_edges)], dtype=np.int64
        )
        not_cut = ~np.isin(shared_keys, cut_keys)
        both_selected = both_selected & not_cut

    keep = adjacency[both_selected]
    n_faces = len(mesh.faces)
    graph = sp.csr_matrix(
        (np.ones(len(keep)), (keep[:, 0], keep[:, 1])),
        shape=(n_faces, n_faces),
    )
    n_comp, raw = connected_components(graph, directed=False)

    # Área de cada componente (sumando solo caras seleccionadas)
    sel_faces = np.flatnonzero(selected_mask)
    comp_of = raw[sel_faces]
    comp_ids, inverse = np.unique(comp_of, return_inverse=True)
    comp_areas = np.bincount(inverse, weights=mesh.area_faces[sel_faces])

    # Mantener solo los componentes con área suficiente, ordenados por tamaño
    big = comp_areas > MIN_PATCH_AREA_MM2
    kept = comp_ids[big][np.argsort(-comp_areas[big])]
    n_dropped = int(comp_ids.size - kept.size)

    # Reindexar 0..n_patches-1 (parche 0 = mayor); fragmentos y no-cara-A = -1
    remap = np.full(n_comp, -1, dtype=np.int64)
    remap[kept] = np.arange(kept.size)
    labels = np.full(n_faces, -1, dtype=np.int64)
    labels[sel_faces] = remap[raw[sel_faces]]
    return int(kept.size), labels, n_dropped


# Huecos de la selección (caras no seleccionadas encerradas) hasta este
# tamaño se rellenan: el flood por ángulo deja "speckle" en zonas lisas y
# eso vuelve el parche un queso no aplanable. No se rellenan regiones
# grandes (esas son superficies reales: reverso, paredes).
MAX_HOLE_FACES = 400


def _fill_selection_holes(mesh, selected_mask: np.ndarray) -> np.ndarray:
    """Rellena huecos PEQUEÑOS encerrados en la selección (cierre morfológico).

    Añade las componentes de caras no seleccionadas que son pequeñas y no son
    la región exterior mayor: así el parche queda sólido (menos lazos de borde)
    y los aplanadores lo tratan como disco.
    """
    n = len(mesh.faces)
    adj = mesh.face_adjacency
    unsel = ~selected_mask
    both = unsel[adj[:, 0]] & unsel[adj[:, 1]]
    keep = adj[both]
    graph = sp.csr_matrix(
        (np.ones(len(keep)), (keep[:, 0], keep[:, 1])), shape=(n, n)
    )
    _, labels = connected_components(graph, directed=False)
    unsel_faces = np.flatnonzero(unsel)
    if unsel_faces.size == 0:
        return selected_mask
    comp_ids, counts = np.unique(labels[unsel_faces], return_counts=True)
    exterior = comp_ids[np.argmax(counts)]  # la región exterior (la mayor)

    filled = selected_mask.copy()
    for comp, count in zip(comp_ids, counts):
        if comp != exterior and count <= MAX_HOLE_FACES:
            filled[unsel_faces[labels[unsel_faces] == comp]] = True
    return filled


def segment_by_seams(selected_faces: list[int]) -> dict:
    """Subdivide la cara A seleccionada por las costuras activas."""
    mesh = SESSION.mesh
    if not selected_faces:
        raise SegmentationError(
            "Selecciona primero la Cara A (la superficie donde vive el patrón)."
        )

    selected = np.asarray(selected_faces, dtype=np.int64)
    if selected.min() < 0 or selected.max() >= len(mesh.faces):
        raise SegmentationError("La selección contiene caras fuera de rango")

    selected_mask = np.zeros(len(mesh.faces), dtype=bool)
    selected_mask[selected] = True
    selected_mask = _fill_selection_holes(mesh, selected_mask)
    SESSION.selected_faces = selected_mask

    seams = list(SESSION.seams.values())
    n_patches, labels, n_dropped = _components_within(
        mesh, selected_mask, _seam_edge_set(seams)
    )

    # Warnings: costura que al quitarla no cambia el número de patrones
    warnings = []
    for seam in seams:
        others = [s for s in seams if s.seam_id != seam.seam_id]
        n_without, _, _ = _components_within(
            mesh, selected_mask, _seam_edge_set(others)
        )
        if n_without == n_patches:
            warnings.append(
                f"La costura #{seam.seam_id} no separa la Cara A en dos piezas "
                "(pero puede seguir sirviendo para abrir la superficie al desplegar)."
            )

    if n_dropped:
        warnings.append(
            f"{n_dropped} fragmento(s) ≤ 3 cm² ignorado(s) (no cuentan como patrón)."
        )

    # Extraer submalla por parche (sin process: conservar índices originales)
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    SESSION.patches.clear()
    for patch_id in range(n_patches):
        face_ids = np.flatnonzero(labels == patch_id)
        patch_faces = faces[face_ids]
        orig_vertex_ids, local_faces = np.unique(patch_faces, return_inverse=True)
        local_faces = local_faces.reshape(-1, 3)
        submesh = trimesh.Trimesh(
            vertices=vertices[orig_vertex_ids],
            faces=local_faces,
            process=False,
        )
        SESSION.patches[patch_id] = {
            "mesh": submesh,
            "orig_vertex_ids": orig_vertex_ids,
            "face_ids": face_ids,
            "area_3d": float(submesh.area),
        }

    SESSION.face_patch = labels.tolist()
    return {
        "n_patches": n_patches,
        "dropped_fragments": n_dropped,
        "face_patch": SESSION.face_patch,  # -1 = fuera de la cara A o fragmento
        "patches": [
            {
                "patch_id": pid,
                "n_faces": int(len(p["face_ids"])),
                "area_cm2": round(p["area_3d"] / 100.0, 1),
            }
            for pid, p in sorted(SESSION.patches.items())
        ],
        "warnings": warnings,
    }
