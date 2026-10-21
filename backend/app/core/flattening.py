"""Fase 4 - Aplanado de parches y métrica de distorsión. FUNCIONAL.

Tres métodos de aplanado (el usuario elige; default "unfold"):

DESPLEGADO RÍGIDO ("unfold", por defecto)
    Abate cada cara sobre la arista-bisagra que comparte con su vecina
    (árbol de expansión sobre las caras), como papercraft / chapa metálica.
    Distorsión CERO por triángulo: cada cara conserva su tamaño real -> el
    patrón es el material real (un cubo cae en su cruz). Las aristas del
    árbol son pliegues (caras unidas); las demás, cortes del contorno.
    En superficies de doble curvatura el net puede SOLAPARSE: se AVISA para
    que el usuario decida poner una costura (nunca se corta automáticamente).

PROYECCIÓN ORTOGONAL ("project")
    Sombra de la Cara A sobre su plano de mejor ajuste (PCA). Coincide con
    la vista, nunca falla, pero subestima el área de las zonas curvas.

DESARROLLADO ARAP ("arap")
    Estira el material a un plano minimizando distorsión local (libigl).
    Bueno para superficies suaves de doble curvatura; puede fallar o
    deformar la forma en mallas complejas.

Interfaz común guardada en el parche:
    patch["uv"]         coords 2D (mm) del contorno
    patch["faces_2d"]   triángulos que indexan a uv
    patch["orig_to_uv"] vértice local original -> 2D (para muescas DXF)
"""

import hashlib
from collections import OrderedDict, defaultdict, deque

import igl
import numpy as np
import scipy.sparse as sp
from scipy.sparse.csgraph import connected_components as connected_components_cs
import scipy.sparse.linalg as spla
import trimesh

from app.core.session import SESSION

PATCH_GAP_MM = 30.0  # separación entre parches en el layout conjunto


class FlattenError(Exception):
    """El parche no puede aplanarse (p. ej. superficie cerrada sin borde)."""


def _triangle_areas(points: np.ndarray, faces: np.ndarray) -> np.ndarray:
    """Áreas de triángulos para puntos 2D o 3D."""
    a = points[faces[:, 1]] - points[faces[:, 0]]
    b = points[faces[:, 2]] - points[faces[:, 0]]
    if points.shape[1] == 2:
        return 0.5 * np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0])
    return 0.5 * np.linalg.norm(np.cross(a, b), axis=1)


# ----------------------------------------------------------- Proyección
def _mean_normal(mesh, areas_3d: np.ndarray) -> np.ndarray:
    fn = np.asarray(mesh.face_normals)
    acc = (fn * areas_3d[:, None]).sum(axis=0)
    norm = np.linalg.norm(acc)
    return acc / norm if norm > 1e-9 else np.array([0.0, 0.0, 1.0])


def _project_uv(V: np.ndarray, mean_normal: np.ndarray) -> np.ndarray:
    centered = V - V.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    axis_u = vt[0]
    normal = vt[2]
    if np.dot(normal, mean_normal) < 0:
        normal = -normal
    axis_v = np.cross(normal, axis_u)
    return np.column_stack([centered @ axis_u, centered @ axis_v])


# ----------------------------------------------------------- Corte en disco
def _cut_mesh_along_edges(V: np.ndarray, F: np.ndarray, cut_edges: set):
    """Corta la malla a lo largo de las aristas de costura duplicando los
    vértices del corte, para que esas aristas se vuelvan BORDE.

    Convierte un parche que envuelve (no-disco, p. ej. un rollo con una
    hendidura) en un disco aplanable: así ARAP/LSCM dejan de fallar.
    Devuelve (V2, F2, cut_to_orig) donde cut_to_orig mapea vértice nuevo ->
    vértice local original. F2 conserva el mismo número y orden de caras.
    """
    F = np.asarray(F)
    V = np.asarray(V)
    n0 = len(V)
    cut_vertices = {int(v) for e in cut_edges for v in e}
    if not cut_vertices:
        return V, F, np.arange(n0)

    vfaces = defaultdict(list)
    for fi in range(len(F)):
        for v in F[fi]:
            if int(v) in cut_vertices:
                vfaces[int(v)].append(fi)
    edge_faces = defaultdict(list)
    for fi in range(len(F)):
        a, b, c = int(F[fi, 0]), int(F[fi, 1]), int(F[fi, 2])
        for x, y in ((a, b), (b, c), (c, a)):
            edge_faces[(min(x, y), max(x, y))].append(fi)

    # Decidir duplicaciones leyendo F original; aplicar al final (sin corromper)
    new_coords: list = []
    cut_to_orig_extra: list = []
    reassign: list = []  # (face, vertice_viejo, vertice_nuevo)
    for v in cut_vertices:
        faces_inc = vfaces[v]
        parent = {fi: fi for fi in faces_inc}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        # Unir caras incidentes a v conectadas por una arista NO-corte en v
        for fi in faces_inc:
            a, b, c = int(F[fi, 0]), int(F[fi, 1]), int(F[fi, 2])
            for x, y in ((a, b), (b, c), (c, a)):
                if v != x and v != y:
                    continue
                e = (min(x, y), max(x, y))
                if e in cut_edges:
                    continue
                for fj in edge_faces[e]:
                    if fj in parent:
                        parent[find(fi)] = find(fj)
        groups = defaultdict(list)
        for fi in faces_inc:
            groups[find(fi)].append(fi)
        if len(groups) <= 1:
            continue  # v no se separa (extremo de hendidura interior)
        # El primer grupo se queda con v; los demás reciben copias
        for grp in list(groups.values())[1:]:
            newv = n0 + len(new_coords)
            new_coords.append(V[v])
            cut_to_orig_extra.append(v)
            for fi in grp:
                reassign.append((fi, v, newv))

    F2 = F.copy()
    for fi, old, new in reassign:
        row = F2[fi]
        row[row == old] = new
    V2 = np.vstack([V, np.array(new_coords)]) if new_coords else V
    cut_to_orig = (
        np.concatenate([np.arange(n0), np.array(cut_to_orig_extra, dtype=int)])
        if cut_to_orig_extra
        else np.arange(n0)
    )
    return V2, F2, cut_to_orig


