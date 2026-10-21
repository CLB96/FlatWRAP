"""Tests de integración del pipeline completo: carga -> costura ->
segmentación -> aplanado -> DXF.

La pieza de prueba es un medio cilindro (r=50, L=200): superficie
DESARROLLABLE cuyo aplanado exacto es un rectángulo de pi*r x L
(157.08 x 200 mm) con distorsión ~0. Eso convierte la geometría en
un oráculo: si el resultado se desvía, el algoritmo está mal.
"""

import sys
from pathlib import Path

import ezdxf
import igl
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from samples.make_samples import half_cylinder, solid_panel  # noqa: E402

from app.core import (  # noqa: E402
    export_dxf,
    flattening,
    geodesics,
    mesh_io,
    segmentation,
    selection,
)
from app.core.session import SESSION  # noqa: E402

RADIUS, LENGTH = 50.0, 200.0
EXPECTED_WIDTH = np.pi * RADIUS  # 157.08 mm


def _all_faces():
    """La cara A de prueba = toda la superficie del medio cilindro."""
    return list(range(len(SESSION.mesh.faces)))


@pytest.fixture()
def cylinder_session():
    """Carga el medio cilindro en la sesión global."""
    stl_bytes = half_cylinder(RADIUS, LENGTH).export(file_type="stl")
    summary = mesh_io.load_mesh(stl_bytes, "medio_cilindro.stl")
    yield summary
    SESSION.reset()


def _edge_boundary_vertices(mesh):
    """Dos vértices en bordes opuestos del cilindro (theta=pi/2, y=0 y y=L)."""
    v = np.asarray(mesh.vertices)
    top = np.flatnonzero((np.abs(v[:, 0]) < 1.0) & (v[:, 1] < 0.1))
    bottom = np.flatnonzero((np.abs(v[:, 0]) < 1.0) & (v[:, 1] > LENGTH - 0.1))
    assert len(top) and len(bottom), "no se hallaron vértices de prueba"
    return int(top[0]), int(bottom[0])


def test_load_mesh(cylinder_session):
    assert cylinder_session.n_faces > 0
    assert not cylinder_session.watertight  # superficie abierta
    dims = np.array(cylinder_session.bbox_max) - np.array(cylinder_session.bbox_min)
    assert dims[1] == pytest.approx(LENGTH, abs=0.5)


def test_seam_follows_surface(cylinder_session):
    a, b = _edge_boundary_vertices(SESSION.mesh)
    result = geodesics.create_seam([a, b])
    # La costura es RECTA entre los dos puntos (generatriz del cilindro):
    # largo ~ LENGTH y sin zigzag (la longitud del trazo ~ la cuerda recta).
    poly = np.array(result["polyline"])
    arc = np.linalg.norm(np.diff(poly, axis=0), axis=1).sum()
    chord = float(np.linalg.norm(poly[-1] - poly[0]))
    assert arc == pytest.approx(LENGTH, rel=0.03)
    assert arc == pytest.approx(chord, rel=0.05)   # recta, no zigzag por aristas
    # El corte por aristas (Dijkstra) sigue existiendo en paralelo
    assert len(result["edge_path"]) >= 1


def test_flood_selects_smooth_cara_a(cylinder_session):
    # El medio cilindro abre 180°; sin filtro de orientación (side=180) el
    # flood toma toda la superficie suave.
    result = selection.flood_select(seed_face=0, angle_deg=35.0, side_deg=180.0)
    assert result["n_faces"] == len(SESSION.mesh.faces)
    assert result["area_cm2"] > 0


def test_flood_side_filter_excludes_back(cylinder_session):
    # Con el filtro de orientación, el flood NO cruza a la mitad opuesta del
    # cilindro (caras que miran a >85° del clic).
    full = selection.flood_select(0, 35.0, side_deg=180.0)["n_faces"]
    filtered = selection.flood_select(0, 35.0, side_deg=85.0)["n_faces"]
    assert filtered < full


def test_flood_respects_seam_as_barrier(cylinder_session):
    # Sin costura (y sin filtro de lado) el flood toma todo el medio cilindro.
    total = len(SESSION.mesh.faces)
    assert selection.flood_select(0, 35.0, side_deg=180.0)["n_faces"] == total
    # Con una costura a lo largo, el flood NO la cruza -> toma solo un lado.
    a, b = _edge_boundary_vertices(SESSION.mesh)
    geodesics.create_seam([a, b])
    r = selection.flood_select(0, 35.0, side_deg=180.0)
    assert 0 < r["n_faces"] < total  # la costura actuó de barrera


def test_cara_a_without_seam_is_one_pattern(cylinder_session):
    # Sin costura, la cara A se mantiene como un solo patrón primario
    result = segmentation.segment_by_seams(_all_faces())
    assert result["n_patches"] == 1
    assert result["warnings"] == []


def test_segment_requires_cara_a(cylinder_session):
    with pytest.raises(segmentation.SegmentationError, match="Cara A"):
        segmentation.segment_by_seams([])


