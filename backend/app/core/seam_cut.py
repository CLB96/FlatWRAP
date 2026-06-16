"""Imprimir una costura en la malla: partir los triángulos que cruza.

El corte de los patrones debe seguir EXACTAMENTE la línea que dibujó el usuario,
no las aristas de la malla. Para eso "imprimimos" la polilínea de la costura en la
malla: donde la línea cruza un triángulo, lo RE-TRIANGULAMOS insertando la línea
como aristas reales. Así la segmentación puede cortar justo por la línea.

Algoritmo (caso común, con fallback a None si aparece un caso no soportado):
  1. Recorrer la polilínea (puntos sobre la superficie, cada uno con su cara) y
     construir el "corredor" de caras que atraviesa, con el punto de cruce en cada
     arista compartida entre caras consecutivas.
  2. Cada punto de cruce sobre una arista se vuelve un vértice nuevo (compartido
     por las 2 caras de esa arista → la malla queda consistente).
  3. Cada cara del corredor se parte por su cuerda entrada→salida (split del
     polígono del triángulo en dos arcos, cada uno triangulado en abanico),
     respetando el sentido de giro original.

Devuelve la malla refinada y las aristas de corte (la costura ya como aristas),
o None si el corredor tiene un caso no soportado (cara revisitada, saltos no
adyacentes, cruce degenerado): el llamador cae al comportamiento por aristas.
"""

from collections import defaultdict

import numpy as np


def _shared_edge_map(mesh) -> dict:
    """(min(f1,f2), max) -> (va, vb) arista compartida entre caras vecinas."""
    out = {}
    for (f1, f2), (va, vb) in zip(mesh.face_adjacency, mesh.face_adjacency_edges):
        out[(int(min(f1, f2)), int(max(f1, f2)))] = (int(va), int(vb))
    return out


def _segment_segment_point_on_b(p0, p1, a0, a1):
    """Punto sobre el segmento a0-a1 más cercano al segmento p0-p1 (clamp [0,1]).

    Sirve para ubicar dónde la costura cruza la arista a0-a1.
    """
    d1 = p1 - p0
    d2 = a1 - a0
    r = p0 - a0
    a = float(d1 @ d1)
    e = float(d2 @ d2)
    f = float(d2 @ r)
    if e < 1e-12:
        return a0.copy()
    c = float(d1 @ r)
    b = float(d1 @ d2)
    denom = a * e - b * b
    s = (b * f - c * e) / denom if denom > 1e-12 else 0.0
    t = (b * s + f) / e
    t = min(1.0, max(0.0, t))
    return a0 + t * d2


def _build_corridor(mesh, points: np.ndarray, faces: np.ndarray, edge_map: dict):
    """Lista ordenada (face, entry_loc, exit_loc) de las caras que cruza la línea.

    loc = ('vertex', vid) en los extremos (clics, que caen en un vértice), o
    ('edge', (va, vb), punto3D) en los cruces de arista. None si el corredor
    tiene un caso no soportado.
    """
    F = np.asarray(mesh.faces)
    # Secuencia de caras distintas + el índice de punto donde entra cada una
    seq = []          # [(face_id, idx_primer_punto_en_esa_cara)]
    for i, f in enumerate(faces):
        f = int(f)
        if not seq or seq[-1][0] != f:
            seq.append((f, i))
    if len(seq) < 1:
        return None
    seen = set()
    for f, _ in seq:
        if f in seen:
            return None        # cara revisitada: no soportado (fallback)
        seen.add(f)

    # Cruce en cada transición de cara (arista compartida)
    crossings = []     # crossings[k] = ('edge', (va,vb), pt) entre seq[k] y seq[k+1]
    for k in range(len(seq) - 1):
        fa, fb = seq[k][0], seq[k + 1][0]
        key = (min(fa, fb), max(fa, fb))
        if key not in edge_map:
            return None        # caras no adyacentes: fallback
        va, vb = edge_map[key]
        idx = seq[k + 1][1]    # primer punto en fb
        pt = _segment_segment_point_on_b(
            points[idx - 1], points[idx], mesh.vertices[va], mesh.vertices[vb]
        )
        crossings.append(("edge", (va, vb), pt))

    def nearest_vertex(face_id, p):
        tri = F[face_id]
        d = np.linalg.norm(mesh.vertices[tri] - p, axis=1)
        return int(tri[int(np.argmin(d))])

    corridor = []
    for k, (f, idx) in enumerate(seq):
        entry = crossings[k - 1] if k > 0 else ("vertex", nearest_vertex(f, points[0]))
        exit_ = crossings[k] if k < len(seq) - 1 else (
            "vertex", nearest_vertex(f, points[-1])
        )
        corridor.append((f, entry, exit_))
    return corridor


