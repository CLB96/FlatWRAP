"""Genera STLs de prueba para FlatWrap.

Uso:  python samples/make_samples.py
Crea: medio_cilindro.stl  (r=50, L=200 -> patrón exacto de 157.1 x 200 mm)
      silla_montar.stl    (paraboloide hiperbólico: distorsión inevitable)
      panel_plastico.stl  (pieza SÓLIDA con espesor 3 mm: cara A abovedada
                           + paredes + reverso, para probar la selección
                           de cara A por relleno de ángulo)
"""

from pathlib import Path

import numpy as np
import trimesh

OUT = Path(__file__).resolve().parent


def grid_mesh(xs: np.ndarray, ys: np.ndarray, z_fn) -> trimesh.Trimesh:
    """Malla triangular de una superficie z = f(x, y) sobre una grilla."""
    nx, ny = len(xs), len(ys)
    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    zz = z_fn(xx, yy)
    vertices = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])

    faces = []
    for i in range(nx - 1):
        for j in range(ny - 1):
            a = i * ny + j
            b = a + ny
            faces.append([a, b, a + 1])
            faces.append([b, b + 1, a + 1])
    return trimesh.Trimesh(vertices=vertices, faces=np.array(faces))


def half_cylinder(radius=50.0, length=200.0, n_theta=80, n_len=60) -> trimesh.Trimesh:
    """Casco de medio cilindro (superficie abierta, desarrollable)."""
    theta = np.linspace(0, np.pi, n_theta)
    y = np.linspace(0, length, n_len)
    tt, yy = np.meshgrid(theta, y, indexing="ij")
    vertices = np.column_stack(
        [radius * np.cos(tt).ravel(), yy.ravel(), radius * np.sin(tt).ravel()]
    )
    faces = []
    for i in range(n_theta - 1):
        for j in range(n_len - 1):
            a = i * n_len + j
            b = a + n_len
            faces.append([a, b, a + 1])
            faces.append([b, b + 1, a + 1])
    return trimesh.Trimesh(vertices=vertices, faces=np.array(faces))


def solid_panel(
    width=200.0, depth=150.0, thickness=3.0, dome=12.0, nx=60, ny=46
) -> trimesh.Trimesh:
    """Pieza plástica SÓLIDA: cara A abovedada + paredes + reverso plano.

    La cara A (arriba) se une a las paredes en un canto de ~90°: el relleno
    por ángulo debe seleccionar solo la bóveda y detenerse ahí.
    """
    xs = np.linspace(-width / 2, width / 2, nx)
    ys = np.linspace(-depth / 2, depth / 2, ny)
    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    # Bóveda cilíndrica suave a lo largo de X, sobre el espesor base
    top_z = thickness + dome * (1.0 - (2.0 * xx / width) ** 2)

    top = np.column_stack([xx.ravel(), yy.ravel(), top_z.ravel()])
    bottom = np.column_stack([xx.ravel(), yy.ravel(), np.zeros(xx.size)])
    vertices = np.vstack([top, bottom])
    n = nx * ny

    def quad(a, b, c, d):
        return [[a, b, c], [a, c, d]]

    faces = []
    for i in range(nx - 1):
        for j in range(ny - 1):
            a = i * ny + j
            b = (i + 1) * ny + j
            faces += quad(a, a + 1, b + 1, b)            # cara A (arriba)
            faces += quad(n + a, n + b, n + b + 1, n + a + 1)  # reverso

    # Paredes perimetrales (top ring -> bottom ring)
    def wall(idx_top):
        for k in range(len(idx_top) - 1):
            t0, t1 = idx_top[k], idx_top[k + 1]
            faces.append([t0, t1, n + t1])
            faces.append([t0, n + t1, n + t0])

    wall([i * ny + 0 for i in range(nx)])
    wall([i * ny + (ny - 1) for i in range(nx)][::-1])
    wall([0 * ny + j for j in range(ny)][::-1])
    wall([(nx - 1) * ny + j for j in range(ny)])

    mesh = trimesh.Trimesh(vertices=vertices, faces=np.array(faces), process=True)
    mesh.fix_normals()
    return mesh


if __name__ == "__main__":
    half_cylinder().export(OUT / "medio_cilindro.stl")
    saddle = grid_mesh(
        np.linspace(-100, 100, 70),
        np.linspace(-100, 100, 70),
        lambda x, y: (x**2 - y**2) / 250.0,
    )
    saddle.export(OUT / "silla_montar.stl")
    solid_panel().export(OUT / "panel_plastico.stl")
    print("STLs generados en", OUT)
