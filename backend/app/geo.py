import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform, transform_bounds, transform_geom
from shapely.geometry import box, mapping, shape
from shapely.geometry.base import BaseGeometry

from .models import BandInfo, RasterMetadata

WGS84 = CRS.from_epsg(4326)
MAX_GRID_SIDE = 2048


def _densify_polygon_coords(coords: list, step: int = 64) -> list:
    dense: list = []
    for ring in coords:
        new_ring = []
        for p0, p1 in zip(ring[:-1], ring[1:]):
            new_ring.append(p0)
            for k in range(1, step):
                t = k / step
                new_ring.append([
                    p0[0] + (p1[0] - p0[0]) * t,
                    p0[1] + (p1[1] - p0[1]) * t,
                ])
        new_ring.append(ring[-1])
        dense.append(new_ring)
    return dense


def transform_densified_bounds(src_crs, dst_crs: CRS, bounds: tuple[float, float, float, float],
                               step: int = 64) -> dict:
    left, bottom, right, top = bounds
    polygon = box(left, bottom, right, top)
    geojson = mapping(polygon)
    geojson["coordinates"] = _densify_polygon_coords(geojson["coordinates"], step)
    return transform_geom(src_crs, dst_crs, geojson)

UnitCompatibility = tuple[bool, str | None, str, str]


@dataclass(frozen=True)
class RasterEntry:
    file_id: str
    slot: Literal["a", "b"]
    path: str
    filename: str
    size_bytes: int
    opened_at: float
    metadata: RasterMetadata


class RasterInputError(ValueError):
    pass


def scalar_for_json(value: Any) -> float | int | str | None:
    if value is None:
        return None
    arr = np.asarray(value)
    if arr.size != 1:
        return None
    item = arr.reshape(()).item()
    if isinstance(item, (int, np.integer)):
        return int(item)
    if isinstance(item, (float, np.floating)):
        if math.isnan(float(item)):
            return "NaN"
        if math.isinf(float(item)):
            return "Infinity" if item > 0 else "-Infinity"
        return float(item)
    return str(item)


def _unit_aliases(unit: str | None) -> str:
    u = (unit or "").strip().lower().replace("_", " ")
    aliases = {
        "": "",
        "m": "m",
        "meter": "m",
        "meters": "m",
        "metre": "m",
        "metres": "m",
        "ft": "ft",
        "feet": "ft",
        "foot": "ft",
        "degree": "deg",
        "degrees": "deg",
        "deg": "deg",
    }
    return aliases.get(u, u)


def compare_units(a: str | None, b: str | None) -> UnitCompatibility:
    aa = _unit_aliases(a)
    bb = _unit_aliases(b)
    raw_a = (a or "").strip()
    raw_b = (b or "").strip()
    if not aa and not bb:
        return False, "两份波段都没有单位元数据，无法确认数值处于同一尺度", raw_a, raw_b
    if not aa or not bb:
        return False, f"单位不一致或缺失：A='{raw_a or '未声明'}'，B='{raw_b or '未声明'}'", raw_a, raw_b
    if aa != bb:
        return False, f"单位不一致：A='{raw_a}'，B='{raw_b}'；本工具不自动换算单位", raw_a, raw_b
    return True, None, raw_a, raw_b


def _valid_number(value: Any) -> bool:
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number)