def test_tiny_fragments_are_not_patterns(cylinder_session):
    # Aislar la cara 0 (deseleccionando sus vecinas) crea un fragmento de un
    # solo triángulo: su área es « 3 cm², así que NO debe contar como patrón.
    mesh = SESSION.mesh
    adj = mesh.face_adjacency
    neighbors = set()
    for a, b in adj:
        if a == 0:
            neighbors.add(int(b))
        elif b == 0:
            neighbors.add(int(a))
    selected = [f for f in range(len(mesh.faces)) if f == 0 or f not in neighbors]

    result = segmentation.segment_by_seams(selected)
    assert result["n_patches"] == 1            # solo el cuerpo grande
    assert result["dropped_fragments"] >= 1    # la cara 0 aislada
    # La cara 0 quedó sin patrón asignado
    assert result["face_patch"][0] == -1


def test_open_seam_splits_in_two(cylinder_session):
    a, b = _edge_boundary_vertices(SESSION.mesh)
    geodesics.create_seam([a, b])
    result = segmentation.segment_by_seams(_all_faces())
    assert result["n_patches"] == 2
    assert result["warnings"] == []


def test_floating_seam_warns(cylinder_session):
    # Costura corta en medio de la superficie: no separa nada
    mesh = SESSION.mesh
    v = np.asarray(mesh.vertices)
    interior = np.flatnonzero(
        (v[:, 1] > 80) & (v[:, 1] < 120) & (v[:, 2] > RADIUS * 0.9)
    )
    geodesics.create_seam([int(interior[0]), int(interior[-1])])
    result = segmentation.segment_by_seams(_all_faces())
    assert result["n_patches"] == 1
    assert len(result["warnings"]) == 1


def test_relax_half_cylinder_is_low_strain_rectangle(cylinder_session):
    segmentation.segment_by_seams(_all_faces())  # sin costuras -> 1 parche
    result = flattening.flatten_patch(0, method="relax")
    # Superficie desarrollable: SLIM la aplana casi sin tensión a pi*r x L
    w, h = sorted([result["width_mm"], result["height_mm"]])
    assert w == pytest.approx(EXPECTED_WIDTH, rel=0.02)
    assert h == pytest.approx(LENGTH, rel=0.02)
    # Desarrollable -> tensión y compresión casi nulas
    assert result["tension_pct"] < 2.0
    assert result["compression_pct"] < 2.0


def test_projection_half_cylinder_is_its_shadow(cylinder_session):
    segmentation.segment_by_seams(_all_faces())
    result = flattening.flatten_patch(0, method="project")
    # La proyección es la SOMBRA: el ancho = diámetro (2r=100), no el
    # desarrollado (pi*r=157). El largo se conserva (eje recto del cilindro).
    w, h = sorted([result["width_mm"], result["height_mm"]])
    assert w == pytest.approx(2 * RADIUS, rel=0.02)
    assert h == pytest.approx(LENGTH, rel=0.02)
    # La proyección pierde área respecto al material real (superficie curva)
    assert result["mean_distortion_pct"] > 5.0


def test_unfold_half_cylinder_is_isometric(cylinder_session):
    # El desplegado rígido conserva el tamaño real -> como el desarrollado:
    # un rectángulo pi*r x L, distorsión 0 y SIN solape (es desarrollable).
    segmentation.segment_by_seams(_all_faces())
    result = flattening.flatten_patch(0, method="unfold")
    w, h = sorted([result["width_mm"], result["height_mm"]])
    assert h == pytest.approx(LENGTH, rel=0.03)
    assert w == pytest.approx(EXPECTED_WIDTH, rel=0.05)
    assert result["max_distortion_pct"] < 1e-6  # isométrico exacto
    assert result["overlap"] is False


def test_unfold_cube_is_isometric_net():
    # Un cubo: el desplegado debe conservar el área total (6 caras) con
    # distorsión cero. Es el oráculo del "patrón = material real".
    import trimesh

    side = 20.0
    box = trimesh.creation.box(extents=(side, side, side))
    mesh_io.load_mesh(box.export(file_type="stl"), "cubo.stl")
    segmentation.segment_by_seams(list(range(len(SESSION.mesh.faces))))
    result = flattening.flatten_patch(0, method="unfold")

    faces = np.asarray(result["faces"])
    uv = np.asarray(result["uv"])
    a = uv[faces[:, 1]] - uv[faces[:, 0]]
    b = uv[faces[:, 2]] - uv[faces[:, 0]]
    area_2d = float(np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]).sum() / 2)
    assert area_2d == pytest.approx(6 * side * side, rel=0.01)  # 6 caras
    assert result["max_distortion_pct"] < 1e-6                  # isométrico
    SESSION.reset()


