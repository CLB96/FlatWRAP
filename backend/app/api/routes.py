"""Endpoints de la API de FlatWrap.

Flujo completo:
    POST /api/mesh            (Fase 1) cargar STL/OBJ
    GET  /api/mesh/geometry   (Fase 1) geometría para el visor Three.js
    POST /api/seams           (Fase 2) crear costura geodésica
    DELETE /api/seams/{id}    (Fase 2) borrar costura
    POST /api/segment         (Fase 3) cortar malla -> parches
    POST /api/flatten/{patch} (Fase 4) aplanar parche -> UV + distorsión
    POST /api/export/dxf      (Fase 5) DXF escala 1:1
"""

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.core import (
    mesh_io,
    geodesics,
    segmentation,
    selection,
    flattening,
    export_dxf,
)
from app.core.session import SESSION
from app.models.schemas import (
    ExportRequest,
    FloodRequest,
    GeodesicRequest,
    MeshSummary,
    SeamPreviewRequest,
    SegmentRequest,
    SelectionInfoRequest,
)

router = APIRouter()


# ----------------------------------------------------------------- Fase 1
@router.post("/mesh", response_model=MeshSummary)
async def upload_mesh(file: UploadFile):
    """Carga un STL/OBJ, lo limpia y lo guarda en la sesión."""
    data = await file.read()
    try:
        summary = mesh_io.load_mesh(data, filename=file.filename or "mesh.stl")
    except mesh_io.MeshLoadError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return summary


@router.get("/mesh/geometry")
def get_geometry():
    """Vértices y caras (aplanados) para construir el BufferGeometry en Three.js."""
    if SESSION.mesh is None:
        raise HTTPException(status_code=404, detail="No hay malla cargada")
    return JSONResponse(mesh_io.geometry_payload(SESSION.mesh))


@router.post("/mesh/subdivide", response_model=MeshSummary)
def subdivide_mesh():
    """Refina la malla (4x caras) para suavizar el aplanado. Limpia selección."""
    try:
        return mesh_io.subdivide_current()
    except mesh_io.MeshLoadError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ----------------------------------------------------------------- Fase 2
@router.post("/seams")
def create_seam(req: GeodesicRequest):
    """Conecta los vértices clickeados con rutas geodésicas y registra la costura."""
    if SESSION.mesh is None:
        raise HTTPException(status_code=404, detail="No hay malla cargada")
    try:
        return geodesics.create_seam(req.vertex_ids, closed=req.closed)
    except geodesics.SeamError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/seams/preview")
def preview_seam(req: SeamPreviewRequest):
    """Traza el camino de la costura en construcción sin registrarla."""
    if SESSION.mesh is None:
        raise HTTPException(status_code=404, detail="No hay malla cargada")
    try:
        return geodesics.preview_seam(req.vertex_ids, closed=req.closed)
    except geodesics.SeamError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.delete("/seams/{seam_id}")
def delete_seam(seam_id: int):
    if not SESSION.remove_seam(seam_id):
        raise HTTPException(status_code=404, detail="Costura no encontrada")
    return {"ok": True}


@router.get("/seams")
def list_seams():
    return SESSION.seams_payload()


# ------------------------------------------------- Fase 2.5: Cara A
@router.post("/selection/flood")
def selection_flood(req: FloodRequest):
    """Relleno por ángulo: caras de la cara A conectadas con el clic."""
    if SESSION.mesh is None:
        raise HTTPException(status_code=404, detail="No hay malla cargada")
    try:
        return selection.flood_select(req.face_id, req.angle_deg, req.side_deg)
    except selection.SelectionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/selection/info")
def selection_info(req: SelectionInfoRequest):
    """Área y conteo de la selección actual (feedback en vivo del pincel)."""
    if SESSION.mesh is None:
        raise HTTPException(status_code=404, detail="No hay malla cargada")
    try:
        return selection.selection_info(req.face_ids)
    except selection.SelectionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ----------------------------------------------------------------- Fase 3
@router.post("/segment")
def segment(req: SegmentRequest):
    """Subdivide la CARA A seleccionada por las costuras activas.

    Regla de negocio (ver core/segmentation.py): la cara A es el patrón
    primario; cada costura que la cruza la divide. El número de patrones =
    componentes conexas de la cara A tras remover las adyacencias cortadas.
    Una costura que no llega a separar nada se reporta como warning.
    """
    if SESSION.mesh is None:
        raise HTTPException(status_code=404, detail="No hay malla cargada")
    try:
        return segmentation.segment_by_seams(req.selected_faces)
    except segmentation.SegmentationError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.delete("/patches/{patch_id}")
def delete_patch(patch_id: int):
    """Elimina un patrón (p. ej. caras de la Cara B que se colaron)."""
    if patch_id not in SESSION.patches:
        raise HTTPException(status_code=404, detail="Patrón no encontrado")
    del SESSION.patches[patch_id]
    if SESSION.face_patch is not None:
        SESSION.face_patch = [
            -1 if p == patch_id else p for p in SESSION.face_patch
        ]
    return {
        "n_patches": len(SESSION.patches),
        "face_patch": SESSION.face_patch,
        "patches": [
            {"patch_id": pid, "area_cm2": round(p["area_3d"] / 100.0, 1)}
            for pid, p in sorted(SESSION.patches.items())
        ],
    }


# ----------------------------------------------------------------- Fase 4
@router.post("/flatten/{patch_id}")
def flatten(patch_id: int, method: str = "relax"):
    """Aplana un parche (method: 'unfold', 'project' u 'arap') -> UV + distorsión."""
    try:
        return flattening.flatten_patch(patch_id, method=method)
    except KeyError:
        raise HTTPException(status_code=404, detail="Parche no encontrado")
    except flattening.FlattenError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/flatten-all")
def flatten_all(method: str = "relax"):
    try:
        return flattening.flatten_all_patches(method=method)
    except flattening.FlattenError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


# ----------------------------------------------------------------- Fase 5
@router.post("/export/dxf")
def export(req: ExportRequest):
    """Genera el DXF escala 1:1 (mm) con todos los parches aplanados."""
    try:
        path = export_dxf.export_patterns(
            seam_allowance_mm=req.seam_allowance_mm,
            add_notches=req.add_notches,
            notch_spacing_mm=req.notch_spacing_mm,
        )
    except export_dxf.ExportError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return FileResponse(path, filename="flatwrap_pattern.dxf")