def read_metadata(file_id: str, slot: Literal["a", "b"], path: str, filename: str,
                  size_bytes: int, opened_at: float) -> RasterMetadata:
    try:
        ds = rasterio.open(path)
    except Exception as exc:  # rasterio raises a mix of RasterioError/NotGeoreferencedError
        raise RasterInputError(f"无法作为 GeoTIFF/栅格文件打开：{exc}") from exc

    with ds:
        if ds.crs is None:
            raise RasterInputError("文件缺少坐标参考（CRS），不能进行地理位置对照")
        if ds.width <= 0 or ds.height <= 0 or ds.count <= 0:
            raise RasterInputError("文件没有有效的行列或波段")
        if ds.transform is None or not _valid_number(ds.transform.a) or ds.transform.a == 0:
            raise RasterInputError("文件缺少有效的仿射变换，不能定位像元")

        bands: list[BandInfo] = []
        for i in range(1, ds.count + 1):
            dtype = ds.dtypes[i - 1]
            if np.issubdtype(np.dtype(dtype), np.complexfloating):
                raise RasterInputError(f"第 {i} 波段是复数类型，本工具只比较数值型栅格")

            scales = ds.scales
            offsets = ds.offsets
            scale_raw = scales[i - 1] if isinstance(scales, (tuple, list)) else scales(i)
            offset_raw = offsets[i - 1] if isinstance(offsets, (tuple, list)) else offsets(i)
            nodata = scalar_for_json(ds.nodatavals[i - 1])
            block_height, block_width = ds.block_shapes[i - 1]
            descriptions = ds.descriptions or ()
            description = descriptions[i - 1] if i - 1 < len(descriptions) else None
            units_values = ds.units
            units = units_values[i - 1] if isinstance(units_values, (tuple, list)) else (
                units_values(i) if callable(units_values) else ""
            )
            scale = float(scale_raw) if _valid_number(scale_raw) else 1.0
            offset = float(offset_raw) if _valid_number(offset_raw) else 0.0
            if scale == 0.0:
                raise RasterInputError(f"第 {i} 波段 scale=0，不能还原标定值")

            bands.append(BandInfo(
                index=i,
                description=description if description else None,
                dtype=dtype,
                units=units or "",
                scale=scale,
                offset=offset,
                nodata=nodata,
                block_width=int(block_width),
                block_height=int(block_height),
            ))

        native_bounds = tuple(float(x) for x in ds.bounds)
        try:
            wgs_bounds = transform_bounds(ds.crs, WGS84, *native_bounds, densify_pts=64)
            wgs_bounds = tuple(float(x) for x in wgs_bounds)
        except Exception as exc:
            raise RasterInputError(f"无法把文件范围转换到 WGS84：{exc}") from exc

        crs = ds.crs
        crs_text = crs.to_string()
        crs_name = None
        try:
            epsg = crs.to_epsg()
            if epsg:
                crs_name = f"EPSG:{epsg}"
        except Exception:
            pass
        if not crs_name:
            crs_name = crs.name or crs_text

        tags = {}
        try:
            for key, value in ds.tags().items():
                if value is not None:
                    tags[str(key)] = str(value)[:500]
        except Exception:
            tags = {}

        return RasterMetadata(
            file_id=file_id,
            slot=slot,
            filename=filename,
            size_bytes=size_bytes,
            opened_at=opened_at,
            driver=ds.driver,
            width=ds.width,
            height=ds.height,
            band_count=ds.count,
            crs=crs_text,
            crs_name=crs_name,
            projected=bool(getattr(crs, "is_projected", False)),
            geographic=bool(getattr(crs, "is_geographic", False)),
            bounds_native=native_bounds,  # type: ignore[arg-type]
            bounds_wgs84=wgs_bounds,  # type: ignore[arg-type]
            transform=[float(x) for x in ds.transform],
            bands=bands,
            tags=tags,
        )


def validate_band(metadata: RasterMetadata, band: int) -> BandInfo:
    for item in metadata.bands:
        if item.index == band:
            return item
    raise RasterInputError(f"波段编号 {band} 不存在；该文件有 {metadata.band_count} 个波段")


def _geometry_wgs84(metadata: RasterMetadata) -> BaseGeometry:
    # Reproject/densify the native footprint; transform_bounds only returns an
    # axis-aligned containing rectangle, which is not a true common coverage.
    projected = transform_densified_bounds(metadata.crs, WGS84, metadata.bounds_native, step=128)
    geom = shape(projected)
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom


def bounds_geometry_in_crs(metadata: RasterMetadata, dst_crs: CRS) -> BaseGeometry:
    if CRS.from_user_input(metadata.crs) == dst_crs:
        left, bottom, right, top = metadata.bounds_native
        return box(left, bottom, right, top)
    projected = transform_densified_bounds(metadata.crs, dst_crs, metadata.bounds_native, step=64)
    geom = shape(projected)
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom


def wgs84_bounds_to_crs_geometry(bounds: tuple[float, float, float, float], dst_crs: CRS) -> BaseGeometry:
    polygon = box(*bounds)
    if dst_crs == WGS84:
        return polygon
    projected = transform_densified_bounds(WGS84, dst_crs, bounds, step=64)
    geom = shape(projected)
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom


def common_coverage(a: RasterMetadata | None, b: RasterMetadata | None) -> dict[str, Any]:
    if a is None or b is None:
        missing = "A" if a is None else "B"
        return {"comparable": False, "reason": f"文件 {missing} 尚未成功打开", "warnings": []}

    geom_a = _geometry_wgs84(a)
    geom_b = _geometry_wgs84(b)
    if not geom_a.intersects(geom_b):
        return {"comparable": False, "reason": "两个文件声明的地理范围没有交集", "warnings": []}
    intersection = geom_a.intersection(geom_b)
    if intersection.is_empty or intersection.area <= 1e-14:
        return {"comparable": False, "reason": "共同覆盖面积为零", "warnings": []}

    minx, miny, maxx, maxy = intersection.bounds
    if not all(math.isfinite(x) for x in (minx, miny, maxx, maxy)) or maxx <= minx or maxy <= miny:
        return {"comparable": False, "reason": "共同覆盖范围无效", "warnings": []}

    return {
        "comparable": True,
        "reason": None,
        "common_bounds_wgs84": [minx, miny, maxx, maxy],
        "intersection_type": intersection.geom_type,
        "warnings": [],
    }