def _open_tube(radius=40.0, length=200.0, n_theta=60, n_len=30):
    import trimesh

    th = np.linspace(0, 2 * np.pi, n_theta, endpoint=False)
    y = np.linspace(0, length, n_len)
    tt, yy = np.meshgrid(th, y, indexing="ij")
    verts = np.column_stack(
        [radius * np.cos(tt).ravel(), yy.ravel(), radius * np.sin(tt).ravel()]
    )
    faces = []
    for i in range(n_theta):
        for j in range(n_len - 1):
            a = i * n_len + j
            b = ((i + 1) % n_theta) * n_len + j  # cierra el tubo en theta
            faces += [[a, b, a + 1], [b, b + 1, a + 1]]
    return trimesh.Trimesh(vertices=verts, faces=np.array(faces), process=True)


def test_unfold_seam_opens_closed_tube():
    # Un tubo cerrado solo se aplana cortándolo a lo largo. La costura debe
    # abrirlo en una lámina exacta de 2*pi*r x L (aunque no separe en 2).
    R, L = 40.0, 200.0
    mesh_io.load_mesh(_open_tube(R, L).export(file_type="stl"), "tubo.stl")
    m = SESSION.mesh
    V = np.asarray(m.vertices)
    gen = np.flatnonzero((np.abs(V[:, 2]) < 2.0) & (V[:, 0] > 0))
    geodesics.create_seam([int(gen[np.argmin(V[gen, 1])]), int(gen[np.argmax(V[gen, 1])])])

    segmentation.segment_by_seams(list(range(len(m.faces))))
    result = flattening.flatten_patch(0, method="unfold")
    short, long = sorted([result["width_mm"], result["height_mm"]])
    assert short == pytest.approx(L, rel=0.03)              # largo del tubo
    assert long == pytest.approx(2 * np.pi * R, rel=0.05)   # circunferencia abierta
    assert result["overlap"] is False
    SESSION.reset()


def test_relax_flattens_saddle_without_collapse():
    # Doble curvatura: SLIM debe aplanar SIN colapsar (donde LSCM/ARAP fallan),
    # repartiendo tensión/compresión. Es el motor tipo ExactFlat.
    from samples.make_samples import grid_mesh

    saddle = grid_mesh(
        np.linspace(-100, 100, 50),
        np.linspace(-100, 100, 50),
        lambda x, y: (x**2 - y**2) / 250.0,
    )
    mesh_io.load_mesh(saddle.export(file_type="stl"), "silla.stl")
    segmentation.segment_by_seams(list(range(len(SESSION.mesh.faces))))
    result = flattening.flatten_patch(0, method="relax")
    # Hay deformación (no es desarrollable) pero acotada y sin reventar
    assert 0.0 < result["tension_pct"] < 80.0
    assert 0.0 < result["compression_pct"] < 80.0
    assert len(result["strain"]) == len(result["faces"])
    SESSION.reset()


def test_unfold_detects_overlap_on_saddle():
    # Doble curvatura: el desplegado es isométrico pero el net se solapa.
    from samples.make_samples import grid_mesh

    saddle = grid_mesh(
        np.linspace(-100, 100, 60),
        np.linspace(-100, 100, 60),
        lambda x, y: (x**2 - y**2) / 250.0,
    )
    mesh_io.load_mesh(saddle.export(file_type="stl"), "silla.stl")
    segmentation.segment_by_seams(list(range(len(SESSION.mesh.faces))))
    result = flattening.flatten_patch(0, method="unfold")
    assert result["max_distortion_pct"] < 1e-6  # isométrico
    assert result["overlap"] is True            # pero se solapa -> avisa
    SESSION.reset()


def test_flood_isolates_cara_a_on_solid_part():
    # Pieza sólida con espesor: el flood desde la cara A (arriba) NO debe
    # incluir paredes ni reverso. La cara A es ~la mitad del área total.
    stl_bytes = solid_panel().export(file_type="stl")
    mesh_io.load_mesh(stl_bytes, "panel_plastico.stl")
    mesh = SESSION.mesh

    # Semilla: cara cuyo centroide está más arriba (cúspide de la bóveda)
    centroids = mesh.triangles.mean(axis=1)
    seed = int(np.argmax(centroids[:, 2]))
    result = selection.flood_select(seed, angle_deg=35.0)

    sel_area = result["area_cm2"]
    total_area = mesh.area / 100.0
    # La cara A es claramente menos que el total (excluye reverso + 4 paredes)
    assert 0.25 < sel_area / total_area < 0.75
    # Y todas las caras seleccionadas miran hacia arriba (normal Z > 0)
    sel = np.asarray(result["faces"])
    assert np.all(mesh.face_normals[sel][:, 2] > 0.3)
    SESSION.reset()