# ----------------------------------------------------------- ARAP / LSCM
def _initial_uv(V: np.ndarray, F: np.ndarray, boundary: np.ndarray) -> np.ndarray:
    bnd_uv = igl.map_vertices_to_circle(V, boundary)
    uv = igl.harmonic(V, F, boundary.astype(np.int64), bnd_uv, 1)
    if np.all(np.isfinite(uv)):
        return uv
    pins = np.array([boundary[0], boundary[len(boundary) // 2]], dtype=np.int64)
    span = float(np.linalg.norm(V[pins[0]] - V[pins[1]]))
    pin_uv = np.array([[0.0, 0.0], [span, 0.0]])
    uv, _ = igl.lscm(V, F, pins, pin_uv)
    if not np.all(np.isfinite(uv)):
        raise FlattenError("La parametrización inicial falló (malla degenerada)")
    return uv


# SLIM se itera en lotes pequeños con parada adaptativa (ver abajo). Validado
# contra 250 iteraciones fijas en casquetes de 40°/75°/100°: mismo estiramiento
# (p95 y área) con 20-25 iteraciones (10-15x menos tiempo). El tope evita
# colgarse en mallas patológicas.
SLIM_BATCH = 5
SLIM_MAX_ITERS = 250
SLIM_MIN_ITERS = 15
# Parada adaptativa: se mide el p95 del estiramiento por cara tras cada lote y
# se detiene cuando mejora menos de SLIM_STRETCH_TOL durante SLIM_PATIENCE lotes
# seguidos. (Medido: a ~20 iteraciones ya se está a ~0.3% del estiramiento
# convergido, pero cada iteración cuesta mucho más en mallas grandes.)
SLIM_STRETCH_TOL = 2.5e-3
SLIM_PATIENCE = 2
# Cache de resultados de SLIM por contenido de malla (V, F): re-aplanar tras
# borrar un parche o cambiar opciones no recalcula los parches intactos.
_SLIM_CACHE: "OrderedDict[str, np.ndarray]" = OrderedDict()
_SLIM_CACHE_MAX = 16


def _boundary_ellipse_uv(V: np.ndarray, boundary: np.ndarray) -> np.ndarray:
    """Borde -> ELIPSE con la proporción de la pieza (init de Tutte).

    Un círculo comprime exponencialmente las piezas alargadas (tiras de 6:1: las
    caras de los extremos quedan con áreas de 1e-20 y se degeneran/voltean). Una
    elipse con semiejes ∝ extensión PCA de la pieza sigue siendo convexa (Tutte
    sigue garantizando 0 volteos) pero reparte el área parejo. Los dos extremos
    de la pieza (a lo largo del eje mayor) se anclan en θ=0 y θ=π y cada mitad
    del borde se reparte por longitud de arco, así el orden es monótono (convexo
    estricto) y conserva la orientación del borde. En mm: cerca de la escala 3D.
    """
    pts = V[boundary]
    allc = V - V.mean(axis=0)
    _, _, vt = np.linalg.svd(allc[:: max(1, len(V) // 20000)], full_matrices=False)
    c1, c2 = allc @ vt[0], allc @ vt[1]
    a1 = max(float(np.ptp(c1)) / 2.0, 1e-6)
    a2 = max(float(np.ptp(c2)) / 2.0, a1 * 0.05)
    b1 = (pts - V.mean(axis=0)) @ vt[0]
    iA, iB = int(np.argmax(b1)), int(np.argmin(b1))
    n = len(boundary)
    seg = np.linalg.norm(pts - np.roll(pts, -1, axis=0), axis=1)   # i -> i+1
    cum = np.concatenate([[0.0], np.cumsum(seg)])                  # cum[i] = arco hasta i
    total = cum[-1]
    pos = np.mod(cum[:n] - cum[iA], total)                          # arco desde A (0..total)
    sAB = pos[iB]
    theta = np.where(
        pos <= sAB,
        np.pi * pos / max(sAB, 1e-12),
        np.pi + np.pi * (pos - sAB) / max(total - sAB, 1e-12),
    )
    return np.column_stack([a1 * np.cos(theta), a2 * np.sin(theta)])


def _tutte_uniform_uv(
    V: np.ndarray, F: np.ndarray, boundary: np.ndarray, bnd_uv: np.ndarray
) -> np.ndarray:
    """Inicialización de Tutte con pesos UNIFORMES (barycentric mapping).

    Para un borde convexo (aquí un círculo) el teorema de Tutte garantiza un
    embedding SIN volteos. A diferencia de la armónica cotangente, no usa
    cotangentes, así que es INMUNE a los triángulos obtusos/sliver que trae el
    teselado de STEP (esos hacían voltear la init y disparaban un falso
    "envuelve sobre sí misma"). Resuelve L_uniforme·uv_interior = -L_borde·uv_borde.
    """
    n = len(V)
    e = np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    e = np.unique(np.sort(e, axis=1), axis=0)
    rows = np.concatenate([e[:, 0], e[:, 1]])
    cols = np.concatenate([e[:, 1], e[:, 0]])
    adj = sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
    deg = np.asarray(adj.sum(axis=1)).ravel()
    lap = sp.diags(deg) - adj
    interior = np.setdiff1d(np.arange(n), boundary)
    uv = np.zeros((n, 2))
    uv[boundary] = bnd_uv
    if interior.size:
        lii = lap[interior][:, interior].tocsc()
        lib = lap[interior][:, boundary]
        uv[interior] = spla.spsolve(lii, -(lib @ bnd_uv))
    return uv


def _tutte_mvc_uv(
    V: np.ndarray, F: np.ndarray, boundary: np.ndarray, bnd_uv: np.ndarray
) -> np.ndarray:
    """Init de Floater con pesos de VALOR MEDIO (mean value coordinates).

    Los pesos uniformes de Tutte ignoran la geometría: una región densa unida al
    resto por un cuello angosto se contrae a un punto (áreas ~1e-20 -> volteos
    falsos). Los pesos de valor medio w_ij = (tan(a/2)+tan(b/2))/|vi-vj| son
    SIEMPRE positivos (triángulos obtusos incluidos), por lo que conservan la
    garantía de embedding sin volteos con borde convexo, pero reparten el área
    según la geometría 3D real.
    """
    n = len(V)
    P = V[F]                                   # (m, 3, 3)
    rows, cols, vals = [], [], []
    for c in range(3):
        a, b, d = P[:, c], P[:, (c + 1) % 3], P[:, (c + 2) % 3]
        u, v = b - a, d - a
        lu, lv = np.linalg.norm(u, axis=1), np.linalg.norm(v, axis=1)
        cross = np.linalg.norm(np.cross(u, v), axis=1)
        t = cross / np.maximum(lu * lv + (u * v).sum(1), 1e-30)   # tan(alfa/2)
        i, j, k = F[:, c], F[:, (c + 1) % 3], F[:, (c + 2) % 3]
        rows += [i, i]
        cols += [j, k]
        vals += [t / np.maximum(lu, 1e-12), t / np.maximum(lv, 1e-12)]
    W = sp.csr_matrix(
        (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)
    )
    lap = sp.diags(np.asarray(W.sum(axis=1)).ravel()) - W
    interior = np.setdiff1d(np.arange(n), boundary)
    uv = np.zeros((n, 2))
    uv[boundary] = bnd_uv
    if interior.size:
        lii = lap[interior][:, interior].tocsc()
        lib = lap[interior][:, boundary]
        uv[interior] = spla.spsolve(lii, -(lib @ bnd_uv))
    return uv


def principal_stretches(
    V: np.ndarray, f_src: np.ndarray, uv: np.ndarray, f_tgt: np.ndarray
):
    """Estiramientos principales σ1≥σ2 del mapa 3D→2D por triángulo.

    σ>1 = tensión (el material se estira); σ<1 = compresión. σ1·σ2 = ratio
    de área. Forma cerrada del SVD de matrices 2x2 (vectorizado).
    f_src indexa el 3D (V), f_tgt el 2D (uv); ambos en el mismo orden de cara.
    """
    P0, P1, P2 = V[f_src[:, 0]], V[f_src[:, 1]], V[f_src[:, 2]]
    e1, e2 = P1 - P0, P2 - P0
    len1 = np.linalg.norm(e1, axis=1)
    u = e1 / len1[:, None]
    n = np.cross(e1, e2)
    w = np.cross(n, e1)
    w /= np.linalg.norm(w, axis=1)[:, None]
    s1x = len1
    s2x, s2y = (e2 * u).sum(1), (e2 * w).sum(1)        # base 2D del triángulo 3D
    det = np.where(np.abs(s1x * s2y) > 1e-12, s1x * s2y, 1e-12)
    t1, t2 = uv[f_tgt[:, 1]] - uv[f_tgt[:, 0]], uv[f_tgt[:, 2]] - uv[f_tgt[:, 0]]
    # M = [t1 t2] · inv([s1 s2]); inv de matriz triangular (s1=(s1x,0))
    a = t1[:, 0] * s2y / det
    b = (-t1[:, 0] * s2x + t2[:, 0] * s1x) / det
    c = t1[:, 1] * s2y / det
    d = (-t1[:, 1] * s2x + t2[:, 1] * s1x) / det
    E, Fm, G, H = (a + d) / 2, (a - d) / 2, (c + b) / 2, (c - b) / 2
    q, r = np.hypot(E, H), np.hypot(Fm, G)
    return q + r, np.abs(q - r)


def _count_flips(uv: np.ndarray, F: np.ndarray) -> int:
    da = igl.doublearea(np.asfortranarray(uv), np.asfortranarray(F.astype(np.int32)))
    return int((da <= 0).sum())


def _boundary_loops_simple(F: np.ndarray) -> list[list[int]]:
    """Lazos de borde (aristas usadas por 1 sola cara) encadenados en bucles."""
    ec: dict = defaultdict(int)
    for f in F:
        for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            ec[(min(int(a), int(b)), max(int(a), int(b)))] += 1
    adj: dict = defaultdict(list)
    for (a, b), c in ec.items():
        if c == 1:
            adj[a].append(b)
            adj[b].append(a)
    seen: set = set()
    loops: list = []
    for start in list(adj):
        if start in seen:
            continue
        loop = [start]
        seen.add(start)
        cur, prev = start, None
        while True:
            nxt = next((x for x in adj[cur] if x != prev and x not in seen), None)
            if nxt is None:
                break
            loop.append(nxt)
            seen.add(nxt)
            prev, cur = cur, nxt
        loops.append(loop)
    return loops


def _earclip_2d(P: np.ndarray) -> list[tuple[int, int, int]] | None:
    """Triangula un polígono 2D simple por recorte de orejas (sin vértices
    nuevos). Devuelve índices a P, o None si no es un polígono simple."""
    n = len(P)
    if n < 3:
        return []
    idx = list(range(n))
    area = 0.5 * sum(
        P[i, 0] * P[(i + 1) % n, 1] - P[(i + 1) % n, 0] * P[i, 1] for i in range(n)
    )
    if area < 0:
        idx = idx[::-1]

    def in_tri(p, a, b, c):
        d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(d) < 1e-12:
            return False
        u = ((b[1] - c[1]) * (p[0] - c[0]) + (c[0] - b[0]) * (p[1] - c[1])) / d
        v = ((c[1] - a[1]) * (p[0] - c[0]) + (a[0] - c[0]) * (p[1] - c[1])) / d
        return u >= -1e-9 and v >= -1e-9 and u + v <= 1 + 1e-9

    tris: list = []
    guard = 0
    while len(idx) > 3 and guard < 3 * n + 10:
        guard += 1
        m = len(idx)
        found = False
        for ii in range(m):
            i0, i1, i2 = idx[(ii - 1) % m], idx[ii], idx[(ii + 1) % m]
            a, b, c = P[i0], P[i1], P[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 0:
                continue  # vértice reflex
            if any(jj not in (i0, i1, i2) and in_tri(P[jj], a, b, c) for jj in idx):
                continue
            tris.append((i0, i1, i2))
            del idx[ii]
            found = True
            break
        if not found:
            return None
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris


def _split_pinch_vertices(V: np.ndarray, F: np.ndarray):
    """Divide los vértices PELLIZCO (vértice de borde compartido por 2+ abanicos de
    caras que solo se tocan en ese vértice, como un 8). Rompen el encadenado de
    bordes y la topología de disco que necesita SLIM. Cada abanico extra recibe
    una copia del vértice. Devuelve (V2, F2, to_orig) con to_orig[i] = índice
    del vértice original (identidad si no hay pellizcos).
    """
    ec: dict = defaultdict(int)
    for f in F:
        for a, b in ((f[0], f[1]), (f[1], f[2]), (f[2], f[0])):
            ec[(min(int(a), int(b)), max(int(a), int(b)))] += 1
    bdeg: dict = defaultdict(int)
    for (a, b), c in ec.items():
        if c == 1:
            bdeg[a] += 1
            bdeg[b] += 1
    pinch = [v for v, d in bdeg.items() if d > 2]
    to_orig = np.arange(len(V))
    if not pinch:
        return V, F, to_orig
    F2 = F.copy()
    newV, new_to = [], []
    vert_faces: dict = defaultdict(list)
    for fi, f in enumerate(F):
        for v in f:
            if int(v) in pinch:
                vert_faces[int(v)].append(fi)
    for v in pinch:
        faces = vert_faces[v]
        # abanicos: caras conectadas por una arista interior (v,x) compartida
        edge_faces: dict = defaultdict(list)
        for fi in faces:
            for x in F[fi]:
                if int(x) != v:
                    edge_faces[int(x)].append(fi)
        parent = {fi: fi for fi in faces}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        for fs in edge_faces.values():
            for other in fs[1:]:
                parent[find(other)] = find(fs[0])
        groups: dict = defaultdict(list)
        for fi in faces:
            groups[find(fi)].append(fi)
        for gfaces in list(groups.values())[1:]:
            nid = len(V) + len(newV)
            newV.append(V[v])
            new_to.append(v)
            for fi in gfaces:
                F2[fi][F2[fi] == v] = nid
    V2 = np.vstack([V, np.asarray(newV)])
    return V2, F2, np.concatenate([to_orig, np.asarray(new_to, dtype=to_orig.dtype)])


def _largest_face_component(V: np.ndarray, F: np.ndarray):
    """Se queda con la componente de caras (conexa por ARISTAS) de mayor área.

    Tras dividir los pellizcos, triángulos sueltos o islas unidas al parche solo
    por un vértice quedan desconectados: no se pueden aplanar junto con el resto
    (y su lazo de 3 aristas, al "taparse", crea una cara duplicada que colapsa la
    init). Se descartan y se compactan los vértices. Devuelve (V2, F2, to_orig).
    """
    m = len(F)
    ekey: dict = {}
    rows, cols = [], []
    for fi in range(m):
        a, b, c = int(F[fi, 0]), int(F[fi, 1]), int(F[fi, 2])
        for u, w in ((a, b), (b, c), (c, a)):
            k = (u, w) if u < w else (w, u)
            if k in ekey:
                rows.append(ekey[k])
                cols.append(fi)
            else:
                ekey[k] = fi
    g = sp.csr_matrix((np.ones(len(rows)), (rows, cols)), shape=(m, m))
    ncomp, lab = connected_components_cs(g)
    if ncomp == 1:
        return V, F, np.arange(len(V))
    area = _triangle_areas(V, F)
    keep_lab = int(np.argmax(np.bincount(lab, weights=area)))
    F1 = F[lab == keep_lab]
    used, inv = np.unique(F1, return_inverse=True)
    return V[used], inv.reshape(-1, 3), used


def _fill_inner_holes(V: np.ndarray, F: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Tapa los huecos INTERIORES del parche (aberturas: agujeros, slots, bolsillos)
    SOLO para el solve de SLIM. Devuelve (V_solve, F_solve): el llamador usa V/F
    originales para la salida, así las aberturas quedan en el patrón pero ya no
    distorsionan el aplanado.

    Cada hueco se tapa con un abanico desde un vértice nuevo en el centroide de su
    borde (siempre triangula). Un parche con bordes múltiples sin tapar es
    multiplemente conexo y SLIM lo pellizca/colapsa; por eso NO es todo-o-nada
    por hueco (antes un solo hueco no plano impedía tapar los otros 30).
    Si el parche ya es un disco simple (≤1 borde) no hace nada.
    """
    loops = _boundary_loops_simple(F)
    if len(loops) <= 1:
        return V, F
    loops.sort(key=len, reverse=True)        # el mayor = borde exterior
    extra: list = []
    new_verts: list = []
    n0 = len(V)
    for loop in loops[1:]:
        if len(loop) < 3:
            continue
        pts = V[loop]
        c = pts.mean(axis=0)
        # Abanico desde el centroide: siempre triangula y nunca crea caras duplicadas
        # (el recorte de orejas en un plano sí, en bordes con "espolones": una cara
        # existente que sobresale hacia el hueco se duplicaba y colapsaba la init).
        cid = n0 + len(new_verts)
        new_verts.append(c)
        m = len(loop)
        for i in range(m):
            extra.append([loop[i], loop[(i + 1) % m], cid])
    if not extra:
        return V, F
    V2 = np.vstack([V, np.asarray(new_verts)]) if new_verts else V
    F2 = np.vstack([F, np.asarray(extra, dtype=F.dtype)])
    # Winding consistente (las orejas/abanicos pueden quedar invertidos vs la superficie)
    tmp = trimesh.Trimesh(vertices=V2, faces=F2, process=False)
    tmp.fix_normals()
    F2 = np.asarray(tmp.faces)
    # Debe quedar un DISCO simple (1 borde); si no, no se toca el parche.
    if len(_boundary_loops_simple(F2)) != 1:
        return V, F
    return V2, F2


def _run_slim(Vf, Ff, V_solve, F_solve, uv0) -> np.ndarray:
    """Itera SLIM en lotes con parada adaptativa por estiramiento p95."""
    data = igl.slim_precompute(
        Vf, Ff, np.asfortranarray(uv0), igl.SYMMETRIC_DIRICHLET,
        np.zeros(0, dtype=np.int32), np.zeros((0, 2)), 0.0,
    )
    uv = uv0
    prev_metric, calm, done = None, 0, 0
    while done < SLIM_MAX_ITERS:
        uv = np.asarray(igl.slim_solve(data, SLIM_BATCH))
        done += SLIM_BATCH
        s1, s2 = principal_stretches(V_solve, F_solve, uv, F_solve)
        metric = float(np.percentile(np.maximum(s1 - 1.0, 1.0 - s2), 95))
        if not np.isfinite(metric):
            continue
        if prev_metric is not None and abs(prev_metric - metric) < SLIM_STRETCH_TOL:
            calm += 1
        else:
            calm = 0
        prev_metric = metric
        if done >= SLIM_MIN_ITERS and calm >= SLIM_PATIENCE:
            break
    return uv


def _slim_uv(patch_id: int, V: np.ndarray, F: np.ndarray, areas_3d: np.ndarray) -> np.ndarray:
    """Aplanado por minimización de energía (SLIM, símil ExactFlat).

    Reparte la distorsión (tensión/compresión) en TODA la pieza, de modo que un
    disco con doble curvatura cae en UN net continuo (se estira un poco) en vez
    de rasgarse en abanico como el desplegado rígido. Pasos:

      1. Init Tutte uniforme (flip-free; no se confunde con triángulos obtusos).
      2. SLIM (SYMMETRIC_DIRICHLET) iterado en lotes hasta converger.

    Solo se rechaza si el parche es CERRADO (sin borde) o si el resultado final
    queda con volteos/colapsa (malla degenerada): en esos casos sí hace falta
    una costura que abra la superficie.
    """
    # Tapar huecos interiores (aberturas) SOLO para el solve: un parche con
    # agujeros es multiplemente-conexo y SLIM lo contrae/pellizca. Se resuelve
    # sobre el disco tapado; la salida usa F original (las aberturas se conservan).
    cache_key = hashlib.sha1(
        np.ascontiguousarray(V, dtype=np.float64).tobytes()
        + np.ascontiguousarray(F, dtype=np.int64).tobytes()
    ).hexdigest()
    cached = _SLIM_CACHE.get(cache_key)
    if cached is not None:
        _SLIM_CACHE.move_to_end(cache_key)
        return cached.copy()

    V_solve, F_solve = _fill_inner_holes(V, F)

    Vf = np.asfortranarray(V_solve[:, :3].astype(np.float64))
    Ff = np.asfortranarray(F_solve.astype(np.int32))
    boundary = igl.boundary_loop(F_solve)
    if boundary.size == 0:
        raise FlattenError(
            f"El parche {patch_id} es una superficie cerrada: marca una costura "
            "que lo abra (o que corte el lazo) para poder aplanarlo."
        )

    # Inicializaciones candidatas (la 1a que arranque y termine sin volteos gana):
    #   b) Tutte uniforme + borde CÍRCULO: clásica, converge rápido (la de siempre)
    #   a) valor medio + borde ELIPSE: respaldo para piezas alargadas/irregulares
    #      donde la clásica se degenera (cuellos angostos -> áreas ~1e-20 -> volteos)
    def _init_a():
        return _tutte_mvc_uv(V_solve, F_solve, boundary, _boundary_ellipse_uv(V_solve, boundary))

    def _init_b():
        return _tutte_uniform_uv(
            V_solve, F_solve, boundary, igl.map_vertices_to_circle(Vf, boundary)
        )

    uv, any_init = None, False
    for make_init in (_init_b, _init_a):
        uv0 = make_init()
        if not np.all(np.isfinite(uv0)) or _count_flips(uv0, F_solve) > 0:
            continue                     # init con volteos: SLIM no puede deshacerlos
        any_init = True
        cand = _run_slim(Vf, Ff, V_solve, F_solve, uv0)
        if _count_flips(cand, F_solve) == 0 and np.all(np.isfinite(cand)):
            uv = cand
            break
    if uv is None:
        if not any_init:
            raise FlattenError(
                f"No se pudo construir un punto de partida sin volteos para el parche "
                f"{patch_id}: la superficie envuelve sobre sí misma o tiene triángulos "
                "degenerados. Agrega una costura que la ABRA (o limpia la Cara A)."
            )
        raise FlattenError(
            f"El parche {patch_id} no se pudo aplanar sin volteos: la superficie "
            "envuelve sobre sí misma. Agrega una costura que la ABRA y vuelve a generar."
        )

    uv = uv[: len(V)]          # quitar vértices auxiliares de los abanicos de hueco
    centered = uv - uv.mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    uv = centered @ vt.T
    total_3d, total_2d = float(areas_3d.sum()), float(_triangle_areas(uv, F).sum())
    if total_2d <= 0 or not np.all(np.isfinite(uv)):
        raise FlattenError(f"El aplanado del parche {patch_id} colapsó (área 0)")
    uv = uv * np.sqrt(total_3d / total_2d)
    _SLIM_CACHE[cache_key] = uv.copy()
    while len(_SLIM_CACHE) > _SLIM_CACHE_MAX:
        _SLIM_CACHE.popitem(last=False)
    return uv


# ----------------------------------------------------------- Desplegado rígido
def _unfold_point(A, B, r1, r2, ref):
    """Tercer vértice del triángulo dados A,B y las longitudes reales r1,r2.

    Se elige el lado OPUESTO a `ref` (el tercer vértice del padre) para que
    la cara hija se abata hacia afuera y no encima del padre.
    """
    d = B - A
    L = float(np.linalg.norm(d))
    if L < 1e-9:
        return A.copy()
    x = (L * L + r1 * r1 - r2 * r2) / (2.0 * L)
    h2 = r1 * r1 - x * x
    h = np.sqrt(h2) if h2 > 0 else 0.0
    dirx = d / L
    perp = np.array([-dirx[1], dirx[0]])
    foot = A + x * dirx
    c1, c2 = foot + h * perp, foot - h * perp
    side_ref = d[0] * (ref[1] - A[1]) - d[1] * (ref[0] - A[0])
    side_c1 = d[0] * (c1[1] - A[1]) - d[1] * (c1[0] - A[0])
    return c1 if side_ref * side_c1 < 0 else c2


def _seam_cut_local_edges(patch: dict) -> set[tuple[int, int]]:
    """Aristas de costura DENTRO del parche, en índices locales del submalla.

    Una costura abre la superficie para aplanarla aunque no separe en dos
    piezas (p. ej. una hendidura a lo largo de un rollo): por eso el unfold
    NO debe cruzarla con su árbol de expansión.
    """
    orig = np.asarray(patch["orig_vertex_ids"])
    g2l = {int(g): i for i, g in enumerate(orig)}
    cuts: set[tuple[int, int]] = set()
    for seam in SESSION.seams.values():
        for a, b in seam.edge_path:
            if a in g2l and b in g2l:
                la, lb = g2l[a], g2l[b]
                cuts.add((min(la, lb), max(la, lb)))
    return cuts


def _unfold_uv(
    mesh,
    V: np.ndarray,
    F: np.ndarray,
    areas_3d: np.ndarray,
    cut_edges: set[tuple[int, int]] | None = None,
):
    """Desplegado rígido por aristas. Devuelve (coords, faces_2d, orig_to_uv).

    Cada cara conserva su forma real; las aristas del árbol de expansión se
    sueldan (pliegues) y las demás quedan separadas (cortes). Un vértice de
    malla en un corte se DUPLICA: por eso coords no se indexa por vértice
    original sino por esquina del net. `cut_edges` (aristas locales de
    costura) se EXCLUYEN del árbol: el net se abre por ellas.
    """
    n_faces = len(F)
    cut_edges = cut_edges or set()

    # Grafo cara-cara con la arista (par de vértices locales) que comparten,
    # saltando las aristas de costura (cortes que abren la superficie).
    nbr: dict[int, list[tuple[int, tuple[int, int]]]] = defaultdict(list)
    for (f1, f2), (va, vb) in zip(mesh.face_adjacency, mesh.face_adjacency_edges):
        if (min(int(va), int(vb)), max(int(va), int(vb))) in cut_edges:
            continue
        nbr[int(f1)].append((int(f2), (int(va), int(vb))))
        nbr[int(f2)].append((int(f1), (int(va), int(vb))))

    coords: list[np.ndarray] = []
    faces_2d = np.full((n_faces, 3), -1, dtype=np.int64)
    placed: dict[int, dict[int, int]] = {}   # cara -> {vertice_local -> idx_out}
    orig_of: list[int] = []                  # idx_out -> vertice_local

    def new_vertex(pt, local_v):
        coords.append(np.asarray(pt, dtype=np.float64))
        orig_of.append(local_v)
        return len(coords) - 1

    def set_face(f, idx_by_v):
        faces_2d[f] = [idx_by_v[int(v)] for v in F[f]]

    def place_root(f):
        i, j, k = (int(v) for v in F[f])
        lij = float(np.linalg.norm(V[i] - V[j]))
        lik = float(np.linalg.norm(V[i] - V[k]))
        ljk = float(np.linalg.norm(V[j] - V[k]))
        A = np.array([0.0, 0.0])
        B = np.array([lij, 0.0])
        x = (lij * lij + lik * lik - ljk * ljk) / (2.0 * lij) if lij > 1e-9 else 0.0
        y2 = lik * lik - x * x
        C = np.array([x, np.sqrt(y2) if y2 > 0 else 0.0])
        idx = {i: new_vertex(A, i), j: new_vertex(B, j), k: new_vertex(C, k)}
        placed[f] = idx
        set_face(f, idx)

    def place_child(f, parent, va, vb):
        pidx = placed[parent]
        A, B = coords[pidx[va]], coords[pidx[vb]]
        vc = int(next(v for v in F[f] if v != va and v != vb))
        r1 = float(np.linalg.norm(V[va] - V[vc]))
        r2 = float(np.linalg.norm(V[vb] - V[vc]))
        vp3 = int(next(v for v in F[parent] if v != va and v != vb))
        C = _unfold_point(A, B, r1, r2, coords[pidx[vp3]])
        idx = {va: pidx[va], vb: pidx[vb], vc: new_vertex(C, vc)}
        placed[f] = idx
        set_face(f, idx)

    # Crecer el árbol desde un EXTREMO a lo largo del eje principal (no desde
    # el centro): el net se desenrolla secuencialmente y faneando menos, así
    # las tiras alargadas salen con mucha menos cizalla.
    centroids = V[F].mean(axis=1)
    cc = centroids - centroids.mean(axis=0)
    _, _, axes = np.linalg.svd(cc, full_matrices=False)
    proj = cc @ axes[0]                  # posición de cada cara a lo largo del eje
    seed_order = np.argsort(proj)        # arrancar desde el extremo

    # BFS por componentes (los parches son conexos; el bucle es por robustez)
    visited = set()
    for seed in seed_order:
        seed = int(seed)
        if seed in visited:
            continue
        place_root(seed)
        visited.add(seed)
        queue = deque([seed])
        while queue:
            f = queue.popleft()
            for g, (va, vb) in nbr[f]:
                if g in visited:
                    continue
                visited.add(g)
                place_child(g, f, va, vb)
                queue.append(g)

    coords = np.asarray(coords)
    # Representante 2D por vértice local original (para muescas del DXF)
    orig_to_uv = np.zeros((len(V), 2))
    for out_idx, local_v in enumerate(orig_of):
        orig_to_uv[local_v] = coords[out_idx]
    return coords, faces_2d, orig_to_uv


# Tope de seguridad para no agotar memoria en mallas patológicas. Por
# encima, se omite el chequeo (el solape se ve en la vista 2D igual).
MAX_OVERLAP_CHECK_FACES = 250_000


def _net_overlaps(coords: np.ndarray, faces_2d: np.ndarray, total_area: float) -> bool:
    """¿El net se solapa? Compara el área de la UNIÓN con la suma de caras.

    Como el desplegado es isométrico, suma(área caras) == área 3D. Si la
    unión cubre menos, hay solape real (no es un falso positivo de geometría
    teselada, como sí lo daría revisar la simplicidad del contorno).
    shapely.unary_union está optimizado en C: ~3 s en 50k triángulos.
    """
    if len(faces_2d) == 0 or len(faces_2d) > MAX_OVERLAP_CHECK_FACES:
        return False
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    union_area = unary_union([Polygon(coords[f]) for f in faces_2d]).area
    return union_area < total_area * 0.999


# ----------------------------------------------------------- Despacho
# Un "relajado" con tensión p95 por encima de esto no es un patrón utilizable
# (casos reales: +9833%): es un aplanado colapsado que SLIM no marca como volteo.
RELAX_MAX_TENSION_P95_PCT = 200.0


def _wrap_hint(mesh) -> str:
    """Diagnóstico corto de por qué una Cara A no aplana: cuánto 'envuelve'."""
    try:
        n, a = np.asarray(mesh.face_normals), np.asarray(mesh.area_faces)
        mean = (n * a[:, None]).sum(axis=0)
        mean /= max(float(np.linalg.norm(mean)), 1e-12)
        ang = np.degrees(np.arccos(np.clip(n @ mean, -1.0, 1.0)))
        pct = 100.0 * float(a[ang > 90.0].sum() / max(a.sum(), 1e-12))
        return f" ({pct:.0f}% del área mira más de 90° respecto a la dirección media)"
    except Exception:  # noqa: BLE001
        return ""


def flatten_patch(patch_id: int, method: str = "unfold") -> dict:
    """Aplana un parche con el método dado -> UV en mm + distorsión."""
    patch = SESSION.patches[patch_id]  # KeyError -> 404 en la ruta
    mesh = patch["mesh"]
    V0 = np.asarray(mesh.vertices, dtype=np.float64)
    F0 = np.asarray(mesh.faces, dtype=np.int64)

    # Cortar el parche en disco a lo largo de las costuras: hace que ARAP/LSCM
    # converjan y que el unfold no cruce la costura, aunque no separe en dos.
    cut_edges = _seam_cut_local_edges(patch)
    if cut_edges:
        V, F, cut_to_orig = _cut_mesh_along_edges(V0, F0, cut_edges)
    else:
        V, F, cut_to_orig = V0, F0, np.arange(len(V0))
    # Vértices pellizco (borde en forma de 8): se dividen para que el parche sea
    # un disco (con huecos) y no un lazo de borde partido en dos.
    V, F, split_to_orig = _split_pinch_vertices(V, F)
    cut_to_orig = cut_to_orig[split_to_orig]
    V, F, keep_to_orig = _largest_face_component(V, F)   # descarta islas sueltas
    cut_to_orig = cut_to_orig[keep_to_orig]
    work_mesh = trimesh.Trimesh(vertices=V, faces=F, process=False)
    areas_3d = _triangle_areas(V, F)

    overlap = False
    if method == "relax":
        try:
            uv = _slim_uv(patch_id, V, F, areas_3d)
        except FlattenError as e:
            raise FlattenError(f"{e}{_wrap_hint(mesh)}") from e
        faces_2d, vert_to_uv = F, uv
    elif method == "project":
        uv = _project_uv(V, _mean_normal(work_mesh, areas_3d))
        faces_2d, vert_to_uv = F, uv
    else:  # unfold (sobre el disco ya cortado: el árbol no cruza la costura)
        uv, faces_2d, vert_to_uv = _unfold_uv(work_mesh, V, F, areas_3d)
        overlap = _net_overlaps(uv, faces_2d, float(areas_3d.sum()))

    # Trasladar el origen a (0, 0)
    shift = uv.min(axis=0)
    uv = uv - shift
    vert_to_uv = vert_to_uv - shift
    # orig_to_uv: 2D por vértice local ORIGINAL (para muescas del DXF)
    orig_to_uv = np.zeros((len(V0), 2))
    orig_to_uv[cut_to_orig] = vert_to_uv

    # Estiramiento principal por triángulo (σ1=tensión, σ2=compresión).
    # Mapa de deformación tipo ExactFlat: dónde el material se estira o frunce.
    s1, s2 = principal_stretches(V, F, uv, faces_2d)
    tension = np.maximum(s1 - 1.0, 0.0)        # estiramiento
    compression = np.maximum(1.0 - s2, 0.0)    # fruncido
    # Valor con signo para el heatmap divergente (rojo=tensión, azul=compresión)
    strain = np.where(tension >= compression, tension, -compression) * 100.0

    # Distorsión de área (se conserva para continuidad)
    distortion = (s1 * s2)
    deviation = np.abs(distortion - 1.0)
    mean_pct = float(np.average(deviation, weights=areas_3d) * 100.0)

    result = {
        "patch_id": patch_id,
        "method": method,
        "uv": np.round(uv, 2).tolist(),
        "faces": faces_2d.tolist(),
        "strain": np.round(strain, 1).tolist(),
        # Estiramientos principales por cara (σ1≥σ2) para el modo TOLERANCIA:
        # el frontend compara contra los límites warp/weft/compresión de la TDS.
        "s1": np.round(s1, 3).tolist(),
        "s2": np.round(s2, 3).tolist(),
        "distortion": np.round(distortion, 4).tolist(),
        "mean_distortion_pct": round(mean_pct, 2),
        "max_distortion_pct": round(float(deviation.max() * 100.0), 2),
        "tension_pct": round(float(np.percentile(tension, 95) * 100.0), 1),
        "compression_pct": round(float(np.percentile(compression, 95) * 100.0), 1),
        "overlap": bool(overlap),
        "width_mm": round(float(uv[:, 0].max()), 1),
        "height_mm": round(float(uv[:, 1].max()), 1),
    }
    if method == "relax" and result["tension_pct"] > RELAX_MAX_TENSION_P95_PCT:
        raise FlattenError(
            f"El aplanado relajado del parche {patch_id} quedó colapsado "
            f"(tensión p95 +{result['tension_pct']:.0f}%): la Cara A envuelve sobre sí "
            f"misma{_wrap_hint(mesh)}. Agrega costuras que la abran en piezas más "
            "planas, o prueba Desplegado / Proyección."
        )
    patch["uv"] = uv
    patch["faces_2d"] = np.asarray(faces_2d)
    patch["orig_to_uv"] = orig_to_uv
    patch["flatten_result"] = result
    return result


def flatten_all_patches(method: str = "unfold") -> dict:
    """Aplana todos los parches y los acomoda en fila (layout para DXF/vista).

    Resiliente: si UN parche no se puede aplanar (envuelve, doble curvatura
    fuerte, etc.) se reporta en `failed` y los demás se entregan igual, en
    vez de tumbar todo el flujo.
    """
    if not SESSION.patches:
        raise FlattenError("No hay parches: ejecuta primero la segmentación.")

    results, failed = [], []
    offset_x = 0.0
    for pid in sorted(SESSION.patches):
        try:
            result = flatten_patch(pid, method=method)
        except FlattenError as e:
            failed.append({"patch_id": pid, "error": str(e)})
            continue
        patch = SESSION.patches[pid]
        patch["uv"][:, 0] += offset_x          # mover a su sitio en el layout
        patch["orig_to_uv"][:, 0] += offset_x
        result["uv"] = np.round(patch["uv"], 2).tolist()
        offset_x += patch["flatten_result"]["width_mm"] + PATCH_GAP_MM
        results.append(result)

    if not results:
        raise FlattenError(
            failed[0]["error"] if failed
            else "No se pudo aplanar ningún parche."
        )
    return {"patches": results, "failed": failed}