def comparison_info(a: RasterMetadata | None, b: RasterMetadata | None,
                    band_a: int | None, band_b: int | None) -> dict[str, Any]:
    info = common_coverage(a, b)
    info.update({
        "band_a": band_a,
        "band_b": band_b,
        "unit_a": None,
        "unit_b": None,
        "normalized_unit": None,
        "difference_label": "B - A",
    })

    if a is not None and band_a is not None:
        try:
            info["unit_a"] = validate_band(a, band_a).units
        except RasterInputError as exc:
            info["comparable"] = False
            info["reason"] = str(exc)
    if b is not None and band_b is not None:
        try:
            info["unit_b"] = validate_band(b, band_b).units
        except RasterInputError as exc:
            info["comparable"] = False
            info["reason"] = str(exc)

    if a is None or b is None or band_a is None or band_b is None:
        info["comparable"] = False
        info["reason"] = info.get("reason") or "请先选择两份文件中参与比较的波段"
        return info

    try:
        ba = validate_band(a, band_a)
        bb = validate_band(b, band_b)
    except RasterInputError as exc:
        info["comparable"] = False
        info["reason"] = str(exc)
        return info

    units_ok, unit_reason, unit_a, unit_b = compare_units(ba.units, bb.units)
    info["unit_a"] = unit_a
    info["unit_b"] = unit_b
    if not units_ok:
        info["comparable"] = False
        info["reason"] = unit_reason
        return info

    info["normalized_unit"] = _unit_aliases(unit_a)
    info["difference_units"] = _unit_aliases(unit_a)
    return info


def query_point(entry: RasterEntry | None, band: int, lon: float, lat: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "file_id": None,
        "filename": None,
        "band": band,
        "available": False,
        "reason": None,
        "crs": None,
        "native_x": None,
        "native_y": None,
        "row": None,
        "col": None,
        "row_float": None,
        "col_float": None,
        "inside_raster": False,
        "raw_value": None,
        "scaled_value": None,
        "is_nodata": None,
        "nodata": None,
        "scale": None,
        "offset": None,
        "units": None,
    }
    if entry is None:
        result["reason"] = "该位置没有已成功打开的文件"
        return result

    result.update(file_id=entry.file_id, filename=entry.filename, crs=entry.metadata.crs)
    try:
        band_info = validate_band(entry.metadata, band)
        result.update(nodata=band_info.nodata, scale=band_info.scale,
                      offset=band_info.offset, units=band_info.units)

        with rasterio.open(entry.path) as ds:
            xs, ys = transform(WGS84, ds.crs, [lon], [lat])
            native_x, native_y = float(xs[0]), float(ys[0])
            result["native_x"] = native_x
            result["native_y"] = native_y
            if not (math.isfinite(native_x) and math.isfinite(native_y)):
                result["reason"] = "坐标无法转换到该文件的 CRS"
                return result

            row_float, col_float = ds.index(native_x, native_y, op=float)
            row_float, col_float = float(row_float), float(col_float)
            result["row_float"] = row_float
            result["col_float"] = col_float
            row = int(math.floor(row_float))
            col = int(math.floor(col_float))
            result["row"] = row
            result["col"] = col

            if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
                result["reason"] = "投影位置在原始栅格范围之外"
                return result
            result["inside_raster"] = True
            result["available"] = True

            raw = ds.read(band, window=((row, row + 1), (col, col + 1)),
                          boundless=False, fill_value=None)
            mask = ds.read_masks(band, window=((row, row + 1), (col, col + 1)),
                                 boundless=False)
            value = raw[0, 0]
            scaled_value = float(value) * band_info.scale + band_info.offset
            is_nodata = bool(mask[0, 0] == 0)
            if band_info.nodata is not None and not isinstance(band_info.nodata, str):
                try:
                    is_nodata = is_nodata or float(value) == float(band_info.nodata)
                except (TypeError, ValueError):
                    pass
            result["raw_value"] = scalar_for_json(value)
            result["scaled_value"] = None if is_nodata else float(scaled_value)
            result["is_nodata"] = is_nodata
            if is_nodata:
                result["reason"] = "该原始像元是无数据（nodata/mask）"
            return result
    except Exception as exc:
        result["available"] = False
        result["reason"] = f"读取该位置失败：{exc}"
        return result


