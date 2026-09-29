"""Generate a small, repeatable pair of GeoTIFF elevation samples.

The two files describe THE SAME analytic terrain near 11.0 E / 46.0 N but
differ on purpose in every storage respect the viewer must handle:

  sample_a.tif  EPSG:32632 (UTM 32N), 30 m cells, Int16 stored values with
                scale=0.1/offset=0 (stored = metres*10), nodata=-32768,
                one band, a circular nodata hole, overviews built.

  sample_b.tif  EPSG:32633 (UTM 33N), 25 m cells, slightly larger/shifted
                extent, Float32, NaN nodata, TWO bands:
                  band 1 = elevation in metres ("m")
                  band 2 = same elevation in feet ("ft")  -> unit conversion
                a square nodata hole at a different place, overviews built.

Ground truth (continuous, evaluated at any lon/lat):

  u = (lon - 11.00) / 0.02
  v = (lat - 46.00) / 0.02
  terrain = 800 + 250*sin(2*pi*1.5*u)*cos(2*pi*v) + 100*u + 60*v
  delta   = 3*u - 2*v + 1.5*sin(2*pi*u)
  A(lon,lat) = terrain
  B(lon,lat) = terrain + delta          (so A - B = -delta)

Run:  .venv/bin/python scripts/generate_samples.py
Outputs files into ./samples plus samples_manifest.json with expected
readings at three test points (one valid both, one A-hole, one B-hole).
"""
from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds, transform as warp_transform

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "samples")
LON0, LAT0, SPAN = 11.00, 46.00, 0.02
A_SCALE = 0.1
A_NODATA = -32768


def terrain(lon, lat):
    u = (lon - LON0) / SPAN
    v = (lat - LAT0) / SPAN
    return 800.0 + 250.0 * math.sin(2 * math.pi * 1.5 * u) \
        * math.cos(2 * math.pi * v) + 100.0 * u + 60.0 * v


def delta(lon, lat):
    u = (lon - LON0) / SPAN
    v = (lat - LAT0) / SPAN
    return 3.0 * u - 2.0 * v + 1.5 * math.sin(2 * math.pi * u)


def lonlat_grid(xs, ys, crs):
    xgrid, ygrid = np.meshgrid(xs, ys)
    lon, lat = warp_transform(crs, "EPSG:4326",
                              xgrid.reshape(-1), ygrid.reshape(-1))
    return np.array(lon), np.array(lat)


