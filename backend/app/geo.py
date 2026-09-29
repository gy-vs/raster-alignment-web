"""Geographic helpers: CRS normalisation, unit handling, overlap geometry.

Everything here uses rasterio (GDAL) only - no system gdal binaries.
"""
from __future__ import annotations

import rasterio.warp
from rasterio.crs import CRS
from shapely.geometry import box, shape
from shapely.geometry.base import BaseGeometry

# Angles are never a valid *vertical* unit. Everything else we treat as a
# linear unit and convert through the CRS definition (fallback: metre).
_ANGLE_UNITS = {
    "degree", "degrees", "deg", "dd", "radian", "radians", "rad",
    "arcdegree", "arc_degree",
}

# Common aliases GDAL reports, mapped to the canonical name we compare on.
_UNIT_ALIASES = {
    "metre": "m", "meter": "m", "metres": "m", "meters": "m", "m": "m",
    "foot": "ft", "feet": "ft", "ft": "ft",
    "us survey foot": "us-ft", "u.s. foot": "us-ft",
    "us_survey_foot": "us-ft", "foot_us": "us-ft",
    "us-ft": "us-ft",
    "international foot": "ft",
    "kilometre": "km", "kilometer": "km", "km": "km",
}


def normalize_unit(raw: str | None) -> str | None:
    if raw is None:
        return None
    u = str(raw).strip().lower()
    if not u:
        return None
    return _UNIT_ALIASES.get(u, u)


def unit_to_metres(unit: str | None, crs: CRS | None) -> float | None:
    """Return the factor that multiplies a value in ``unit`` to metres.

    Angle units are rejected (None). Unknown linear units fall back to the
    CRS linear unit; if that is unknown too we refuse the conversion.
    """
    if unit is not None and unit in _ANGLE_UNITS:
        return None
    if unit == "m":
        return 1.0
    if unit == "km":
        return 1000.0
    if unit == "ft":
        return 0.3048
    if unit == "us-ft":
        return 1200.0 / 3937.0  # US survey foot, exact
    # Ask the CRS: useful for uncommon projected units.
    if crs is not None and not crs.is_geographic:
        try:
            factor = float(crs.linear_units_factor[1])
            return factor
        except Exception:
            return None
    return None


def bounds_polygon(
    bounds: rasterio.coords.BoundingBox | tuple[float, float, float, float],
    crs: CRS,
) -> BaseGeometry:
    return box(bounds[0], bounds[1], bounds[2], bounds[3])


def reproject_geometry(
    geom: BaseGeometry, src_crs: CRS, dst_crs: CRS
) -> BaseGeometry:
    if src_crs == dst_crs:
        return geom
    mapped = rasterio.warp.transform_geom(
        src_crs, dst_crs, geom.__geo_interface__
    )
    return shape(mapped)


def intersects(
    bounds_a: tuple[float, float, float, float],
    crs_a: CRS,
    bounds_b: tuple[float, float, float, float],
    crs_b: CRS,
) -> bool:
    """True if two CRS-aware bounding boxes overlap (WGS84 common frame)."""
    WGS84 = CRS.from_epsg(4326)
    ga = reproject_geometry(bounds_polygon(bounds_a, crs_a), crs_a, WGS84)
    gb = reproject_geometry(bounds_polygon(bounds_b, crs_b), crs_b, WGS84)
    return ga.intersects(gb)


def intersection_bounds_wgs84(
    bounds_a: tuple[float, float, float, float],
    crs_a: CRS,
    bounds_b: tuple[float, float, float, float],
    crs_b: CRS,
) -> tuple[float, float, float, float] | None:
    """Intersection of two raster extents, reported in WGS84."""
    WGS84 = CRS.from_epsg(4326)
    ga = reproject_geometry(bounds_polygon(bounds_a, crs_a), crs_a, WGS84)
    gb = reproject_geometry(bounds_polygon(bounds_b, crs_b), crs_b, WGS84)
    inter = ga.intersection(gb)
    if inter.is_empty:
        return None
    return inter.bounds  # (minx, miny, maxx, maxy)


def reproject_bounds(
    bounds: tuple[float, float, float, float],
    src_crs: CRS,
    dst_crs: CRS,
) -> tuple[float, float, float, float]:
    g = reproject_geometry(bounds_polygon(bounds, src_crs), src_crs, dst_crs)
    return g.bounds


def describe_crs(crs: CRS) -> dict:
    epsg = None
    try:
        epsg = crs.to_epsg()
    except Exception:
        epsg = None
    kind = "geographic" if crs.is_geographic else (
        "projected" if crs.is_projected else "unknown"
    )
    linear = None
    linear_factor = None
    if not crs.is_geographic:
        try:
            linear = crs.linear_units
        except Exception:
            linear = None
        try:
            linear_factor = float(crs.linear_units_factor[1])
        except Exception:
            linear_factor = None
    name = None
    try:
        name = crs.to_dict().get("name") or crs.get("Name")
    except Exception:
        pass
    return {
        "epsg": epsg,
        "kind": kind,
        "name": name,
        "proj4": crs.to_proj4(),
        "wkt_preview": crs.to_wkt()[:400],
        "linear_units": linear,
        "linear_units_factor": linear_factor,
    }
