"""Comparability rules and numeric difference between two datasets.

A difference grid is only produced when the two selected bands describe the
same physical quantity in the same unit (scale/offset and nodata are handled
per file first). Otherwise the reason is reported and no diff is rendered.
"""
from __future__ import annotations

from typing import Any

import numpy as np
from rasterio.crs import CRS

from . import geo
from .rasterio_ops import RasterError

# Values closer than this (after unit conversion to metres) count as the
# same unit; protects against "metre" vs "meter" style aliases too.
UNIT_EPS = 1e-9


def evaluate(
    meta_a: dict[str, Any], band_a_idx: int,
    meta_b: dict[str, Any], band_b_idx: int,
) -> dict[str, Any]:
    """Decide whether the two selected bands are numerically comparable."""
    ba = meta_a["bands"][band_a_idx - 1]
    bb = meta_b["bands"][band_b_idx - 1]
    crs_a = _crs_from_meta(meta_a)
    crs_b = _crs_from_meta(meta_b)

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    # --- unit compatibility ------------------------------------------------
    ua, ub = ba.get("unit"), bb.get("unit")
    fa = geo.unit_to_metres(ua, crs_a)
    fb = geo.unit_to_metres(ub, crs_b)

    unit_conversion_a_to_b = 1.0
    unit_label = ua or ub

    if ua is not None and fa is None:
        blockers.append({
            "code": "angular_or_unknown_unit_a",
            "message": (
                f"A「{meta_a['filename']}」的单位 {ba.get('unit_raw') or ua}"
                " 不是可识别的长度单位（可能是角度），无法与高程/深度量比较。"
            ),
        })
    if ub is not None and fb is None:
        blockers.append({
            "code": "angular_or_unknown_unit_b",
            "message": (
                f"B「{meta_b['filename']}」的单位 {bb.get('unit_raw') or ub}"
                " 不是可识别的长度单位（可能是角度），无法与高程/深度量比较。"
            ),
        })

    if fa is not None and fb is not None:
        if abs(fa - fb) > UNIT_EPS:
            # Convertible length units: compare in metres.
            unit_conversion_a_to_b = fa / fb
            unit_label = "m"
            warnings.append({
                "code": "units_converted",
                "message": (
                    f"两份数据单位不同（{ua} 与 {ub}），差值已统一换算为米："
                    f"1 {ua} = {fa / fb:g} {ub}。"
                ),
            })
    elif fa is None and fb is None and not blockers:
        # Neither file records a usable unit.
        warnings.append({
            "code": "no_units_declared",
            "message": (
                "两份文件都没有声明量测单位，按数值直接相减。请确认它们确实"
                "是同一种量，否则差值没有物理意义。"
            ),
        })
    elif not blockers:
        only = "A" if ua else "B"
        warnings.append({
            "code": "one_side_missing_unit",
            "message": (
                f"仅 {only} 文件声明了单位，另一份没有单位记录，按数值直接"
                "相减。若两者单位不同，差值会失真。"
            ),
        })

    # --- geographic overlap ------------------------------------------------
    overlap = geo.intersection_bounds_wgs84(
        (meta_a["bounds_native"]["left"], meta_a["bounds_native"]["bottom"],
         meta_a["bounds_native"]["right"], meta_a["bounds_native"]["top"]),
        crs_a,
        (meta_b["bounds_native"]["left"], meta_b["bounds_native"]["bottom"],
         meta_b["bounds_native"]["right"], meta_b["bounds_native"]["top"]),
        crs_b,
    )
    if overlap is None:
        blockers.append({
            "code": "no_geographic_overlap",
            "message": "两份栅格在地理范围上没有交集，不存在共同覆盖区域。",
        })

    comparable = len(blockers) == 0
    return {
        "comparable": comparable,
        "blockers": blockers,
        "warnings": warnings,
        "unit_a": ua,
        "unit_b": ub,
        "unit_a_raw": ba.get("unit_raw"),
        "unit_b_raw": bb.get("unit_raw"),
        "unit_a_source": ba.get("unit_source"),
        "unit_b_source": bb.get("unit_source"),
        "unit_conversion_a_to_b": unit_conversion_a_to_b,
        "difference_unit": unit_label,
        "overlap_wgs84": (
            None if overlap is None else {
                "left": overlap[0], "bottom": overlap[1],
                "right": overlap[2], "top": overlap[3],
            }
        ),
        "scale_a": ba["scale"], "offset_a": ba["offset"],
        "nodata_a": ba["nodata"], "dtype_a": ba["dtype"],
        "scale_b": bb["scale"], "offset_b": bb["offset"],
        "nodata_b": bb["nodata"], "dtype_b": bb["dtype"],
    }


def compute_diff(
    view_a: dict[str, Any],
    view_b: dict[str, Any],
    comparability: dict[str, Any],
) -> dict[str, Any]:
    """a - b on the shared view grid, union-mask of valid cells.

    Inputs are already calibrated per-file grids (NaN where missing). The
    diff exists only where BOTH files have a measurement; no filling or
    extrapolation is performed.
    """
    if not comparability["comparable"]:
        raise RasterError("数据不可比较，拒绝生成差值。")

    va = view_a["values"].astype("float64", copy=False)
    vb = view_b["values"].astype("float64", copy=False)
    if va.shape != vb.shape:
        raise RasterError("两份数据视图网格不一致，无法做像元差值。")

    factor = float(comparability["unit_conversion_a_to_b"])
    if abs(factor - 1.0) > UNIT_EPS:
        va = va * factor

    both = np.isfinite(va) & np.isfinite(vb)
    diff = np.full(va.shape, np.nan, dtype="float32")
    diff[both] = (va[both] - vb[both]).astype("float32")

    mask_a = view_a["mask"].astype(bool)
    mask_b = view_b["mask"].astype(bool)
    stats = None
    if both.any():
        d = diff[both]
        stats = {
            "count": int(both.sum()),
            "min": float(d.min()),
            "max": float(d.max()),
            "mean": float(d.mean()),
            "abs_mean": float(np.abs(d).mean()),
            "rmse": float(np.sqrt(np.mean(d.astype("float64") ** 2))),
            "std": float(d.std()),
        }

    return {
        "values": diff,
        "mask_both": both.astype("uint8"),
        "mask_a": mask_a.astype("uint8"),
        "mask_b": mask_b.astype("uint8"),
        "only_a": (mask_a & ~mask_b).astype("uint8"),
        "only_b": (mask_b & ~mask_a).astype("uint8"),
        "stats": stats,
        "unit": comparability["difference_unit"],
        "order": "A - B",
    }


def _crs_from_meta(meta: dict[str, Any]) -> CRS:
    c = meta["crs"]
    if c.get("epsg"):
        return CRS.from_epsg(c["epsg"])
    # Fall back through proj4 stored in metadata (rare custom CRS).
    return CRS.from_proj4(c["proj4"])