def build_a():
    # lon/lat window -> UTM 32N metres
    w, e = 10.990, 11.030
    s, n = 45.990, 46.030
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", "EPSG:32632", w, s, e, n, densify_pts=41
    )
    res = 30.0
    width = int(math.floor((right - left) / res))
    height = int(math.floor((top - bottom) / res))
    transform = from_origin(left, top, res, res)
    xs = left + (np.arange(width) + 0.5) * res
    ys = top - (np.arange(height) + 0.5) * res
    lon, lat = lonlat_grid(xs, ys, "EPSG:32632")

    vals = np.array([terrain(lo, la) for lo, la in zip(lon, lat)],
                    dtype="float64").reshape(height, width)
    # circular hole around (11.010, 46.010), radius 160 m
    cx, cy = warp_transform("EPSG:4326", "EPSG:32632",
                            [11.010], [46.010])
    xg, yg = np.meshgrid(xs, ys)
    hole = (xg - cx[0]) ** 2 + (yg - cy[0]) ** 2 < 160.0 ** 2

    stored = np.rint(vals / A_SCALE).astype("int16")
    stored[hole] = A_NODATA

    path = os.path.join(OUT_DIR, "sample_a.tif")
    profile = {
        "driver": "GTiff", "width": width, "height": height,
        "count": 1, "dtype": "int16", "crs": "EPSG:32632",
        "transform": transform, "nodata": A_NODATA,
        "tiled": True, "blockxsize": 128, "blockysize": 128,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(stored, 1)
        dst.scales = (A_SCALE,)
        dst.offsets = (0.0,)
        dst.set_band_description(1, "elevation (stored int16 x10)")
        dst.update_tags(1, units="m")
        dst.build_overviews([2, 4], Resampling.average)
        dst.update_tags(ns="terrain", formula="terrain(lon,lat)")
    return path, {"width": width, "height": height,
                  "utm_bounds": [left, bottom, right, top]}


def build_b():
    # slightly larger / shifted window, different zone, 25 m cells
    w, e = 10.985, 11.035
    s, n = 45.985, 46.035
    left, bottom, right, top = transform_bounds(
        "EPSG:4326", "EPSG:32633", w, s, e, n, densify_pts=41
    )
    res = 25.0
    width = int(math.floor((right - left) / res))
    height = int(math.floor((top - bottom) / res))
    transform = from_origin(left, top, res, res)
    xs = left + (np.arange(width) + 0.5) * res
    ys = top - (np.arange(height) + 0.5) * res
    lon, lat = lonlat_grid(xs, ys, "EPSG:32633")

    elev = np.array(
        [terrain(lo, la) + delta(lo, la) for lo, la in zip(lon, lat)],
        dtype="float64",
    ).reshape(height, width)

    # square hole (200 m side) around (11.006, 46.016)
    cx, cy = warp_transform("EPSG:4326", "EPSG:32633",
                            [11.006], [46.016])
    xg, yg = np.meshgrid(xs, ys)
    hole = (np.abs(xg - cx[0]) < 100.0) & (np.abs(yg - cy[0]) < 100.0)
    elev[hole] = np.nan

    feet = (elev / 0.3048).astype("float32")

    path = os.path.join(OUT_DIR, "sample_b.tif")
    profile = {
        "driver": "GTiff", "width": width, "height": height,
        "count": 2, "dtype": "float32", "crs": "EPSG:32633",
        "transform": transform, "nodata": float("nan"),
        "tiled": True, "blockxsize": 128, "blockysize": 128,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(elev.astype("float32"), 1)
        dst.write(feet, 2)
        dst.set_band_description(1, "elevation metres")
        dst.set_band_description(2, "elevation feet")
        dst.update_tags(1, units="m")
        dst.update_tags(2, units="ft")
        dst.build_overviews([2, 4], Resampling.average)
    return path, {"width": width, "height": height,
                  "utm_bounds": [left, bottom, right, top]}


def expected_point(name, lon, lat):
    return {
        "name": name,
        "lon": lon,
        "lat": lat,
        "terrain": terrain(lon, lat),
        "A_expected": terrain(lon, lat),
        "B_expected_m": terrain(lon, lat) + delta(lon, lat),
        "A_minus_B_expected": -delta(lon, lat),
        "note": "cell-center values may differ by at most one rasterisation "
                "step; the API point reading is from the actual containing "
                "cell - manifest truth is analytic.",
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    pa, info_a = build_a()
    pb, info_b = build_b()

    manifest = {
        "terrain": "800 + 250*sin(2*pi*1.5*u)*cos(2*pi*v) + 100*u + 60*v",
        "u": "(lon-11.00)/0.02",
        "v": "(lat-46.00)/0.02",
        "relation": "B = terrain + (3u - 2v + 1.5 sin(2*pi*u)); A - B = -delta",
        "files": {
            "sample_a.tif": {
                "crs": "EPSG:32632", "resolution_m": 30,
                "dtype": "int16", "scale": A_SCALE, "offset": 0.0,
                "nodata": A_NODATA, "bands": 1, "unit": "m",
                **info_a,
            },
            "sample_b.tif": {
                "crs": "EPSG:32633", "resolution_m": 25,
                "dtype": "float32", "nodata": "NaN",
                "bands": [
                    {"index": 1, "unit": "m", "quantity": "elevation m"},
                    {"index": 2, "unit": "ft", "quantity": "elevation ft"},
                ],
                **info_b,
            },
        },
        "test_points": [
            expected_point("valid_both", 11.012, 46.012),
            expected_point("a_nodata_hole", 11.010, 46.010),
            expected_point("b_nodata_hole", 11.006, 46.016),
        ],
    }
    mp = os.path.join(OUT_DIR, "samples_manifest.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("wrote", os.path.relpath(pa), info_a)
    print("wrote", os.path.relpath(pb), info_b)
    print("wrote", os.path.relpath(mp))
    for p in manifest["test_points"]:
        print(f"  {p['name']:15s} ({p['lon']},{p['lat']}) "
              f"A={p['A_expected']:.3f} B={p['B_expected_m']:.3f} "
              f"A-B={p['A_minus_B_expected']:.3f}")


if __name__ == "__main__":
    main()