def _finite_stats(values: np.ndarray) -> dict[str, Any]:
    finite = np.isfinite(values)
    valid = int(finite.sum())
    stats: dict[str, Any] = {"valid_pixels": valid, "total_pixels": int(values.size),
                             "min": None, "max": None, "p02": None, "p98": None}
    if valid:
        good = values[finite]
        stats["min"] = float(good.min())
        stats["max"] = float(good.max())
        if valid > 1:
            p02, p98 = np.percentile(good, [2, 98])
            stats["p02"] = float(p02)
            stats["p98"] = float(p98)
        else:
            stats["p02"] = stats["min"]
            stats["p98"] = stats["max"]
    return stats


def read_view_grid(entry: RasterEntry, band: int,
                   bounds: tuple[float, float, float, float],
                   width: int, height: int) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Read one calibrated, geographically reprojected float32 grid for the view.

    The source is opened lazily and WarpedVRT lets GDAL use overviews/windowed IO.
    Scale/offset and nodata are applied after georeferenced bilinear display
    resampling; no color values are involved.
    """
    side: dict[str, Any] = {
        "file_id": entry.file_id,
        "filename": entry.filename,
        "band": band,
        "available": False,
        "reason": None,
        "crs": entry.metadata.crs,
        "resampling": "bilinear",
        "source_resampling_note": "用于显示/差值；原始读数请使用选点接口",
    }
    if not (64 <= width <= MAX_GRID_SIDE and 64 <= height <= MAX_GRID_SIDE):
        side["reason"] = f"请求网格边长必须在 64 到 {MAX_GRID_SIDE} 之间"
        return None, side

    west, south, east, north = bounds
    if not all(math.isfinite(v) for v in bounds) or east <= west or north <= south:
        side["reason"] = "视图范围无效"
        return None, side

    try:
        band_info = validate_band(entry.metadata, band)
        target_polygon = wgs84_bounds_to_crs_geometry(bounds, CRS.from_user_input(entry.metadata.crs))
        source_polygon = bounds_geometry_in_crs(entry.metadata, CRS.from_user_input(entry.metadata.crs))
        if not target_polygon.intersects(source_polygon):
            side["reason"] = "当前视图不在此文件覆盖范围内"
            return None, side

        dst_transform = from_bounds(west, south, east, north, width, height)
        with rasterio.open(entry.path) as src:
            vrt_options = dict(
                crs=WGS84,
                width=width,
                height=height,
                transform=dst_transform,
                dtype="float32",
                nodata=np.float32(np.nan),
                warp_mem_limit=128,
                num_threads=2,
            )
            window = rasterio.windows.Window(0, 0, width, height)
            with WarpedVRT(src, resampling=Resampling.bilinear, **vrt_options) as values_vrt:
                raw = values_vrt.read(band, window=window)
            # Build the validity mask independently with nearest resampling.
            # Bilinear interpolation around holes can otherwise paint values into
            # target pixels whose nearest source cell is nodata/no coverage.
            with WarpedVRT(src, resampling=Resampling.nearest, **vrt_options) as mask_vrt:
                mask = mask_vrt.read_masks(band, window=window)

        values = raw.astype(np.float32, copy=True)
        values[mask == 0] = np.nan
        values[~np.isfinite(values)] = np.nan
        values = values * np.float32(band_info.scale) + np.float32(band_info.offset)
        if band_info.nodata == "NaN":
            values[~np.isfinite(values)] = np.nan
        side["available"] = True
        side["units"] = band_info.units
        side["stats"] = _finite_stats(values)
        return values, side
    except Exception as exc:
        side["reason"] = f"视图读取失败：{exc}"
        return None, side


def diff_grids(a: np.ndarray | None, b: np.ndarray | None,
               comparable: bool, reason: str | None) -> tuple[np.ndarray | None, dict[str, Any]]:
    info: dict[str, Any] = {"available": False, "reason": None, "label": "B - A", "stats": None}
    if not comparable:
        info["reason"] = reason or "当前波段组合不能进行数值比较"
        return None, info
    if a is None or b is None:
        info["reason"] = "两份栅格在当前视图中都成功读取后才计算差值"
        return None, info

    diff = (b - a).astype(np.float32)
    # Missing data is a mask, not zero or an interpolated value.
    diff[~(np.isfinite(a) & np.isfinite(b))] = np.nan
    info["available"] = True
    info["stats"] = _finite_stats(diff)
    if info["stats"]["valid_pixels"] == 0:
        info["available"] = False
        info["reason"] = "共同覆盖区内没有任何双方都有效的重叠像元"
        return None, info
    return diff, info


# This tiny context helper avoids importing contextlib solely in one path above.
from contextlib import nullcontext  # noqa: E402