def _loc_vertex_id(loc, vid_of_edge: dict):
    """Id de vértice de un loc; para 'edge' usa el id (ya creado) de su cruce."""
    if loc[0] == "vertex":
        return loc[1]
    return vid_of_edge[(min(loc[1]), max(loc[1]))]


def _split_face(tri, entry, exit_, vid_of_edge, verts):
    """Parte un triángulo por la cuerda entry→exit. Devuelve lista de tris nuevos.

    Construye el borde del triángulo insertando los puntos de cruce en su arista,
    lo corta en dos arcos por entry/exit y triangula cada arco en abanico,
    corrigiendo el sentido de giro contra la normal original.
    """
    v0, v1, v2 = (int(v) for v in tri)
    n_orig = np.cross(verts[v1] - verts[v0], verts[v2] - verts[v0])

    eid = {}  # id de vértice de cada loc
    for loc in (entry, exit_):
        eid[id(loc)] = _loc_vertex_id(loc, vid_of_edge)

    # Borde del triángulo con los cruces insertados, en orden de giro
    loop = []
    for a, b in ((v0, v1), (v1, v2), (v2, v0)):
        loop.append(a)
        for loc in (entry, exit_):
            if loc[0] == "edge" and {min(loc[1]), max(loc[1])} == {a, b}:
                loop.append(eid[id(loc)])
    i_in = loop.index(eid[id(entry)])
    i_out = loop.index(eid[id(exit_)])
    if i_in == i_out:
        return None
    # Dos arcos del ciclo
    n = len(loop)
    arc1, j = [], i_in
    while True:
        arc1.append(loop[j]);
        if j == i_out:
            break
        j = (j + 1) % n
    arc2, j = [], i_out
    while True:
        arc2.append(loop[j])
        if j == i_in:
            break
        j = (j + 1) % n

    tris = []
    for arc in (arc1, arc2):
        for t in range(1, len(arc) - 1):
            a, b, c = arc[0], arc[t], arc[t + 1]
            nt = np.cross(verts[b] - verts[a], verts[c] - verts[a])
            if float(nt @ n_orig) < 0:
                b, c = c, b
            tris.append((a, b, c))
    return tris


def imprint_seam(mesh, points: np.ndarray, faces: np.ndarray):
    """Imprime la polilínea (points en la superficie, faces = cara de cada punto)
    en la malla. Devuelve dict {vertices, faces, cut_edges} o None si no se puede.

    cut_edges = pares de vértices (la costura ya como aristas reales) para que la
    segmentación corte exactamente por ahí.
    """
    points = np.asarray(points, dtype=np.float64)
    faces = np.asarray(faces)
    if len(points) < 2:
        return None
    edge_map = _shared_edge_map(mesh)
    corridor = _build_corridor(mesh, points, faces, edge_map)
    if corridor is None:
        return None

    verts = [np.asarray(v, dtype=np.float64) for v in mesh.vertices]
    vid_of_edge = {}   # (min,max) arista -> id de vértice del cruce
    for _, entry, exit_ in corridor:
        for loc in (entry, exit_):
            if loc[0] == "edge":
                key = (min(loc[1]), max(loc[1]))
                if key not in vid_of_edge:
                    vid_of_edge[key] = len(verts)
                    verts.append(np.asarray(loc[2], dtype=np.float64))

    F = np.asarray(mesh.faces)
    corridor_faces = {f for f, _, _ in corridor}
    new_faces = [F[i].tolist() for i in range(len(F)) if i not in corridor_faces]
    cut_edges = []
    for f, entry, exit_ in corridor:
        sub = _split_face(F[f], entry, exit_, vid_of_edge, verts)
        if sub is None:
            return None
        new_faces.extend(sub)
        a = _loc_vertex_id(entry, vid_of_edge)
        b = _loc_vertex_id(exit_, vid_of_edge)
        cut_edges.append((int(min(a, b)), int(max(a, b))))

    return {
        "vertices": np.asarray(verts),
        "faces": np.asarray(new_faces, dtype=np.int64),
        "cut_edges": cut_edges,
    }
