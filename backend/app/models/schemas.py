"""Esquemas de datos (request/response) de la API."""

from pydantic import BaseModel, Field


class MeshSummary(BaseModel):
    """Resumen de la malla cargada que se envía al frontend."""

    session_id: str
    n_vertices: int
    n_faces: int
    units: str = "mm"
    bbox_min: list[float]
    bbox_max: list[float]
    watertight: bool


class GeodesicRequest(BaseModel):
    """Costura: secuencia de vértices clickeados sobre la malla.

    El backend conecta cada par consecutivo con la ruta geodésica
    (camino más corto sobre la superficie).
    """

    vertex_ids: list[int] = Field(min_length=2)
    closed: bool = False  # True si la costura cierra un loop


class SeamPreviewRequest(BaseModel):
    """Vista previa en vivo de la costura mientras el usuario hace clics."""

    vertex_ids: list[int] = Field(min_length=1)
    closed: bool = False


class GeodesicResponse(BaseModel):
    seam_id: int
    # Polilínea 3D de la costura para dibujarla en el visor
    polyline: list[list[float]]
    # Aristas de la malla que serán cortadas en la segmentación
    edge_path: list[list[int]]


class FloodRequest(BaseModel):
    """Clic de relleno por ángulo para seleccionar la cara A."""

    face_id: int = Field(ge=0)
    angle_deg: float = Field(default=35.0, gt=0, le=180)
    # No cruzar a caras que miran a más de este ángulo del clic (frena en el
    # canto/reverso aunque sea un filete suave). 180 = sin filtro.
    side_deg: float = Field(default=85.0, gt=0, le=180)


class SelectionInfoRequest(BaseModel):
    """Caras seleccionadas para calcular área/conteo en vivo."""

    face_ids: list[int] = Field(default_factory=list)


class SegmentRequest(BaseModel):
    """Subdivisión de la cara A: requiere las caras seleccionadas."""

    selected_faces: list[int] = Field(min_length=1)
    # Tolerancia (mm) para prolongar el extremo de una costura hasta el borde
    # de la Cara A / otra costura. 0 = desactivado.
    snap_mm: float = Field(default=5.0, ge=0, le=50)


class SegmentationResponse(BaseModel):
    """Resultado de subdividir la cara A por las costuras."""

    n_patches: int
    # face_id -> patch_id (-1 = fuera de la cara A), para colorear el visor
    face_patch: list[int]


class FlattenResponse(BaseModel):
    """Patrón 2D de un parche."""

    patch_id: int
    # Coordenadas UV en mm (escala real 1:1)
    uv: list[list[float]]
    faces: list[list[int]]
    # Distorsión de área por triángulo (1.0 = sin distorsión)
    distortion: list[float]
    max_distortion_pct: float


class ExportRequest(BaseModel):
    seam_allowance_mm: float = 10.0
    add_notches: bool = True
    notch_spacing_mm: float = 50.0


class ProjectSaveRequest(BaseModel):
    """Estado que solo conoce el frontend (Cara A, ajustes) para guardar el proyecto."""

    selected_faces: list[int] = Field(default_factory=list)
    settings: dict = Field(default_factory=dict)
    name: str = ""
