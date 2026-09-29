#!/usr/bin/env python3
"""Generate two small, numerically known elevation GeoTIFFs.

The pair deliberately differs in CRS, cell size, storage type, scale/offset and
no-data layout. Both files are normal GeoTIFFs and are meant to be uploaded
through the same API as user data.

Known physical values (before storage encoding):
  z_a(lon, lat) = 200 + 4000*(lon-10.05) + 3000*(lat-49.95)
  z_b(lon, lat) = z_a(lon, lat) + 2.5

A stores calibrated(z) = raw * 0.1 + 50 in Int16 with an explicit nodata value.
B stores calibrated(z) directly in Float32 with a NaN/masked hole.
"""
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "samples"

WEST, EAST = 10.050_000, 10.054_000
SOUTH, NORTH = 49.950_000, 49.953_000
DX = 0.000_100  # about 7.15 m at this latitude in WGS84
DY = 0.000_100  # about 11.1 m


def z_a(lon: np.ndarray | float, lat: np.ndarray | float) -> np.ndarray | float:
    return 200.0 + 4000.0 * (lon - 10.05) + 3000.0 * (lat - 49.95)


def z_b(lon: np.ndarray | float, lat: np.ndarray | float) -> np.ndarray | float:
    return z_a(lon, lat) + 2.5


def make_wgs84() -> dict:
    width = int(round((EAST - WEST) / DX))
    height = int(round((NORTH - SOUTH) / DY))
    transform_ = Affine(DX, 0.0, WEST, 0.0, -DY, NORTH)
    lon = WEST + (np.arange(width) + 0.5) * DX
    lat = NORTH - (np.arange(height) + 0.5) * DY
    grid_lon, grid_lat = np.meshgrid(lon, lat)
    values = z_b(grid_lon, grid_lat).astype("float32")

    # A small interior missing-data region, not an interpolated zero value.
    values[7:10, 15:18] = np.nan

    path = SAMPLE_DIR / "sample_b_wgs84.tif"
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 1,
        "dtype": "float32",
        "crs": CRS.from_epsg(4326),
        "transform": transform_,
        "nodata": np.float32(np.nan),
        "tiled": True,
        "blockxsize": 16,
        "blockysize": 16,
        "compress": "lzw",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(values, 1)
        dst.set_band_description(1, "Known elevation B, WGS84")
        dst.update_tags(units="m", sample="true")
        dst.set_band_unit(1, "m")

    # Center lon/lat 10.052 / 49.9515 is column 20? row 15? Record exact source
    # cells to make manual checks straightforward.
    checks = []
    for col, row in [(20, 15), (10, 10), (25, 20)]:
        cell_lon = WEST + (col + 0.5) * DX
        cell_lat = NORTH - (row + 0.5) * DY
        checks.append({
            "side": "B", "row": row, "col": col,
            "lon": cell_lon, "lat": cell_lat,
            "value_b": float(values[row, col]) if np.isfinite(values[row, col]) else None,
        })
    return {"path": str(path.relative_to(ROOT)), "width": width, "height": height,
            "crs": "EPSG:4326", "checks": checks}


def make_utm() -> dict:
    # Build a slightly larger UTM raster so the intersection is asymmetric.
    # Use 5 m cells and snap an interior cell center to the known WGS84 test point.
    crs = CRS.from_epsg(32632)
    anchor_lon, anchor_lat = 10.052_05, 49.951_55
    (anchor_x,), (anchor_y,) = transform(CRS.from_epsg(4326), crs, [anchor_lon], [anchor_lat])

    # Anchor this coordinate at the center of col=32, row=24 on a 5 m grid.
    col0, row0 = 32, 24
    left = anchor_x - (col0 + 0.5) * 5.0
    top = anchor_y + (row0 + 0.5) * 5.0
    width, height = 70, 55
    transform_ = Affine(5.0, 0.0, left, 0.0, -5.0, top)

    xs = left + (np.arange(width) + 0.5) * 5.0
    ys = top - (np.arange(height) + 0.5) * 5.0
    grid_x, grid_y = np.meshgrid(xs, ys)
    lon, lat = transform(crs, CRS.from_epsg(4326), grid_x.ravel(), grid_y.ravel())
    lon = np.asarray(lon).reshape(grid_x.shape)
    lat = np.asarray(lat).reshape(grid_y.shape)
    calibrated = z_a(lon, lat)

    # calibrated = raw * 0.1 + 50 -> raw = (calibrated - 50) / 0.1
    raw = np.rint((calibrated - 50.0) / 0.1).astype("int16")
    nodata = np.int16(-9999)

    # Explicit sentinel missing region, partly overlapping B's NaN region and
    # partly not. This lets the UI distinguish A-only, B-only and both-missing.
    raw[6:9, 38:41] = nodata

    path = SAMPLE_DIR / "sample_a_utm.tif"
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 1,
        "dtype": "int16",
        "crs": crs,
        "transform": transform_,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 32,
        "blockysize": 32,
        "compress": "lzw",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(raw, 1)
        dst.set_band_description(1, "Known elevation A, UTM, scale/offset encoded")
        dst.update_tags(units="m", sample="true")
        dst.set_band_unit(1, "m")

    # Explicit GeoTIFF scale and offset tags are not reliably exposed through
    # ordinary Rasterio profile fields; GDAL metadata keys are used here.
    with rasterio.open(path, "r+") as dst:
        dst.update_tags(1, STATISTICS_NOTE="display/value calibration is scale=0.1 offset=50")
        dst._set_all_scales([0.1])  # type: ignore[attr-defined]
        dst._set_all_offsets([50.0])  # type: ignore[attr-defined]
        dst.set_band_unit(1, "m")

    checks = [
        {"side": "A", "row": row0, "col": col0, "lon": anchor_lon, "lat": anchor_lat,
         "raw_a": int(raw[row0, col0]), "value_a": float(calibrated[row0, col0]),
         "scale": 0.1, "offset": 50.0},
    ]
    return {"path": str(path.relative_to(ROOT)), "width": width, "height": height,
            "crs": "EPSG:32632", "anchor_x": anchor_x, "anchor_y": anchor_y, "checks": checks}


def main() -> None:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    a = make_utm()
    b = make_wgs84()
    readme = SAMPLE_DIR / "README.md"
    readme.write_text(
        "# Small known-value samples\n\n"
        "Run `python3 scripts/make_samples.py` from the project root to regenerate these files.\n\n"
        "- `sample_a_utm.tif`: EPSG:32632, 5 m cells, Int16, nodata=-9999, scale=0.1, offset=50.\n"
        "- `sample_b_wgs84.tif`: EPSG:4326, about 0.0001° cells, Float32, NaN/mask nodata.\n"
        "- The physical model is `z_a=200+4000*(lon-10.05)+3000*(lat-49.95)` metres and "
        "`z_b=z_a+2.5` metres where both rasters are valid.\n"
        "- Interior no-data regions intentionally overlap only partly.\n"
        "- Load each file in the browser with the sample buttons; it goes through `/api/upload` "
        "and the normal view/point calculations.\n\n"
        f"Generation metadata: A={a}\nB={b}\n",
        encoding="utf-8",
    )
    print(f"wrote {SAMPLE_DIR / 'sample_a_utm.tif'}")
    print(f"wrote {SAMPLE_DIR / 'sample_b_wgs84.tif'}")
    print("B-A at overlapping valid locations is expected to be 2.5 m, apart from display bilinear resampling.")


if __name__ == "__main__":
    main()
