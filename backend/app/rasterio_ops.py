"""Raster dataset wrapper and all numeric operations.

Raw file values vs calibrated values:
  raw        = the integer/float number physically stored in the GeoTIFF
  calibrated = raw * scale + offset, with nodata excluded

Display ("resampled") values are reprojections of RAW values onto the
requested Web-Mercator view grid, calibrated afterwards. They exist for
visualisation only - point inspection always re-reads source cells and is
computed independently of whatever is on screen.
"""
from __future__ import annotations

import math
import threading
from typing import Any

import numpy as np
import rasterio
import rasterio.warp
from rasterio.crs import CRS
from rasterio.enums import Resampling

from . import geo

WEB_MERCATOR = CRS.from_epsg(3857)
WGS84 = CRS.from_epsg(4326)

RESAMPLING = {
    "bilinear": Resampling.bilinear,
    "nearest": Resampling.nearest,
    "average": Resampling.average,
    "cubic": Resampling.cubic,
}

# A view grid larger than this is rejected (memory / bandwidth guard).
MAX_GRID_PIXELS = 1400 * 1400


class RasterError(Exception):
    """File-level problem that makes a dataset unusable."""


def _is_float(dtype) -> bool:
    return np.issubdtype(np.dtype(dtype), np.floating)


def _get_scale_offset(src, bidx) -> tuple[float, float]:
    scales = src.scales
    offsets = src.offsets
    scale = float(scales[bidx - 1]) if scales else 1.0
    offset = float(offsets[bidx - 1]) if offsets else 0.0
    return scale, offset