def _spherical_cap(radius=60.0, half_angle_deg=80.0, n_phi=24, n_theta=48):
    """Casquete esférico = DISCO con doble curvatura fuerte (1 borde).

    Es el oráculo del fix de relax: la init armónica-cotangente VOLTEA en el
    teselado obtuso del borde (falso "envuelve sobre sí misma"), pero el casquete
    SÍ es aplanable con estiramiento. Polo en phi=0 (un punto), borde en phi=alpha.
    """
    import trimesh

    alpha = np.radians(half_angle_deg)
    phis = np.linspace(0, alpha, n_phi)
    thetas = np.linspace(0, 2 * np.pi, n_theta, endpoint=False)
    verts = [[0.0, 0.0, radius]]  # polo
    for phi in phis[1:]:
        for th in thetas:
            verts.append([
                radius * np.sin(phi) * np.cos(th),
                radius * np.sin(phi) * np.sin(th),
                radius * np.cos(phi),
            ])
    verts = np.array(verts)
    faces = []
    # anillo del polo
    for i in range(n_theta):
        faces.append([0, 1 + i, 1 + (i + 1) % n_theta])
    # anillos restantes
    for ring in range(n_phi - 2):
        base = 1 + ring * n_theta
        nxt = base + n_theta
        for i in range(n_theta):
            a, b = base + i, base + (i + 1) % n_theta
            c, d = nxt + i, nxt + (i + 1) % n_theta
            faces += [[a, b, c], [b, d, c]]
    return trimesh.Trimesh(vertices=verts, faces=np.array(faces), process=True)


def test_tutte_init_is_flipfree_on_curved_disk():
    # La init de SLIM debe ser flip-free (Tutte uniforme) para que SLIM no se
    # atasque. En un casquete (disco de doble curvatura) Tutte no voltea.
    cap = _spherical_cap()
    mesh_io.load_mesh(cap.export(file_type="stl"), "casquete.stl")
    F = np.asarray(SESSION.mesh.faces)
    V = np.asarray(SESSION.mesh.vertices, dtype=np.float64)
    bnd = igl.boundary_loop(F)
    bnd_uv = igl.map_vertices_to_circle(np.asfortranarray(V), bnd)
    tutte = flattening._tutte_uniform_uv(V, F, bnd, bnd_uv)
    assert flattening._count_flips(tutte, F) == 0
    SESSION.reset()


def test_relax_flattens_curved_cap_with_stretch():
    # Un casquete (disco de doble curvatura) cae en UN net continuo con
    # estiramiento repartido, sin colapsar ni solaparse (motor tipo ExactFlat).
    cap = _spherical_cap()
    mesh_io.load_mesh(cap.export(file_type="stl"), "casquete.stl")
    segmentation.segment_by_seams(list(range(len(SESSION.mesh.faces))))
    result = flattening.flatten_patch(0, method="relax")
    assert result["width_mm"] > 0 and result["height_mm"] > 0
    assert np.all(np.isfinite(np.asarray(result["uv"])))
    assert 0.0 < result["tension_pct"] < 120.0     # hay estiramiento, acotado
    assert result["overlap"] is False              # un solo net, sin cruzarse
    SESSION.reset()


@pytest.mark.slow
def test_relax_flattens_wrapping_disk_from_real_step():
    # Regresión del bug real: la Cara A del 'p42s toproll' es un DISCO que
    # envuelve, con teselado obtuso de STEP. La init cotangente volteaba (77
    # flips) -> relax rechazaba con un FALSO "envuelve sobre sí misma". Con la
    # init Tutte + iteración a convergencia, ese disco SÍ se aplana.
    sample = Path(__file__).resolve().parents[2] / "samples" / "p42s toproll.stl"
    if not sample.exists():
        pytest.skip("sample p42s toproll.stl no disponible")
    mesh_io.load_mesh(sample.read_bytes(), "p42s toproll.stl")
    mesh = SESSION.mesh
    seed = int(np.argmax(mesh.area_faces))
    sel = selection.flood_select(seed, angle_deg=35.0, side_deg=85.0)
    segmentation.segment_by_seams(sel["faces"])
    # El parche 0 es la Cara A grande que envuelve: antes daba FlattenError.
    result = flattening.flatten_patch(0, method="relax")
    assert result["width_mm"] > 0 and result["height_mm"] > 0
    assert np.all(np.isfinite(np.asarray(result["uv"])))
    assert result["overlap"] is False
    SESSION.reset()


def test_closed_patch_fails_with_clear_error():
    import trimesh

    stl_bytes = trimesh.creation.icosphere(subdivisions=3, radius=40.0).export(
        file_type="stl"
    )
    mesh_io.load_mesh(stl_bytes, "esfera.stl")
    segmentation.segment_by_seams(list(range(len(SESSION.mesh.faces))))
    # El aplanado suave necesita un borde; una esfera cerrada no lo tiene.
    with pytest.raises(flattening.FlattenError, match="cerrada"):
        flattening.flatten_patch(0, method="relax")
    SESSION.reset()


def test_boundary_loops_terminate_on_nonmanifold():
    # Dos triángulos que comparten SOLO el vértice 2: el vértice queda con 4
    # aristas de borde (no-manifold). El extractor antes entraba en bucle
    # infinito; ahora debe terminar consumiendo cada arista una vez.
    faces = np.array([[0, 1, 2], [2, 3, 4]])
    loops = export_dxf._boundary_loops(faces)
    total = sum(len(loop) for loop in loops)
    assert loops  # devolvió algo y, sobre todo, NO se colgó
    assert total <= 8  # no revisita aristas infinitamente


