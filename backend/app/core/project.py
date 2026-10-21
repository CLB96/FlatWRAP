"""Guardar / abrir proyecto FlatWrap (.flatwrap).

Un proyecto = malla limpia + costuras + Cara A + ajustes de la interfaz. Se
guarda la malla YA LIMPIA (vértices float64 exactos + caras) para que los
`vertex_ids` de las costuras sigan apuntando a los mismos vértices al abrir.
Los parches y aplanados NO se guardan: se regeneran en segundos (SLIM tiene
cache y parada adaptativa), así el archivo es pequeño y nunca queda obsoleto.

Formato: JSON comprimido con gzip. `version` permite migrar en el futuro.
"""

import base64
import gzip
import json
import zlib

import numpy as np
import trimesh

from app.core import geodesics, mesh_io
from app.core.session import SESSION

FORMAT = "flatwrap-project"
VERSION = 1


class ProjectError(Exception):
    """Archivo de proyecto inválido o sin malla que guardar."""


def _pack(arr: np.ndarray) -> str:
    return base64.b64encode(zlib.compress(np.ascontiguousarray(arr).tobytes(), 6)).decode()


def _unpack(s: str, dtype, shape) -> np.ndarray:
    raw = zlib.decompress(base64.b64decode(s))
    return np.frombuffer(raw, dtype=dtype).reshape(shape).copy()


def save_project(selected_faces: list[int], settings: dict, name: str = "") -> bytes:
    mesh = SESSION.mesh
    if mesh is None:
        raise ProjectError("No hay malla cargada que guardar.")
    n = len(mesh.faces)
    sel = sorted({int(f) for f in selected_faces if 0 <= int(f) < n})
    doc = {
        "format": FORMAT,
        "version": VERSION,
        "name": name,
        "mesh": {
            "n_vertices": len(mesh.vertices),
            "n_faces": n,
            "vertices": _pack(np.asarray(mesh.vertices, dtype=np.float64)),
            "faces": _pack(np.asarray(mesh.faces, dtype=np.uint32)),
        },
        "seams": [
            {"vertex_ids": [int(v) for v in s.vertex_ids], "closed": bool(s.closed),
             "straighten": bool(s.straighten)}
            for s in SESSION.seams.values()
        ],
        "selected_faces": sel,
        "settings": settings or {},
    }
    return gzip.compress(json.dumps(doc, separators=(",", ":")).encode("utf-8"), 6)


def load_project(data: bytes) -> dict:
    """Restaura malla y costuras en la sesión. Devuelve lo que el frontend necesita."""
    try:
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        doc = json.loads(data.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise ProjectError("El archivo no es un proyecto FlatWrap válido.") from e
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ProjectError("El archivo no es un proyecto FlatWrap válido.")
    if int(doc.get("version", 0)) > VERSION:
        raise ProjectError("El proyecto fue guardado con una versión más nueva de FlatWrap.")

    try:
        m = doc["mesh"]
        V = _unpack(m["vertices"], np.float64, (int(m["n_vertices"]), 3))
        F = _unpack(m["faces"], np.uint32, (int(m["n_faces"]), 3)).astype(np.int64)
    except Exception as e:  # noqa: BLE001
        raise ProjectError("La malla del proyecto está dañada.") from e
    if F.size and (F.max() >= len(V)):
        raise ProjectError("La malla del proyecto está dañada.")

    mesh = trimesh.Trimesh(vertices=V, faces=F, process=False)
    SESSION.reset()
    SESSION.mesh = mesh
    SESSION.mesh_version += 1

    seams, warnings = [], []
    for s in doc.get("seams", []):
        try:
            r = geodesics.create_seam([int(v) for v in s["vertex_ids"]], closed=bool(s.get("closed")))
        except geodesics.SeamError as e:
            warnings.append(f"Una costura no se pudo restaurar: {e}")
            continue
        SESSION.seams[r["seam_id"]].straighten = bool(s.get("straighten", False))
        r["straighten"] = bool(s.get("straighten", False))
        r["points"] = [[round(float(c), 3) for c in V[v]] for v in s["vertex_ids"]]
        seams.append(r)

    sel = [int(f) for f in doc.get("selected_faces", []) if 0 <= int(f) < len(F)]
    return {
        "summary": mesh_io._summary(mesh).model_dump(),
        "name": doc.get("name", ""),
        "seams": seams,
        "selected_faces": sel,
        "settings": doc.get("settings", {}),
        "warnings": warnings,
    }
