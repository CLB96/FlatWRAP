"""Imprimir una costura en la malla: partir los triángulos que cruza.

El corte de los patrones debe seguir EXACTAMENTE la línea entre los puntos que
clickeó el usuario, no las aristas de la malla. Para eso "imprimimos" el camino
recto sobre la superficie en la malla: donde cruza un triángulo, lo
RE-TRIANGULAMOS insertando el camino como aristas reales.

Piezas:
  1. `_walk_straight`: camina en línea recta SOBRE la superficie de un vértice a
     otro, triángulo a triángulo (proyecta el destino al plano de cada cara y
     sale por la arista que cruza). Da el "corredor" de caras adyacentes con el
     punto de cruce en cada arista — robusto y sin dependencias (no usa rtree ni
     geodésicas exactas, que fallan en mallas no-manifold de STEP).
  2. `_split_face`: parte cada cara del corredor por su cuerda entrada→salida.

`imprint_seam(mesh, waypoints)` devuelve la malla refinada + las aristas de corte
(la costura ya como aristas) y `face_source` (cara nueva -> cara original), o None
si aparece un caso no soportado (el llamador cae al corte por aristas).
"""

from collections import defaultdict

import numpy as np


# --------------------------------------------------------------- caminado recto
def _edge_neighbors(mesh) -> dict:
    """arista (min,max) -> lista de caras que la comparten."""
    F = np.asarray(mesh.faces)
    out = defaultdict(list)
    for fi, (a, b, c) in enumerate(F):
        for x, y in ((a, b), (b, c), (c, a)):
            out[(int(min(x, y)), int(max(x, y)))].append(fi)
    return out


def _vertex_faces(mesh) -> dict:
    F = np.asarray(mesh.faces)
    out = defaultdict(list)
    for fi, tri in enumerate(F):
        for v in tri:
            out[int(v)].append(fi)
    return out


def _face_basis(V, tri):
    """Base ortonormal (origen, u, v, normal) del plano de un triángulo."""
    p0, p1, p2 = V[tri[0]], V[tri[1]], V[tri[2]]
    u = p1 - p0
    nu = np.linalg.norm(u)
    u = u / nu if nu > 1e-12 else np.array([1.0, 0.0, 0.0])
    n = np.cross(p1 - p0, p2 - p0)
    nn = np.linalg.norm(n)
    n = n / nn if nn > 1e-12 else np.array([0.0, 0.0, 1.0])
    v = np.cross(n, u)
    return p0, u, v, n


def _walk_straight(mesh, v_start, v_end, eneigh, vfaces, max_steps=100000):
    """Corredor (lista de (face, entry_loc, exit_loc)) del camino recto sobre la
    superficie de v_start a v_end. loc = ('vertex', id) o ('edge', (a,b), pt3d).

    Devuelve None si no logra avanzar (se usa el fallback por aristas)."""
    V = np.asarray(mesh.vertices)
    F = np.asarray(mesh.faces)
    target = V[v_end]

    # Cara inicial: la incidente a v_start más orientada hacia el destino
    cand = vfaces[v_start]
    if not cand:
        return None
    dirto = target - V[v_start]
    best, bestdot = cand[0], -2.0
    for fi in cand:
        cen = V[F[fi]].mean(axis=0)
        d = cen - V[v_start]
        nd = np.linalg.norm(d)
        if nd < 1e-12:
            continue
        dot = float(d @ dirto) / (nd * (np.linalg.norm(dirto) + 1e-12))
        if dot > bestdot:
            bestdot, best = dot, fi

    corridor = []
    face = best
    entry = ("vertex", int(v_start))
    cur = V[v_start].copy()
    prev_edge = None

    for _ in range(max_steps):
        tri = [int(x) for x in F[face]]
        if v_end in tri:
            corridor.append((face, entry, ("vertex", int(v_end))))
            return corridor

        p0, u, v, _ = _face_basis(V, tri)
        def to2d(P):
            d = P - p0
            return np.array([d @ u, d @ v])
        c2 = to2d(cur)
        t2 = to2d(target)        # destino proyectado al plano de la cara
        d2 = t2 - c2
        if np.linalg.norm(d2) < 1e-9:
            return None

        # Aristas de la cara como pares de vértices
        edges = [(tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])]
        best_t, exit_edge, exit_pt = np.inf, None, None
        for (a, b) in edges:
            ekey = (min(a, b), max(a, b))
            if prev_edge is not None and ekey == prev_edge:
                continue  # no salir por donde entramos
            A, B = to2d(V[a]), to2d(V[b])
            e = B - A
            denom = d2[0] * (-e[1]) - d2[1] * (-e[0])
            if abs(denom) < 1e-12:
                continue
            rhs = A - c2
            t = (rhs[0] * (-e[1]) - rhs[1] * (-e[0])) / denom   # avance del rayo
            s = (d2[0] * rhs[1] - d2[1] * rhs[0]) / denom       # posición en arista
            if t > 1e-9 and -1e-6 <= s <= 1 + 1e-6:
                if t < best_t:
                    s = min(1.0, max(0.0, s))
                    best_t, exit_edge = t, ekey
                    exit_pt = V[a] + s * (V[b] - V[a])
        if exit_edge is None:
            return None

        corridor.append((face, entry, ("edge", exit_edge, exit_pt)))
        # Pasar a la cara vecina por la arista de salida
        nb = [g for g in eneigh[exit_edge] if g != face]
        if not nb:
            return None      # arista de borde: el camino se sale de la malla
        entry = ("edge", exit_edge, exit_pt)
        cur = exit_pt
        prev_edge = exit_edge
        face = nb[0]
    return None