def test_straighten_seam_run_collapses_interior():
    # El tramo de costura (vértices 2,3,4) se colapsa a recta: se quita el
    # interior (3), se conservan extremos (2,4) y el contorno libre (0,1,5).
    out = export_dxf._straighten_seam_run([0, 1, 2, 3, 4, 5], {2, 3, 4})
    assert out == [0, 1, 2, 4, 5]
    # Sin costura, el borde no cambia.
    assert export_dxf._straighten_seam_run([0, 1, 2, 3], set()) == [0, 1, 2, 3]


def test_export_straightens_only_union_seams(cylinder_session):
    # Camino B por costura: una costura de UNIÓN (straighten=True) endereza su
    # borde -> el contorno pierde puntos. Una costura REAL (default) NO se toca.
    a, b = _edge_boundary_vertices(SESSION.mesh)
    sid = geodesics.create_seam([a, b])["seam_id"]
    # Por defecto es REAL: no endereza nada.
    assert export_dxf._seam_local_vertices(
        {"orig_vertex_ids": np.arange(len(SESSION.mesh.vertices))}
    ) == set()
    SESSION.seams[sid].straighten = True   # marcarla como UNIÓN
    segmentation.segment_by_seams(_all_faces())
    flattening.flatten_all_patches(method="relax")
    p = SESSION.patches[0]
    faces = np.asarray(p.get("faces_2d", p["mesh"].faces))
    loops = export_dxf._boundary_loops(faces)
    seam_local = export_dxf._seam_local_vertices(p)
    assert seam_local                                   # el parche toca la costura
    before = sum(len(loop) for loop in loops)
    after = sum(len(export_dxf._straighten_seam_run(loop, seam_local)) for loop in loops)
    assert after < before                               # se enderezó (menos puntos)


def _flat_irregular_mesh(side=200.0, n_inner=1500, n_edge=41, seed=0):
    """Lámina plana z=0 con triangulación IRREGULAR (Delaunay). Imita el teselado
    de una pieza STEP real: a diferencia de una grilla, el camino más corto por
    aristas (Dijkstra) sí sigue de cerca a la geodésica, zigzagueando a escala de
    triángulo a su alrededor — justo el caso que el suavizado debe arreglar."""
    import trimesh
    from scipy.spatial import Delaunay

    rng = np.random.default_rng(seed)
    e = np.linspace(0, side, n_edge)
    z, full = np.zeros_like(e), np.full_like(e, side)
    border = np.vstack([
        np.column_stack([e, z]), np.column_stack([e, full]),
        np.column_stack([z, e]), np.column_stack([full, e]),
    ])
    pts = np.unique(np.vstack([border, rng.uniform(5, side - 5, size=(n_inner, 2))]), axis=0)
    faces = Delaunay(pts).simplices
    verts = np.column_stack([pts, np.zeros(len(pts))])
    return trimesh.Trimesh(vertices=verts, faces=faces, process=True)


def _setup_real_seam_on_flat():
    """Carga la lámina plana, traza una costura REAL diagonal de borde a borde,
    segmenta (2 parches) y aplana. Devuelve (patch, uv, seam_id)."""
    mesh_io.load_mesh(_flat_irregular_mesh().export(file_type="stl"), "flat.stl")
    v = np.asarray(SESSION.mesh.vertices)
    a = int(np.argmin(np.linalg.norm(v[:, :2] - np.array([0.0, 60.0]), axis=1)))
    b = int(np.argmin(np.linalg.norm(v[:, :2] - np.array([200.0, 140.0]), axis=1)))
    sid = geodesics.create_seam([a, b])["seam_id"]
    segmentation.segment_by_seams(list(range(len(SESSION.mesh.faces))))
    flattening.flatten_all_patches(method="project")
    patch = SESSION.patches[0]
    return patch, np.asarray(patch["uv"]), sid


def _perp_offsets(pts: np.ndarray) -> np.ndarray:
    """Desplazamiento perpendicular de cada punto respecto a la recta extremo-
    extremo (mide cuánto se aparta el trazo de la cuerda)."""
    pts = np.asarray(pts, float)
    d = pts[-1] - pts[0]
    L = float(np.linalg.norm(d))
    if L < 1e-9:
        return np.zeros(len(pts))
    d = d / L
    rel = pts - pts[0]
    return rel[:, 0] * (-d[1]) + rel[:, 1] * d[0]


