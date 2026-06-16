"""Tests del imprint de costura (partir triángulos por la línea de corte)."""
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core import seam_cut  # noqa: E402


def _grid(nx=4, ny=2):
    """Malla plana z=0: nx*ny celdas, cada una en 2 triángulos."""
    xs, ys = np.arange(nx + 1), np.arange(ny + 1)
    verts = np.array([[x, y, 0.0] for y in ys for x in xs], dtype=float)
    faces, w = [], nx + 1
    for j in range(ny):
        for i in range(nx):
            a = j * w + i
            faces += [[a, a + 1, a + w], [a + 1, a + 1 + w, a + w]]
    return trimesh.Trimesh(vertices=verts, faces=np.array(faces), process=False)


def _on_line(pts, a, b, tol=1e-6):
    d = b - a
    return all(
        abs(d[0] * (p[1] - a[1]) - d[1] * (p[0] - a[0])) <= tol for p in pts
    )


def _area(V, F):
    return float(trimesh.Trimesh(vertices=V, faces=F, process=False).area)


def test_imprint_splits_triangles_along_line():
    # Malla plana, costura diagonal entre dos vértices que cruza triángulos.
    mesh = _grid(4, 2)
    va, vb = 0, 9                      # vértices (0,0) y (4,1)
    res = seam_cut.imprint_seam(mesh, [va, vb])
    assert res is not None

    V2, F2, cuts = res["vertices"], res["faces"], res["cut_edges"]
    assert _area(V2, F2) == pytest.approx(_area(mesh.vertices, mesh.faces), rel=1e-6)
    assert len(V2) > len(mesh.vertices)
    a, b = np.asarray(mesh.vertices[va]), np.asarray(mesh.vertices[vb])
    assert _on_line(V2[len(mesh.vertices):], a, b)          # cruces sobre la línea
    cut_pts = np.array([V2[v] for e in cuts for v in e])
    assert _on_line(cut_pts, a, b)
    for (p, q), (r, s) in zip(cuts, cuts[1:]):              # cadena continua
        assert {q} & {r, s} or {p} & {r, s}
    assert len(res["face_source"]) == len(F2)


@pytest.mark.xfail(
    reason="LIMITACIÓN CONOCIDA: el caminado recto heurístico (apuntar al "
    "destino proyectado por cara) se desvía en superficies curvas. El camino "
    "recto exacto sobre una superficie curva es el problema de las geodésicas; "
    "falta un walk con transporte paralelo (unfolding) o una geodésica robusta "
    "a no-manifold. Ver memoria MEJORA-15.",
    strict=False,
)
def test_imprint_on_curved_mesh_preserves_area():
    # En una superficie CURVA (semicilindro) el camino recto va sobre la malla
    # (no por un chord 3D). Debe imprimir sin colapsar: área conservada, sin
    # caras degeneradas, aristas de corte encadenadas.
    from samples.make_samples import half_cylinder

    mesh = half_cylinder(50.0, 200.0)
    V = np.asarray(mesh.vertices)
    va = int(np.argmin(V[:, 0] + V[:, 1]))
    vb = int(np.argmax(V[:, 0] + V[:, 1]))
    res = seam_cut.imprint_seam(mesh, [va, vb])
    assert res is not None, "el caminado recto debería resolver el semicilindro"

    V2, F2, cuts = res["vertices"], res["faces"], res["cut_edges"]
    assert _area(V2, F2) == pytest.approx(_area(mesh.vertices, mesh.faces), rel=1e-4)
    # sin triángulos degenerados (área ~0)
    areas = trimesh.Trimesh(vertices=V2, faces=F2, process=False).area_faces
    assert (areas > 1e-9).all()
    assert len(cuts) >= 2
