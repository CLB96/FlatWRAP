"""Tests del imprint de costura (partir triángulos por la línea de corte)."""
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import seam_cut  # noqa: E402


def _grid(nx=4, ny=2):
    """Malla plana z=0: nx*ny celdas, cada una en 2 triángulos."""
    xs, ys = np.arange(nx + 1), np.arange(ny + 1)
    verts = np.array([[x, y, 0.0] for y in ys for x in xs], dtype=float)
    faces = []
    w = nx + 1
    for j in range(ny):
        for i in range(nx):
            a = j * w + i
            faces += [[a, a + 1, a + w], [a + 1, a + 1 + w, a + w]]
    return trimesh.Trimesh(vertices=verts, faces=np.array(faces), process=False)


def _faces_for_points(mesh, pts):
    """Cara que contiene cada punto (malla plana z=0), sin depender de rtree.

    Usa coordenadas baricéntricas 2D y elige el triángulo donde el punto está
    'más adentro' (resuelve aristas/vértices de forma determinista)."""
    V = np.asarray(mesh.vertices)[:, :2]
    F = np.asarray(mesh.faces)
    out = []
    for p in pts[:, :2]:
        best, best_score = 0, -1e9
        for fi, (a, b, c) in enumerate(F):
            v0, v1, v2 = V[a], V[b], V[c]
            d = (v1[1] - v2[1]) * (v0[0] - v2[0]) + (v2[0] - v1[0]) * (v0[1] - v2[1])
            if abs(d) < 1e-12:
                continue
            w0 = ((v1[1] - v2[1]) * (p[0] - v2[0]) + (v2[0] - v1[0]) * (p[1] - v2[1])) / d
            w1 = ((v2[1] - v0[1]) * (p[0] - v2[0]) + (v0[0] - v2[0]) * (p[1] - v2[1])) / d
            score = min(w0, w1, 1 - w0 - w1)
            if score > best_score:
                best_score, best = score, fi
        out.append(best)
    return np.array(out)


def _on_line(pts, a, b, tol=1e-6):
    """¿Todos los puntos (xy) colineales con el segmento a-b?"""
    d = b - a
    for p in pts:
        cross = d[0] * (p[1] - a[1]) - d[1] * (p[0] - a[0])
        if abs(cross) > tol:
            return False
    return True


def test_imprint_splits_triangles_along_line():
    mesh = _grid(4, 2)
    a = np.array([0.0, 0.0, 0.0])   # vértice (0,0)
    b = np.array([4.0, 1.0, 0.0])   # vértice (4,1): diagonal que cruza triángulos
    pts = np.linspace(a, b, 60)
    faces = _faces_for_points(mesh, pts)

    res = seam_cut.imprint_seam(mesh, pts, faces)
    assert res is not None, "el imprint debería resolver este caso simple"

    V2, F2, cuts = res["vertices"], res["faces"], res["cut_edges"]
    # Área conservada: la re-triangulación tesela exactamente la misma región
    area_orig = float(mesh.area)
    area_new = float(trimesh.Trimesh(vertices=V2, faces=F2, process=False).area)
    assert area_new == pytest.approx(area_orig, rel=1e-6)

    # Hay vértices nuevos (cruces) y TODOS caen sobre la línea de la costura
    assert len(V2) > len(mesh.vertices)
    new_pts = V2[len(mesh.vertices):]
    assert _on_line(new_pts, a, b)

    # Las aristas de corte caen sobre la línea y forman una cadena continua
    assert len(cuts) >= 1
    cut_pts = np.array([V2[v] for e in cuts for v in e])
    assert _on_line(cut_pts, a, b)
    # cadena: cada arista comparte un vértice con la siguiente
    for (p, q), (r, s) in zip(cuts, cuts[1:]):
        assert q == r or q == s or p == r or p == s


def test_imprint_returns_none_on_revisited_face():
    # Una "línea" que vuelve sobre la misma cara no está soportada -> None (fallback)
    mesh = _grid(2, 2)
    f0 = 0
    c = mesh.triangles_center[f0]
    pts = np.array([c, c + [0.05, 0, 0], c])  # entra y vuelve a la misma cara
    faces = np.array([f0, f0, f0])
    assert seam_cut.imprint_seam(mesh, pts, faces) is None
