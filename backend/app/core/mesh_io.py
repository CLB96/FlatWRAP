"""Fase 1 - Carga, limpieza y serialización de la malla. FUNCIONAL.

Notas para STL exportado desde Solid Edge:
- Exportar con tolerancia fina (0.05-0.1 mm de desviación de cuerda)
  para que las geodésicas y el aplanado sean precisos.
- Las unidades del STL no van en el archivo: asumimos mm (estándar automotriz).
"""

import io
import os
import tempfile
import uuid

import numpy as np
import trimesh

from app.core.session import SESSION
from app.models.schemas import MeshSummary

# NO decimamos: conservamos la resolución original de la malla. Decimar
# pierde detalle y degrada el unfold (el usuario prioriza fidelidad y tiene
# RAM de sobra). Solo se limpia la conectividad (soldar vértices, etc.).

MESH_FORMATS = {"stl", "obj", "ply", "glb", "gltf", "3mf", "off"}
CAD_FORMATS = {"step", "stp"}  # B-rep NURBS -> teselado fino con cascadio

# Teselado de STEP (cascadio/OpenCASCADE): adaptativo, fino en curvas y
# grueso en planos. tol_linear = desviación de cuerda en mm (como en CAD).
STEP_TOL_LINEAR_MM = 0.08
STEP_TOL_ANGULAR = 0.35  # rad (~20°)


class MeshLoadError(Exception):
    pass


def _load_step(data: bytes) -> trimesh.Trimesh:
    """Lee un STEP (superficies NURBS) y lo tesela finamente vía cascadio.

    Da una malla suave y eficiente (acabado tipo JT), partiendo de la
    geometría buena del CAD en vez de un STL ya facetado.
    """
    try:
        import cascadio
    except ImportError as e:  # pragma: no cover
        raise MeshLoadError(
            "Soporte STEP no instalado (falta 'cascadio')."
        ) from e
    with tempfile.TemporaryDirectory() as d:
        inp, out = os.path.join(d, "in.step"), os.path.join(d, "out.glb")
        with open(inp, "wb") as f:
            f.write(data)
        cascadio.step_to_glb(
            inp, out,
            tol_linear=STEP_TOL_LINEAR_MM,
            tol_angular=STEP_TOL_ANGULAR,
        )
        mesh = trimesh.load(out, force="mesh")
    # glTF está siempre en METROS; el resto de la app trabaja en mm.
    mesh.apply_scale(1000.0)
    return mesh


def load_mesh(data: bytes, filename: str) -> MeshSummary:
    """Carga malla (STL/OBJ/PLY/GLB/3MF) o CAD (STEP), normaliza y guarda."""
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext not in MESH_FORMATS | CAD_FORMATS:
        raise MeshLoadError(
            f"Formato no soportado: .{ext}. Usa STL, OBJ, PLY, GLB, 3MF o STEP. "
            "(JT es propietario de Siemens: exporta STEP desde tu CAD.)"
        )

    try:
        if ext in CAD_FORMATS:
            mesh = _load_step(data)
        else:
            mesh = trimesh.load(io.BytesIO(data), file_type=ext, force="mesh")
    except MeshLoadError:
        raise
    except Exception as e:
        raise MeshLoadError(f"No se pudo leer la malla: {e}") from e

    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise MeshLoadError("El archivo no contiene una malla triangular válida")

    # Limpieza estándar: STL duplica vértices por triángulo; hay que
    # soldarlos para tener conectividad real (sin esto no hay geodésicas
    # ni segmentación posibles).
    mesh.merge_vertices()
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    # NOTA: NO quitamos las caras no-manifold de la malla completa (eso metía
    # huecos que distorsionaban el aplanado). La limpieza a manifold se hace en
    # una COPIA aparte, solo para el solver de geodésicas (ver geodesics.py).
    # Winding consistente: un STL con caras mal orientadas hace que el
    # aplanado SLIM cuente falsos volteos (área con signo) y rechace un parche
    # que SÍ es aplanable. fix_normals deja una orientación coherente.
    mesh.fix_normals()
    # Sin decimación: se conserva la resolución original completa.

    SESSION.reset()
    SESSION.mesh = mesh
    SESSION.mesh_version += 1  # invalida caches por malla (selección/geodésicas)

    return _summary(mesh)


def _summary(mesh: trimesh.Trimesh) -> MeshSummary:
    return MeshSummary(
        session_id=uuid.uuid4().hex[:8],
        n_vertices=len(mesh.vertices),
        n_faces=len(mesh.faces),
        bbox_min=mesh.bounds[0].tolist(),
        bbox_max=mesh.bounds[1].tolist(),
        watertight=bool(mesh.is_watertight),
    )


# Tope para refinar: 4x por nivel; por encima de esto el visor se vuelve lento.
MAX_FACES_SUBDIVIDE = 600_000


def subdivide_current() -> MeshSummary:
    """Refina la malla actual (subdivisión midpoint, 4x caras) para suavizar
    el aplanado. Invalida selección, costuras y parches (cambia la topología).
    """
    mesh = SESSION.mesh
    if mesh is None:
        raise MeshLoadError("No hay malla cargada")
    if len(mesh.faces) * 4 > MAX_FACES_SUBDIVIDE:
        raise MeshLoadError(
            f"La malla ya es muy densa para refinar ({len(mesh.faces):,} caras)."
        )
    v2, f2 = trimesh.remesh.subdivide(
        np.asarray(mesh.vertices), np.asarray(mesh.faces)
    )
    new_mesh = trimesh.Trimesh(vertices=v2, faces=f2, process=False)
    new_mesh.merge_vertices()

    SESSION.reset()
    SESSION.mesh = new_mesh
    SESSION.mesh_version += 1
    return _summary(new_mesh)


def geometry_binary(mesh: trimesh.Trimesh) -> bytes:
    """[nV u32][nF u32][vértices float32 (nV*3)][caras uint32 (nF*3)], little-endian."""
    v = np.ascontiguousarray(mesh.vertices, dtype="<f4")
    f = np.ascontiguousarray(mesh.faces, dtype="<u4")
    header = np.array([len(v), len(f)], dtype="<u4").tobytes()
    return header + v.tobytes() + f.tobytes()


def geometry_payload(mesh: trimesh.Trimesh) -> dict:
    """Geometría plana (Float32/Uint32) lista para BufferGeometry de Three.js."""
    return {
        "vertices": np.asarray(mesh.vertices, dtype=np.float32).ravel().tolist(),
        "faces": np.asarray(mesh.faces, dtype=np.uint32).ravel().tolist(),
    }