def test_real_seam_cut_follows_geodesic_not_jagged():
    # Camino A: una costura REAL sobre la lámina plana irregular debe cortar
    # siguiendo la GEODÉSICA (recta en el plano), no el zigzag de las aristas.
    patch, uv, sid = _setup_real_seam_on_flat()
    assert SESSION.seams[sid].straighten is False              # REAL por defecto
    assert len(uv) == len(np.asarray(patch["mesh"].vertices))  # 1:1 por vértice

    real_seams = export_dxf._real_seams_2d(patch, uv)
    assert len(real_seams) == 1
    poly2d = np.asarray(real_seams[0]["poly2d"])
    assert len(poly2d) > 2                                     # NO colapsada a recta

    # edge_path (jagged) mapeado al 2D, en orden, para comparar
    seam = SESSION.seams[sid]
    chain = list(dict.fromkeys([v for ab in seam.edge_path for v in ab]))
    g2l = {int(g): i for i, g in enumerate(np.asarray(patch["orig_vertex_ids"]))}
    jagged2d = uv[[g2l[v] for v in chain if v in g2l]]

    smooth_dev = float(np.abs(_perp_offsets(poly2d)).max())
    jagged_dev = float(np.abs(_perp_offsets(jagged2d)).max())
    # Suavidad: variación total perpendicular (cuánto sube-y-baja el trazo)
    smooth_tv = float(np.abs(np.diff(_perp_offsets(poly2d))).sum())
    jagged_tv = float(np.abs(np.diff(_perp_offsets(jagged2d))).sum())

    assert smooth_dev < 5.0                  # sigue la geodésica recta (~0)
    assert smooth_dev < jagged_dev * 0.5     # se aparta MUCHO menos que el zigzag
    assert smooth_tv < jagged_tv * 0.25      # el zigzag de alta frecuencia desapareció
    SESSION.reset()


def test_real_seam_contour_not_straightened_like_union():
    # La costura REAL conserva su geodésica en el contorno (Camino A); la de
    # UNIÓN colapsa ese tramo a una recta (Camino B). El contorno REAL tiene
    # por tanto MÁS puntos en el tramo de costura que la versión enderezada.
    patch, uv, _ = _setup_real_seam_on_flat()
    faces = np.asarray(patch.get("faces_2d", patch["mesh"].faces))

    real_seams = export_dxf._real_seams_2d(patch, uv)
    _, rings_real = export_dxf._patch_polygon(uv, faces, set(), real_seams)
    union_local = {v for rs in real_seams for v in rs["local"]}
    _, rings_union = export_dxf._patch_polygon(uv, faces, union_local, [])

    assert sum(len(r) for r in rings_real) > sum(len(r) for r in rings_union)
    SESSION.reset()


def test_export_dxf_layers_and_scale(cylinder_session, tmp_path, monkeypatch):
    monkeypatch.setattr(export_dxf, "OUTPUT_DIR", tmp_path)
    a, b = _edge_boundary_vertices(SESSION.mesh)
    geodesics.create_seam([a, b])
    segmentation.segment_by_seams(_all_faces())
    flattening.flatten_all_patches()

    path = export_dxf.export_patterns(seam_allowance_mm=10.0, notch_spacing_mm=50.0)
    doc = ezdxf.readfile(path)

    assert doc.header["$INSUNITS"] == 4  # mm
    layers = {layer.dxf.name for layer in doc.layers}
    assert {"CORTE", "COSTURA", "MUESCAS", "TEXTO"} <= layers

    msp = doc.modelspace()
    cut_lines = [e for e in msp if e.dxf.layer == "CORTE"]
    seam_lines = [e for e in msp if e.dxf.layer == "COSTURA"]
    notches = [e for e in msp if e.dxf.layer == "MUESCAS" and e.dxftype() == "LINE"]
    assert len(cut_lines) >= 2  # un contorno por parche
    assert len(seam_lines) >= 2
    assert len(notches) > 0

    # Escala 1:1: el bbox del corte debe superar al patrón neto por el margen
    xs = [p[0] for e in cut_lines for p in e.get_points("xy")]
    assert max(xs) - min(xs) > EXPECTED_WIDTH  # patrón + margen


def _two_patch_session():
    """Medio cilindro con una costura de borde a borde -> 2 parches aplanados."""
    a, b = _edge_boundary_vertices(SESSION.mesh)
    geodesics.create_seam([a, b])
    segmentation.segment_by_seams(_all_faces())
    flattening.flatten_all_patches()


def test_preview_matches_dxf_geometry(cylinder_session, tmp_path, monkeypatch):
    # La vista previa 2D y el DXF salen de la MISMA geometría: lo que se ve en
    # pantalla es lo que se exporta (mismo conteo de contornos, muescas y bbox).
    monkeypatch.setattr(export_dxf, "OUTPUT_DIR", tmp_path)
    _two_patch_session()

    prev = export_dxf.preview_patterns(10.0, True, 50.0)
    assert len(prev["patches"]) == 2 and prev["skipped"] == []

    msp = ezdxf.readfile(export_dxf.export_patterns(10.0, True, 50.0)).modelspace()
    cut = [e for e in msp if e.dxf.layer == "CORTE"]
    seam = [e for e in msp if e.dxf.layer == "COSTURA"]
    notch = [e for e in msp if e.dxf.layer == "MUESCAS" and e.dxftype() == "LINE"]
    assert len(cut) == sum(len(p["cut_rings"]) for p in prev["patches"])
    assert len(seam) == sum(len(p["seam_rings"]) for p in prev["patches"])
    assert len(notch) == sum(len(p["notches"]) for p in prev["patches"]) > 0

    xs_prev = [pt[0] for p in prev["patches"] for r in p["cut_rings"] for pt in r]
    xs_dxf = [pt[0] for e in cut for pt in e.get_points("xy")]
    assert max(xs_prev) == pytest.approx(max(xs_dxf), abs=0.02)
    assert min(xs_prev) == pytest.approx(min(xs_dxf), abs=0.02)