# --------------------------------------------------------------- split de cara
def _loc_vertex_id(loc, vid_of_edge):
    if loc[0] == "vertex":
        return loc[1]
    return vid_of_edge[(min(loc[1]), max(loc[1]))]


def _split_face(tri, entry, exit_, vid_of_edge, verts):
    """Parte el triángulo por la cuerda entry->exit (ver módulo). Lista de tris."""
    v0, v1, v2 = (int(v) for v in tri)
    n_orig = np.cross(verts[v1] - verts[v0], verts[v2] - verts[v0])
    eid = {id(loc): _loc_vertex_id(loc, vid_of_edge) for loc in (entry, exit_)}

    loop = []
    for a, b in ((v0, v1), (v1, v2), (v2, v0)):
        loop.append(a)
        for loc in (entry, exit_):
            if loc[0] == "edge" and {min(loc[1]), max(loc[1])} == {a, b}:
                loop.append(eid[id(loc)])
    i_in, i_out = loop.index(eid[id(entry)]), loop.index(eid[id(exit_)])
    if i_in == i_out:
        return None
    n = len(loop)
    def arc(start, end):
        out, j = [], start
        while True:
            out.append(loop[j])
            if j == end:
                break
            j = (j + 1) % n
        return out
    tris = []
    for a in (arc(i_in, i_out), arc(i_out, i_in)):
        for t in range(1, len(a) - 1):
            x, y, z = a[0], a[t], a[t + 1]
            if float(np.cross(verts[y] - verts[x], verts[z] - verts[x]) @ n_orig) < 0:
                y, z = z, y
            tris.append((x, y, z))
    return tris


# --------------------------------------------------------------- imprint
def imprint_seam(mesh, waypoints):
    """Imprime el camino recto que une los waypoints (ids de vértice) en la malla.

    Devuelve {vertices, faces, cut_edges, face_source} o None si no se puede.
    """
    wp = [int(w) for w in waypoints]
    wp = [wp[0]] + [w for i, w in enumerate(wp[1:], 1) if w != wp[i - 1]]
    if len(wp) < 2:
        return None

    eneigh = _edge_neighbors(mesh)
    vfaces = _vertex_faces(mesh)
    corridor = []
    for a, b in zip(wp, wp[1:]):
        seg = _walk_straight(mesh, a, b, eneigh, vfaces)
        if seg is None:
            return None
        corridor.extend(seg if not corridor else seg)
    if not corridor:
        return None
    # Cara repetida en todo el corredor: no soportado (slivers/cruces) -> fallback
    cfaces = [f for f, _, _ in corridor]
    if len(cfaces) != len(set(cfaces)):
        return None

    verts = [np.asarray(v, dtype=np.float64) for v in mesh.vertices]
    vid_of_edge = {}
    for _, entry, exit_ in corridor:
        for loc in (entry, exit_):
            if loc[0] == "edge":
                key = (min(loc[1]), max(loc[1]))
                if key not in vid_of_edge:
                    vid_of_edge[key] = len(verts)
                    verts.append(np.asarray(loc[2], dtype=np.float64))

    F = np.asarray(mesh.faces)
    corridor_faces = {f for f, _, _ in corridor}
    new_faces, face_source = [], []
    for i in range(len(F)):
        if i not in corridor_faces:
            new_faces.append(F[i].tolist())
            face_source.append(i)
    cut_edges = []
    for f, entry, exit_ in corridor:
        sub = _split_face(F[f], entry, exit_, vid_of_edge, verts)
        if sub is None:
            return None
        for t in sub:
            new_faces.append(list(t))
            face_source.append(int(f))
        a = _loc_vertex_id(entry, vid_of_edge)
        b = _loc_vertex_id(exit_, vid_of_edge)
        cut_edges.append((int(min(a, b)), int(max(a, b))))

    return {
        "vertices": np.asarray(verts),
        "faces": np.asarray(new_faces, dtype=np.int64),
        "cut_edges": cut_edges,
        "face_source": np.asarray(face_source, dtype=np.int64),
    }
