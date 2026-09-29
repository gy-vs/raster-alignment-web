"""FastAPI application: upload, current-view raster compute, point query.

Endpoints are independently callable. Every numeric result shown in the
browser is computed here with rasterio; the frontend only colorises grids
and manages the viewport.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware  # noqa: F401
from pydantic import BaseModel, Field

from . import compare as compare_mod
from .protocol import encode_view
from .rasterio_ops import RESAMPLING, RasterError
from .session import SLOTS, save_slot_upload_async, store

TEMP_DIR = tempfile.mkdtemp(prefix="geocmp_uploads_")

app = FastAPI(
    title="GeoTIFF 双栅格地理对照",
    version="1.0.0",
    description="浏览器端 GeoTIFF 数值对照后端（rasterio / 自带 GDAL）",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MAX_VIEW_SIDE = 1400
MERC_LIMIT = 20037508.342789244


# ----------------------------------------------------------------------
# models
class BandSelect(BaseModel):
    band: int = Field(ge=1)


class ViewRequest(BaseModel):
    slot: str | None = None
    bounds: dict[str, float]
    width: int = Field(ge=1, le=MAX_VIEW_SIDE)
    height: int = Field(ge=1, le=MAX_VIEW_SIDE)
    mode: str = "swipe"  # swipe | diff
    resampling: str = "bilinear"
    expected_ids: dict[str, str | None] = Field(default_factory=dict)


# ----------------------------------------------------------------------
# helpers
def _require_session(session_id: str):
    try:
        return store.get(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


def _slot_ds(session, slot_name: str):
    slot = session.get_slot(slot_name)
    if slot.dataset is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "slot_empty", "slot": slot_name,
                    "message": f"槽位 {slot_name.upper()} 还没有可读取的文件。"},
        )
    return slot


def _check_expected(session, expected: dict[str, str | None]):
    """Refuse to compute if the client view targets a stale file version."""
    for name in SLOTS:
        if name in expected and expected[name] is not None:
            current = session.get_slot(name).dataset
            current_id = current.dataset_id if current else None
            if current_id != expected[name]:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "dataset_version_mismatch",
                        "slot": name,
                        "expected": expected[name],
                        "current": current_id,
                        "message": (
                            "视图对应的文件版本已过期（该槽位已重新上传），"
                            "请使用新文件重新获取视图。"
                        ),
                    },
                )


def _comparison_payload(session) -> dict[str, Any]:
    a = session.slots["a"]
    b = session.slots["b"]
    if not a.dataset or not b.dataset:
        return {
            "comparable": False,
            "blockers": [{
                "code": "need_two_files",
                "message": "需要 A、B 各成功打开一份文件后才能进行数值比较。",
            }],
            "warnings": [],
            "overlap_wgs84": None,
        }
    return compare_mod.evaluate(
        a.dataset.metadata(), a.band,
        b.dataset.metadata(), b.band,
    )


def _validate_bounds(b: dict[str, float]):
    keys = {"left", "bottom", "right", "top"}
    if set(b.keys()) != keys:
        raise HTTPException(
            status_code=422,
            detail="bounds 必须且只能包含 left/bottom/right/top（Web 墨卡托米）。",
        )
    vals = (b["left"], b["bottom"], b["right"], b["top"])
    if any(abs(v) > MERC_LIMIT * 1.01 for v in vals[:2] + vals[2:]):
        raise HTTPException(status_code=422, detail="视图范围超出 Web 墨卡托范围。")
    if b["right"] <= b["left"] or b["top"] <= b["bottom"]:
        raise HTTPException(status_code=422, detail="视图范围无效（右下/左上倒置）。")
    return vals


def _slot_summary(slot) -> dict[str, Any] | None:
    if slot.dataset is None:
        return None
    return {
        "dataset_id": slot.dataset.dataset_id,
        "filename": slot.dataset.filename,
        "band": slot.band,
        "metadata": slot.dataset.metadata(),
    }


# ----------------------------------------------------------------------
# session / state
@app.post("/api/sessions")
def create_session():
    s = store.create()
    return {"session_id": s.session_id, "slots": {"a": None, "b": None}}


@app.get("/api/sessions/{session_id}")
def session_state(session_id: str):
    s = _require_session(session_id)
    return {
        "session_id": s.session_id,
        "slots": {name: _slot_summary(s.get_slot(name)) for name in SLOTS},
        "comparison": _comparison_payload(s),
    }


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str):
    store.drop(session_id)
    return {"ok": True}


# ----------------------------------------------------------------------
# upload / slot management
@app.post("/api/sessions/{session_id}/slots/{slot_name}/upload")
async def upload_file(session_id: str, slot_name: str,
                      file: UploadFile = File(...)):
    s = _require_session(session_id)
    if slot_name not in SLOTS:
        raise HTTPException(status_code=404, detail="槽位只支持 a 或 b。")

    # Stream to disk (files may be hundreds of MB; never buffer fully).
    try:
        path = await save_slot_upload_async(
            file, TEMP_DIR, file.filename or "upload.tif"
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"保存上传文件失败: {exc}"
        )
    finally:
        try:
            await file.close()
        except Exception:
            pass

    # Validate by OPENING. On failure the previous file (if any) stays
    # available; the unusable upload is removed and the error is reported
    # against this specific slot.
    try:
        with s.lock:
            ds = s.replace_slot(slot_name, path, file.filename or "upload.tif")
            meta = ds.metadata()
    except RasterError as exc:
        try:
            os.unlink(path)
        except OSError:
            pass
        existing = s.get_slot(slot_name).dataset
        raise HTTPException(
            status_code=422,
            detail={
                "code": "unreadable_raster",
                "slot": slot_name,
                "message": str(exc),
                "previous_dataset_id": (
                    existing.dataset_id if existing else None
                ),
            },
        )

    return {
        "slot": slot_name,
        "dataset_id": ds.dataset_id,
        "filename": ds.filename,
        "metadata": meta,
        "comparison": _comparison_payload(s),
    }


@app.delete("/api/sessions/{session_id}/slots/{slot_name}")
def remove_slot(session_id: str, slot_name: str):
    s = _require_session(session_id)
    if slot_name not in SLOTS:
        raise HTTPException(status_code=404, detail="槽位只支持 a 或 b。")
    with s.lock:
        slot = s.get_slot(slot_name)
        if slot.dataset is not None:
            slot.dataset.close()
            path = slot.path
            slot.dataset = None
            slot.path = None
            slot.band = 1
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    return {"ok": True, "comparison": _comparison_payload(s)}


@app.put("/api/sessions/{session_id}/slots/{slot_name}/band")
def select_band(session_id: str, slot_name: str, body: BandSelect):
    s = _require_session(session_id)
    slot = _slot_ds(s, slot_name)
    if body.band > slot.dataset.metadata()["band_count"]:
        raise HTTPException(status_code=422, detail="波段编号超出范围。")
    slot.band = body.band
    return {
        "slot": slot_name,
        "band": slot.band,
        "comparison": _comparison_payload(s),
    }


@app.get("/api/sessions/{session_id}/comparison")
def get_comparison(session_id: str):
    s = _require_session(session_id)
    return _comparison_payload(s)


# ----------------------------------------------------------------------
# current view compute
@app.post("/api/sessions/{session_id}/view")
def compute_view(session_id: str, body: ViewRequest):
    s = _require_session(session_id)
    _check_expected(s, body.expected_ids or {})

    if body.resampling not in RESAMPLING:
        raise HTTPException(
            status_code=422,
            detail=f"重采样方法只支持 {sorted(RESAMPLING)}。",
        )
    resampling = RESAMPLING[body.resampling]
    bounds = _validate_bounds(body.bounds)
    if body.width * body.height > MAX_VIEW_SIDE * MAX_VIEW_SIDE:
        raise HTTPException(status_code=422, detail="请求网格过大。")

    mode = body.mode
    if mode not in ("swipe", "diff"):
        raise HTTPException(status_code=422, detail="mode 只支持 swipe / diff。")

    comp = _comparison_payload(s)
    slot_a = s.get_slot("a")
    slot_b = s.get_slot("b")

    header_base: dict[str, Any] = {
        "session_id": s.session_id,
        "mode": mode,
        "resampling": body.resampling,
        "width": body.width,
        "height": body.height,
        "bounds": {
            "left": bounds[0], "bottom": bounds[1],
            "right": bounds[2], "top": bounds[3],
        },
        "dataset_ids": {
            "a": slot_a.dataset.dataset_id if slot_a.dataset else None,
            "b": slot_b.dataset.dataset_id if slot_b.dataset else None,
        },
        "bands": {"a": slot_a.band, "b": slot_b.band},
        "comparison": comp,
    }

    layers: dict[str, dict] = {}

    if mode == "swipe":
        # One layer may be absent (other file failed / not yet uploaded).
        for name, slot in (("a", slot_a), ("b", slot_b)):
            if slot.dataset is None:
                continue
            try:
                view = slot.dataset.read_view(
                    slot.band, bounds, body.width, body.height, resampling
                )
            except RasterError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"slot": name, "message": str(exc)},
                )
            layers[name] = {
                "values": view["values"],
                "mask": view["mask"],
                "stats": view["stats"],
            }
        if not layers:
            raise HTTPException(status_code=409, detail="还没有任何可显示的文件。")
        header_base["layer_stats"] = {
            name: l.pop("stats") for name, l in layers.items()
        }
        return _binary_response(encode_view(header_base, layers))

    # ---- diff mode: hard-block unless genuinely comparable ---------------
    if slot_a.dataset is None or slot_b.dataset is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "need_two_files",
                    "message": "差值模式需要两份文件均已成功打开。"},
        )
    if not comp["comparable"]:
        # Never synthesise a misleading difference image.
        raise HTTPException(
            status_code=422,
            detail={
                "code": "not_comparable",
                "blockers": comp["blockers"],
                "warnings": comp["warnings"],
                "message": "两份数据当前不可进行数值比较，未生成差值图。",
            },
        )

    va = slot_a.dataset.read_view(
        slot_a.band, bounds, body.width, body.height, resampling
    )
    vb = slot_b.dataset.read_view(
        slot_b.band, bounds, body.width, body.height, resampling
    )
    diff = compare_mod.compute_diff(va, vb, comp)
    layers["diff"] = {
        "values": diff["values"],
        "mask": diff["mask_both"],
        "mask_a": diff["mask_a"],
        "mask_b": diff["mask_b"],
        "only_a": diff["only_a"],
        "only_b": diff["only_b"],
        "extras": ["mask_a", "mask_b", "only_a", "only_b"],
    }
    header_base["diff_stats"] = diff["stats"]
    header_base["diff_unit"] = diff["unit"]
    header_base["diff_order"] = diff["order"]
    return _binary_response(encode_view(header_base, layers))


def _binary_response(payload: bytes):
    from fastapi.responses import Response

    return Response(
        content=payload,
        # Custom media type; the zlib envelope is part of our own binary
        # protocol (magic-prefixed), decoded client-side, so deliberately
        # no HTTP Content-Encoding to avoid transparent double handling.
        media_type="application/x-geocmp-view",
        headers={"Cache-Control": "no-store"},
    )


# ----------------------------------------------------------------------
# point inspection - source cells, independent of the display grid
@app.get("/api/sessions/{session_id}/point")
def inspect_point(session_id: str, lon: float, lat: float):
    s = _require_session(session_id)
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise HTTPException(status_code=422, detail="经纬度越界。")

    comp = _comparison_payload(s)
    out: dict[str, Any] = {
        "session_id": s.session_id,
        "lon": lon, "lat": lat,
        "comparison": comp,
        "slots": {},
    }
    for name in SLOTS:
        slot = s.get_slot(name)
        if slot.dataset is None:
            out["slots"][name] = {"available": False,
                                  "reason": "no_file"}
            continue
        info = slot.dataset.inspect_point(slot.band, lon, lat)
        info["available"] = True
        info["filename"] = slot.dataset.filename
        out["slots"][name] = info

    # Numeric relation AT THE SOURCE CELLS (calibrated, unit-normalised).
    # This is deliberately a cell-reading comparison, not a screen pixel.
    a = out["slots"].get("a", {})
    b = out["slots"].get("b", {})
    point_diff = None
    if (a.get("available") and b.get("available")
            and a.get("comparable") and b.get("comparable")
            and comp.get("comparable")):
        factor = float(comp.get("unit_conversion_a_to_b", 1.0))
        va = a["calibrated_value"] * factor
        vb = b["calibrated_value"]
        point_diff = {
            "a_minus_b": va - vb,
            "unit": comp.get("difference_unit"),
            "a_value_normalized": va,
            "b_value": vb,
            "same_source_cells_geographically": True,
        }
    out["point_difference"] = point_diff
    out["screen_position_comparable"] = point_diff is not None
    return out


# ----------------------------------------------------------------------
# Static frontend (production single-port serving). In dev, Vite on :5173
# proxies /api here and serves its own hot-reloading page.
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")
if os.path.isdir(STATIC_DIR):
    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(STATIC_DIR, "assets")),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    def index_page():
        from fastapi.responses import FileResponse

        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