def test_preview_respects_options(cylinder_session):
    # Sin muescas no hay muescas; un margen mayor ensancha el contorno de corte.
    _two_patch_session()
    p0 = export_dxf.preview_patterns(5.0, False, 50.0)
    p1 = export_dxf.preview_patterns(20.0, False, 50.0)
    assert all(not p["notches"] for p in p0["patches"])
    width = lambda prev: max(  # noqa: E731
        pt[0] for p in prev["patches"] for r in p["cut_rings"] for pt in r
    ) - min(pt[0] for p in prev["patches"] for r in p["cut_rings"] for pt in r)
    assert width(p1) > width(p0) + 20.0


def _break_patch_zero(monkeypatch):
    real = export_dxf._patch_geometry

    def flaky(pid, *args, **kwargs):
        if pid == 0:
            raise ValueError("boom")
        return real(pid, *args, **kwargs)

    monkeypatch.setattr(export_dxf, "_patch_geometry", flaky)


def test_export_reports_skipped_patches(cylinder_session, tmp_path, monkeypatch):
    # Un parche que falla NO tumba el export, pero ya no desaparece en silencio:
    # se reporta con su id y el motivo.
    monkeypatch.setattr(export_dxf, "OUTPUT_DIR", tmp_path)
    _two_patch_session()
    _break_patch_zero(monkeypatch)

    report: dict = {}
    path = export_dxf.export_patterns(10.0, True, 50.0, report=report)
    assert [s["patch_id"] for s in report["skipped"]] == [0]
    assert "boom" in report["skipped"][0]["error"]
    msp = ezdxf.readfile(path).modelspace()
    assert len([e for e in msp if e.dxf.layer == "CORTE"]) >= 1  # el otro sí salió

    prev = export_dxf.preview_patterns(10.0, True, 50.0)
    assert len(prev["patches"]) == 1
    assert prev["skipped"][0]["patch_id"] == 0


