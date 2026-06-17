"""Estado de la sesión en memoria.

Al ser una app local de un solo usuario no necesitamos base de datos:
la malla, las costuras y los parches viven en RAM mientras corre el server.
Con 32 GB de RAM, una malla de 500k triángulos (~150 MB procesada) es trivial.
"""

from dataclasses import dataclass, field
from typing import Any

import trimesh


@dataclass
class Seam:
    seam_id: int
    vertex_ids: list[int]          # vértices clickeados por el usuario
    edge_path: list[tuple[int, int]]  # aristas de malla que recorre la geodésica
    polyline: list[list[float]]    # puntos 3D para dibujar en el visor
    closed: bool = False
    # Costura de UNIÓN: su corte se endereza a una recta en el DXF (los dos
    # parches casan plano en AutoCAD). False (por defecto) = costura REAL: se
    # conserva la curva que marcó el usuario (parte del diseño del producto).
    straighten: bool = False


@dataclass
class Session:
    mesh: trimesh.Trimesh | None = None
    # Se incrementa con cada malla cargada. Las caches por malla (selección,
    # geodésicas) se indexan por esta versión y NO por id(mesh), porque
    # Python reutiliza ids tras liberar la malla previa -> cache obsoleta.
    mesh_version: int = 0
    seams: dict[int, Seam] = field(default_factory=dict)
    # Se incrementa al cambiar las costuras; el flood de Cara A lo usa para
    # respetar las costuras como barreras (selección consciente de costuras).
    seams_version: int = 0
    _next_seam_id: int = 1
    # Máscara booleana (len = n_faces) de la CARA A seleccionada
    selected_faces: Any = None
    # Resultados de segmentación: face_id -> patch_id (-1 = fuera de la cara A)
    face_patch: list[int] | None = None
    # patch_id -> dict con submalla y resultado de aplanado
    patches: dict[int, dict[str, Any]] = field(default_factory=dict)

    def reset(self) -> None:
        self.mesh = None
        self.seams.clear()
        self._next_seam_id = 1
        self.selected_faces = None
        self.face_patch = None
        self.patches.clear()

    def add_seam(self, seam: Seam) -> int:
        seam.seam_id = self._next_seam_id
        self.seams[seam.seam_id] = seam
        self._next_seam_id += 1
        self._invalidate_downstream()
        return seam.seam_id

    def remove_seam(self, seam_id: int) -> bool:
        removed = self.seams.pop(seam_id, None) is not None
        if removed:
            self._invalidate_downstream()
        return removed

    def _invalidate_downstream(self) -> None:
        """Cambiar costuras invalida segmentación, aplanados y el flood."""
        self.seams_version += 1
        self.face_patch = None
        self.patches.clear()

    def seams_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "seam_id": s.seam_id,
                "polyline": s.polyline,
                "closed": s.closed,
                "straighten": s.straighten,
            }
            for s in self.seams.values()
        ]


SESSION = Session()
