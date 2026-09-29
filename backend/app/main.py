import json
import struct
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .geo import (
    RasterInputError,
    compare_units,
    comparison_info,
    diff_grids,
    query_point,
    read_view_grid,
    validate_band,
)
from .models import PointRequest, ViewRequest
from .session import SessionNotFound, SessionStore

BASE_DIR = Path(__file__).resolve().parents[2]
SESSION_ROOT = BASE_DIR / ".runtime" / "sessions"
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"
SAMPLE_DIR = BASE_DIR / "samples"

app = FastAPI(title="GeoTIFF Geographic Check", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
store = SessionStore(SESSION_ROOT)


@app.on_event("shutdown")
def _shutdown() -> None:
    store.shutdown()


def session_from_header(x_session_id: str | None, create: bool = False):
    if not x_session_id:
        if create:
            return store.create()
        raise HTTPException(status_code=400, detail="缺少 X-Session-ID")
    if not x_session_id.replace("-", "").isalnum() or len(x_session_id) > 64:
        raise HTTPException(status_code=400, detail="X-Session-ID 格式无效")
    try:
        return store.get(x_session_id)
    except SessionNotFound:
        if create:
            # A caller may reuse an upload URL after a server restart. The client
            # session id is only an ephemeral check-session key, not an account.
            try:
                return store.get_or_create(x_session_id)
            except (ValueError, FileExistsError):
                raise HTTPException(status_code=409, detail="检查会话建立冲突，请重试")
        raise HTTPException(status_code=404, detail="检查会话不存在或已过期")


def state_payload(session) -> dict[str, Any]:
    entries = session.snapshot()
    a = entries["a"]
    b = entries["b"]
    # The browser selects band 1 by default; exposing that comparison immediately
    # still lets users change either band before entering the shared footprint.
    comparison = comparison_info(
        a.metadata if a else None,
        b.metadata if b else None,
        1 if a else None,
        1 if b else None,
    )
    return {
        "session_id": session.id,
        "a": a.metadata.model_dump() if a else None,
        "b": b.metadata.model_dump() if b else None,
        "comparison": comparison,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/session")
def create_session(x_session_id: str | None = Header(default=None)) -> dict[str, Any]:
    if x_session_id:
        session = session_from_header(x_session_id, create=True)
    else:
        session = store.create()
    return state_payload(session)


@app.get("/api/session")
def get_session(x_session_id: str | None = Header(default=None)) -> dict[str, Any]:
    return state_payload(session_from_header(x_session_id))


@app.post("/api/upload/{slot}")
async def upload_raster(
    slot: str,
    file: UploadFile = File(...),
    x_session_id: str | None = Header(default=None),
):
    if slot not in {"a", "b"}:
        raise HTTPException(status_code=400, detail="slot 必须是 a 或 b")
    session = session_from_header(x_session_id, create=True)
    try:
        await run_in_threadpool(session.replace_file, slot, file.file, file.filename or "raster.tif")
    except (RasterInputError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=f"{'A' if slot == 'a' else 'B'} 文件不能使用：{exc}") from exc
    finally:
        await file.close()
    return await run_in_threadpool(state_payload, session)


@app.post("/api/point")
def point_query(request: PointRequest, x_session_id: str | None = Header(default=None)) -> dict[str, Any]:
    session = session_from_header(x_session_id)
    with session.computation_lock:
        return _point_query_unlocked(session, request)


def _point_query_unlocked(session, request: PointRequest) -> dict[str, Any]:
    entries = session.snapshot()
    a = entries["a"]
    b = entries["b"]
    result_a = query_point(a, request.band_a, request.lon, request.lat)
    result_b = query_point(b, request.band_b, request.lon, request.lat)

    both_valid = bool(
        result_a.get("available")
        and result_b.get("available")
        and result_a.get("scaled_value") is not None
        and result_b.get("scaled_value") is not None
    )
    difference = None
    units = None
    warning = None

    if both_valid and a is not None and b is not None:
        try:
            ba = validate_band(a.metadata, request.band_a)
            bb = validate_band(b.metadata, request.band_b)
            ok, unit_warning, unit_a, unit_b = compare_units(ba.units, bb.units)
            result_a["units"] = unit_a
            result_b["units"] = unit_b
            if ok:
                units = ba.units.strip()
                difference = float(result_b["scaled_value"] - result_a["scaled_value"])
            else:
                warning = unit_warning
        except Exception as exc:
            warning = f"不能计算选点差值：{exc}"
    elif result_a.get("available") or result_b.get("available"):
        reasons = []
        if result_a.get("reason"):
            reasons.append(f"A：{result_a['reason']}")
        if result_b.get("reason"):
            reasons.append(f"B：{result_b['reason']}")
        warning = "该屏幕地理点没有双方可比较的标定值" + ("；" + "；".join(reasons) if reasons else "")

    return {
        "lon": request.lon,
        "lat": request.lat,
        "band_a": request.band_a,
        "band_b": request.band_b,
        "a": result_a,
        "b": result_b,
        "both_valid": both_valid,
        "difference": difference,
        "difference_units": units,
        "warning": warning,
    }


def _grid_bytes(grid: np.ndarray | None, expected: int) -> bytes:
    if grid is None:
        return b""
    if grid.dtype != np.float32 or int(grid.size) != expected:
        raise RuntimeError("internal grid encoding error")
    return np.ascontiguousarray(grid, dtype="<f4").tobytes()


@app.post("/api/view")
def view(request: ViewRequest, x_session_id: str | None = Header(default=None)) -> Response:
    session = session_from_header(x_session_id)
    with session.computation_lock:
        return _view_unlocked(session, request)


def _view_unlocked(session, request: ViewRequest) -> Response:
    entries = session.snapshot()
    a = entries["a"]
    b = entries["b"]
    bnds = request.bounds
    bounds = (bnds.west, bnds.south, bnds.east, bnds.north)

    comparison = comparison_info(
        a.metadata if a else None,
        b.metadata if b else None,
        request.band_a if a else None,
        request.band_b if b else None,
    )
    comparable_for_files = bool(comparison.get("comparable"))

    grid_a, side_a = (None, {"file_id": None, "available": False, "reason": "A 尚未打开"})
    grid_b, side_b = (None, {"file_id": None, "available": False, "reason": "B 尚未打开"})
    if a is not None:
        grid_a, side_a = read_view_grid(a, request.band_a, bounds, request.width, request.height)
    if b is not None:
        grid_b, side_b = read_view_grid(b, request.band_b, bounds, request.width, request.height)

    # Difference requires numerical compatibility and both current grids. If one
    # side is out of this view, that is missing coverage, not a zero difference.
    diff, diff_info = diff_grids(
        grid_a,
        grid_b,
        comparable_for_files and grid_a is not None and grid_b is not None,
        comparison.get("reason") if not comparable_for_files else None,
    )

    header: dict[str, Any] = {
        "format": "geotiff-check-grid-v1",
        "request_id": request.request_id,
        "crs": "EPSG:4326",
        "bounds": list(bounds),
        "width": request.width,
        "height": request.height,
        "arrays": {"a": grid_a is not None, "b": grid_b is not None, "diff": diff is not None},
        "a": side_a,
        "b": side_b,
        "difference": diff_info,
        "comparison": comparison,
    }
    payload = bytearray()
    payload.extend(b"GTCG")
    header_bytes = json.dumps(header, allow_nan=False, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    payload.extend(struct.pack("<I", len(header_bytes)))
    payload.extend(header_bytes)
    expected = request.width * request.height
    payload.extend(_grid_bytes(grid_a, expected))
    payload.extend(_grid_bytes(grid_b, expected))
    payload.extend(_grid_bytes(diff, expected))
    return Response(bytes(payload), media_type="application/x-geotiff-check-grid")


@app.exception_handler(Exception)
async def unexpected_error(_request, exc):  # pragma: no cover - defensive
    if isinstance(exc, HTTPException):
        raise exc
    return Response(
        content=json.dumps({"detail": f"服务端错误：{exc}"}, ensure_ascii=False).encode("utf-8"),
        status_code=500,
        media_type="application/json",
    )


if SAMPLE_DIR.exists():
    app.mount("/samples", StaticFiles(directory=str(SAMPLE_DIR)), name="samples")

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="assets")

    @app.get("/")
    def index() -> Response:
        return Response((FRONTEND_DIST / "index.html").read_bytes(), media_type="text/html")

    @app.get("/{path:path}")
    def spa_fallback(path: str) -> Response:
        # Do not shadow API calls or real sample/assets files.
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        target = FRONTEND_DIST / path
        if target.is_file():
            return Response(target.read_bytes())
        return Response((FRONTEND_DIST / "index.html").read_bytes(), media_type="text/html")