def test_export_api_preview_and_skipped_header(cylinder_session, tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(export_dxf, "OUTPUT_DIR", tmp_path)
    _two_patch_session()
    client = TestClient(app)
    body = {"seam_allowance_mm": 10.0, "add_notches": True, "notch_spacing_mm": 50.0}

    r = client.post("/api/export/preview", json=body)
    assert r.status_code == 200 and len(r.json()["patches"]) == 2
    ok = client.post("/api/export/dxf", json=body)
    assert ok.status_code == 200 and "x-flatwrap-skipped" not in ok.headers

    _break_patch_zero(monkeypatch)
    bad = client.post("/api/export/dxf", json=body)
    assert bad.status_code == 200
    assert bad.headers["x-flatwrap-skipped"] == "0"


def _short_seam_vertices(mesh, gap_mm):
    """Vértices sobre theta=pi/2 que quedan ~gap_mm antes de cada borde."""
    v = np.asarray(mesh.vertices)
    col = np.flatnonzero(np.abs(v[:, 0]) < 1.0)
    ys = v[col, 1]
    a = col[np.argmin(np.abs(ys - gap_mm))]
    b = col[np.argmin(np.abs(ys - (LENGTH - gap_mm)))]
    return int(a), int(b)


def test_seam_endpoint_snaps_to_boundary(cylinder_session):
    # Una costura que se queda unos mm corta NO separaba la cara A; con snap se
    # prolonga hasta el borde y sí divide. snap_mm=0 conserva el comportamiento viejo.
    a, b = _short_seam_vertices(SESSION.mesh, gap_mm=3.0)
    geodesics.create_seam([a, b])
    off = segmentation.segment_by_seams(_all_faces(), snap_mm=0)
    assert off["n_patches"] == 1 and off["snaps"] == []

    on = segmentation.segment_by_seams(_all_faces(), snap_mm=12.0)
    assert on["n_patches"] == 2
    assert len(on["snaps"]) >= 1 and all(s["dist_mm"] <= 12.0 for s in on["snaps"])
    assert any("prolongado" in w for w in on["warnings"])
    # La costura registrada no se muta (la extensión es solo del segmentado)
    assert len(SESSION.seams) == 1


def test_seam_snap_respects_tolerance(cylinder_session):
    a, b = _short_seam_vertices(SESSION.mesh, gap_mm=30.0)
    geodesics.create_seam([a, b])
    res = segmentation.segment_by_seams(_all_faces(), snap_mm=5.0)
    assert res["n_patches"] == 1 and res["snaps"] == []


def test_slim_adaptive_stop_and_cache(cylinder_session, monkeypatch):
    # SLIM para pronto cuando el estiramiento ya no mejora y, al re-aplanar la
    # misma malla (p. ej. tras borrar OTRO parche), no recalcula: sale del cache.
    import igl

    flattening._SLIM_CACHE.clear()
    calls = {"n": 0}
    real = igl.slim_solve

    def counting(data, n):
        calls["n"] += n
        return real(data, n)

    monkeypatch.setattr(igl, "slim_solve", counting)
    _two_patch_session()
    flattening.flatten_all_patches("relax")
    first = calls["n"]
    assert 0 < first < 2 * flattening.SLIM_MAX_ITERS  # no agota el tope

    res = flattening.flatten_all_patches("relax")
    assert calls["n"] == first                         # 2º aplanado: 0 iteraciones nuevas
    assert len(res["patches"]) == 2


def test_project_roundtrip(cylinder_session):
    # Guardar -> reiniciar -> abrir: misma malla, mismas costuras (mismos vértices)
    # y misma Cara A; los parches se regeneran igual (mismo conteo).
    from app.core import project

    a, b = _edge_boundary_vertices(SESSION.mesh)
    geodesics.create_seam([a, b])
    SESSION.seams[1].straighten = True
    sel = _all_faces()[:50]
    blob = project.save_project(sel, {"allowance": 12}, "pieza.stl")
    assert len(blob) < 500_000

    v0 = np.asarray(SESSION.mesh.vertices).copy()
    f0 = np.asarray(SESSION.mesh.faces).copy()
    SESSION.reset()
    out = project.load_project(blob)

    assert np.array_equal(np.asarray(SESSION.mesh.vertices), v0)
    assert np.array_equal(np.asarray(SESSION.mesh.faces), f0)
    assert out["selected_faces"] == sel and out["settings"] == {"allowance": 12}
    assert len(out["seams"]) == 1 and out["seams"][0]["straighten"] is True
    assert SESSION.seams[out["seams"][0]["seam_id"]].vertex_ids == [a, b]
    assert segmentation.segment_by_seams(_all_faces())["n_patches"] == 2


def test_project_rejects_garbage():
    from app.core import project

    with pytest.raises(project.ProjectError):
        project.load_project(b"no soy un proyecto")


def test_geometry_binary_roundtrip(cylinder_session):
    v = np.asarray(SESSION.mesh.vertices, dtype=np.float32)
    f = np.asarray(SESSION.mesh.faces, dtype=np.uint32)
    blob = mesh_io.geometry_binary(SESSION.mesh)
    nv, nf = np.frombuffer(blob, dtype="<u4", count=2)
    assert (nv, nf) == (len(v), len(f))
    assert len(blob) == 8 + v.nbytes + f.nbytes
    v2 = np.frombuffer(blob, dtype="<f4", count=nv * 3, offset=8).reshape(-1, 3)
    f2 = np.frombuffer(blob, dtype="<u4", count=nf * 3, offset=8 + nv * 12).reshape(-1, 3)
    assert np.array_equal(v2, v) and np.array_equal(f2, f)


def test_api_gzip_and_binary_geometry(cylinder_session):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    r = c.get("/api/mesh/geometry.bin")
    assert r.status_code == 200 and r.content == mesh_io.geometry_binary(SESSION.mesh)
    big = c.get("/api/mesh/geometry", headers={"Accept-Encoding": "gzip"})
    assert big.headers.get("content-encoding") == "gzip"


def test_relax_rejects_collapsed_flatten(cylinder_session, monkeypatch):
    # Un relajado colapsado (tensión p95 enorme) NO se entrega como patrón válido:
    # error accionable con diagnóstico de cuánto envuelve la Cara A.
    segmentation.segment_by_seams(_all_faces())
    real = flattening.principal_stretches

    def junk(V, f_src, uv, f_tgt):
        s1, s2 = real(V, f_src, uv, f_tgt)
        return s1 * 50.0, s2

    monkeypatch.setattr(flattening, "principal_stretches", junk)
    with pytest.raises(flattening.FlattenError, match="colapsado"):
        flattening.flatten_patch(0, "relax")


def test_pinch_vertices_and_islands_are_cleaned():
    # Dos triángulos unidos solo por un vértice (8): el pellizco se divide y la isla
    # chica se descarta; el resultado es una sola componente sin vértices pellizco.
    V = np.array([[0, 0, 0], [10, 0, 0], [0, 10, 0], [10, 10, 0], [-10, 0, 0], [0, -1, 0]], float)
    F = np.array([[0, 1, 2], [1, 3, 2], [0, 4, 5]])   # (0,4,5) toca el resto solo en el vértice 0
    V2, F2, to = flattening._split_pinch_vertices(V, F)
    assert len(V2) == len(V) + 1
    V3, F3, to3 = flattening._largest_face_component(V2, F2)
    assert len(F3) == 2 and len(set(F3.ravel())) == 4