def _sample_stats(src, bidx, nodata, target: int = 2_000_000) -> dict:
    """Estimated min/max/mean from a strided decimation of the band.

    Only used to seed display color ramps; never used for comparison math.
    Never reads the whole array (files may be hundreds of MB).
    """
    height, width = src.height, src.width
    total = height * width
    step = max(1, int(math.sqrt(total / target)))
    try:
        arr = src.read(
            bidx,
            out_shape=(max(1, height // step), max(1, width // step)),
            resampling=Resampling.nearest,
        )
        vals = arr.reshape(-1)
        if nodata is not None:
            if _is_float(arr.dtype) and isinstance(nodata, float) \
                    and math.isnan(nodata):
                vals = vals[~np.isnan(vals)]
            else:
                vals = vals[vals != nodata]
        if vals.size == 0:
            return {"sampled": True, "count": 0}
        vals = vals.astype("float64")
        return {
            "sampled": True,
            "step": step,
            "count": int(vals.size),
            "min": float(vals.min()),
            "max": float(vals.max()),
            "mean": float(vals.mean()),
        }
    except Exception:
        return {"sampled": False, "count": 0}


def describe_band(src: rasterio.DatasetReader, bidx: int) -> dict[str, Any]:
    tags = src.tags(bidx)
    scale, offset = _get_scale_offset(src, bidx)

    # Measurement unit: band tag wins (vertical unit of the measurement);
    # for projected data without a band tag we fall back to the CRS unit.
    raw_unit = (
        tags.get("units")
        or tags.get("UNIT")
        or tags.get("vertical_units")
        or None
    )
    unit = geo.normalize_unit(raw_unit)
    unit_source = "band_tag" if raw_unit else None
    if unit is None and not src.crs.is_geographic:
        try:
            unit = geo.normalize_unit(src.crs.linear_units)
            unit_source = "crs_linear_units"
        except Exception:
            pass

    nodata = src.nodata
    if nodata is not None and isinstance(nodata, float) and math.isnan(nodata):
        nodata_json: Any = "NaN"
    else:
        nodata_json = None if nodata is None else float(nodata)
    stats = _sample_stats(src, bidx, nodata)

    return {
        "index": bidx,
        "description": src.descriptions[bidx - 1]
        or tags.get("DESCRIPTION")
        or f"band {bidx}",
        "dtype": src.dtypes[bidx - 1],
        "nodata": nodata_json,
        "scale": scale,
        "offset": offset,
        "unit": unit,
        "unit_source": unit_source,
        "unit_raw": raw_unit,
        "color_interpretation": (
            str(src.colorinterp[bidx - 1])
            if src.colorinterp and src.colorinterp[bidx - 1] is not None
            else None
        ),
        "sample_stats": stats,
        "tags": {k: str(v) for k, v in tags.items()},
    }


class Dataset:
    """An opened GeoTIFF tied to one upload slot of a session."""

    def __init__(self, dataset_id: str, filename: str, path: str):
        self.dataset_id = dataset_id
        self.filename = filename
        self.path = path
        self.lock = threading.Lock()
        try:
            self.src = rasterio.open(path)
        except Exception as exc:
            raise RasterError(
                f"无法以 GeoTIFF 打开 {filename}: {exc}"
            ) from exc
        if self.src.crs is None:
            self.src.close()
            raise RasterError(
                f"{filename} 缺少坐标参考（CRS）。本工具只在地理意义上对齐"
                "栅格，无法接受无 CRS 的文件。"
            )
        if self.src.count < 1:
            self.src.close()
            raise RasterError(f"{filename} 中没有任何波段。")

    # ---- metadata -----------------------------------------------------
    def metadata(self) -> dict[str, Any]:
        with self.lock:
            src = self.src
            bounds = src.bounds
            wgs84_bounds = geo.reproject_bounds(
                (bounds.left, bounds.bottom, bounds.right, bounds.top),
                src.crs, WGS84,
            )
            res = src.res
            res_m = None
            if not src.crs.is_geographic:
                res_m = (float(res[0]), float(res[1]))
            bands = [describe_band(src, b) for b in range(1, src.count + 1)]
            return {
                "dataset_id": self.dataset_id,
                "filename": self.filename,
                "driver": src.driver,
                "width": src.width,
                "height": src.height,
                "band_count": src.count,
                "crs": geo.describe_crs(src.crs),
                "bounds_native": {
                    "left": bounds.left, "bottom": bounds.bottom,
                    "right": bounds.right, "top": bounds.top,
                },
                "bounds_wgs84": {
                    "left": wgs84_bounds[0], "bottom": wgs84_bounds[1],
                    "right": wgs84_bounds[2], "top": wgs84_bounds[3],
                },
                "resolution_native": {"x": float(res[0]), "y": float(res[1])},
                "resolution_metres": (
                    {"x": res_m[0], "y": res_m[1]} if res_m else None
                ),
                "transform": list(src.transform)[:6],
                "bands": bands,
                "tiled": bool(src.is_tiled),
            }

    # ---- view rendering ----------------------------------------------
    def read_view(
        self,
        bidx: int,
        merc_bounds: tuple[float, float, float, float],
        width: int,
        height: int,
        resampling: Resampling,
    ) -> dict[str, Any]:
        """Calibrated values + validity mask on an exact Web Mercator grid.

        Returns float32 measurement-unit values (scale/offset applied),
        NaN where the file has nodata or the view falls outside the raster,
        plus a 0/1 mask. ``raw_resampled`` is kept separate so the UI can
        show that display sampling is not a source-cell reading.
        """
        if width * height > MAX_GRID_PIXELS:
            raise RasterError("请求网格过大（内存保护）。")
        left, bottom, right, top = merc_bounds
        if not all(math.isfinite(v) for v in merc_bounds):
            raise RasterError("视图范围不是有限数值。")
        if right <= left or top <= bottom or width <= 0 or height <= 0:
            raise RasterError("视图范围或尺寸无效。")

        dst_transform = rasterio.transform.from_bounds(
            left, bottom, right, top, width, height
        )

        with self.lock:
            src = self.src
            scale, offset = _get_scale_offset(src, bidx)
            nodata = src.nodata

        # Reproject directly from the SOURCE to the exact mercator grid.
        # GDAL's warper then selects an overview pyramid level by comparing
        # source vs destination resolution, so a zoomed-out view over a
        # hundreds-of-MB file reads overview tiles (windowed IO), never the
        # full-resolution array. A float32/NaN destination with explicit
        # src_nodata makes integer sentinels honoured as a mask during warp,
        # preventing nodata bleeding under bilinear/cubic sampling.
        src_nodata = None
        if nodata is not None:
            src_nodata = (
                float("nan") if isinstance(nodata, float) and math.isnan(nodata)
                else nodata
            )
        raw = np.full((height, width), np.nan, dtype="float32")
        rasterio.warp.reproject(
            source=rasterio.band(src, bidx),
            destination=raw,
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src_nodata,
            dst_transform=dst_transform,
            dst_crs=WEB_MERCATOR,
            dst_nodata=float("nan"),
            resampling=resampling,
            num_threads=2,
        )

        valid = ~np.isnan(raw)
        calibrated = raw.astype("float32", copy=True)
        if valid.any():
            calibrated[valid] = (
                calibrated[valid] * np.float32(scale) + np.float32(offset)
            )

        stats = None
        if valid.any():
            cv = calibrated[valid]
            stats = {
                "min": float(cv.min()), "max": float(cv.max()),
                "mean": float(cv.mean()), "count": int(valid.sum()),
            }
        return {
            "values": calibrated,
            "raw_resampled": raw,
            "mask": valid.astype("uint8"),
            "stats": stats,
        }

    # ---- point inspection --------------------------------------------
    def inspect_point(self, bidx: int, lon: float, lat: float,
                      radius_cells: int = 1) -> dict[str, Any]:
        """Read the SOURCE cell under a geographic point (WGS84 lon/lat).

        Independent of the display grid: reports integer row/col, the raw
        stored value, its calibrated form and the nodata decision.
        """
        with self.lock:
            src = self.src
            scale, offset = _get_scale_offset(src, bidx)
            nodata = src.nodata

            xs, ys = rasterio.warp.transform(WGS84, src.crs, [lon], [lat])
            x, y = xs[0], ys[0]
            inside = (
                src.bounds.left <= x <= src.bounds.right
                and src.bounds.bottom <= y <= src.bounds.top
            )

            row_f, col_f = src.index(x, y)
            row, col = int(math.floor(row_f)), int(math.floor(col_f))

            result: dict[str, Any] = {
                "dataset_id": self.dataset_id,
                "native_x": float(x),
                "native_y": float(y),
                "row_float": float(row_f),
                "col_float": float(col_f),
                "row": row,
                "col": col,
                "fractional_row": float(row_f - row),
                "fractional_col": float(col_f - col),
                "inside_bounds": bool(inside),
                "pixel_size_native": {
                    "x": float(src.res[0]), "y": float(src.res[1])},
            }

            if not inside:
                result.update(
                    comparable=False, reason="point_outside_dataset_bounds"
                )
                return result

            r0 = max(0, row - radius_cells)
            c0 = max(0, col - radius_cells)
            r1 = min(src.height, row + radius_cells + 1)
            c1 = min(src.width, col + radius_cells + 1)
            window = rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0)
            raw_tile = src.read(bidx, window=window)
            center_raw = raw_tile[row - r0, col - c0]
            center_float = float(center_raw)
            is_nan = math.isnan(center_float)
            nodata_is_nan = (
                nodata is not None and isinstance(nodata, float)
                and math.isnan(nodata)
            )
            center_nodata = is_nan or (
                nodata is not None and not nodata_is_nan
                and center_float == float(nodata)
            )

            cal = None
            if not center_nodata:
                cal = float(center_raw) * scale + offset

            if nodata is not None and isinstance(nodata, float) \
                    and math.isnan(nodata):
                nodata_out: Any = "NaN"
            else:
                nodata_out = None if nodata is None else float(nodata)

            result.update({
                "raw_value": None if center_nodata else float(center_raw),
                "raw_dtype": str(raw_tile.dtype),
                "calibrated_value": cal,
                "is_nodata": bool(center_nodata),
                "nodata_value": nodata_out,
                "scale": float(scale),
                "offset": float(offset),
                "comparable": bool(not center_nodata),
                "reason": "nodata_cell" if center_nodata else None,
                "window": {
                    "row_off": r0, "col_off": c0,
                    "height": r1 - r0, "width": c1 - c0,
                    "raw": _raw_tile_payload(raw_tile, nodata),
                },
            })
            return result

    def close(self):
        try:
            with self.lock:
                self.src.close()
        except Exception:
            pass


def _raw_tile_payload(tile: np.ndarray, nodata) -> list:
    out: list[float | None] = []
    flat = tile.reshape(-1)
    for v in flat.tolist():
        if isinstance(v, float) and math.isnan(v):
            out.append(None)
        elif nodata is not None and v == nodata:
            out.append(None)
        else:
            out.append(v)
    return out
