"""End-to-end backend checks using FastAPI TestClient + real uploads.

Run: .venv/bin/python scripts/backend_test.py
Exits non-zero on the first failed expectation.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import zlib

import numpy as np

ROOT = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(ROOT, "..", "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

SAMPLES = os.path.join(ROOT, "..", "samples")
A_PATH = os.path.join(SAMPLES, "sample_a.tif")
B_PATH = os.path.join(SAMPLES, "sample_b.tif")

fails = []


def check(cond, msg):
    status = "ok  " if cond else "FAIL"
    print(f"[{status}] {msg}")
    if not cond:
        fails.append(msg)


def upload(sid, slot, path):
    with open(path, "rb") as f:
        r = client.post(
            f"/api/sessions/{sid}/slots/{slot}/upload",
            files={"file": (os.path.basename(path), f, "image/tiff")},
        )
    return r


def decode_view(resp):
    raw = zlib.decompress(resp.content)
    assert raw[:7] == b"GEOCP01"
    (hlen,) = struct.unpack_from("<I", raw, 7)
    header = json.loads(raw[11:11 + hlen])
    pos = 11 + hlen
    w, h = header["width"], header["height"]
    layers = {}
    for name in header["order"]:
        n = w * h
        vals = np.frombuffer(raw, dtype="<f4", count=n, offset=pos).reshape(h, w).copy()
        pos += n * 4
        mask = np.frombuffer(raw, count=n, offset=pos, dtype="uint8").reshape(h, w).copy()
        pos += n
        extras = {}
        for en in header["layers"][name]["extras"]:
            extras[en] = np.frombuffer(raw, count=n, offset=pos, dtype="uint8").reshape(h, w).copy()
            pos += n
        layers[name] = {"values": vals, "mask": mask, **extras}
    return header, layers


# 1. session + uploads -----------------------------------------------------
r = client.post("/api/sessions")
check(r.status_code == 200, "create session")
sid = r.json()["session_id"]

r = upload(sid, "a", A_PATH)
check(r.status_code == 200, f"upload A -> {r.status_code} {r.text[:200]}")
id_a = r.json()["dataset_id"]
meta_a = r.json()["metadata"]
check(meta_a["crs"]["epsg"] == 32632, "A CRS reported EPSG:32632")
check(meta_a["width"] == 106 and meta_a["height"] == 150, "A dimensions")
check(abs(meta_a["bands"][0]["scale"] - 0.1) < 1e-12, "A scale=0.1")
check(meta_a["bands"][0]["nodata"] == -32768, "A nodata=-32768")
check(meta_a["bands"][0]["unit"] == "m", "A unit m from band tag")

r = upload(sid, "b", B_PATH)
check(r.status_code == 200, f"upload B -> {r.status_code} {r.text[:200]}")
id_b = r.json()["dataset_id"]
meta_b = r.json()["metadata"]
check(meta_b["crs"]["epsg"] == 32633, "B CRS EPSG:32633")
check(meta_b["band_count"] == 2, "B has two bands")
check(meta_b["bands"][1]["unit"] == "ft", "B band2 unit ft")

comp = r.json()["comparison"]
check(comp["comparable"] is True, "A vs B-band1 comparable in metres")
check(comp["overlap_wgs84"] is not None, "overlap reported")

# 2. point reading vs analytic truth --------------------------------------
manifest = json.load(open(os.path.join(SAMPLES, "samples_manifest.json")))
for tp in manifest["test_points"]:
    r = client.get(f"/api/sessions/{sid}/point",
                   params={"lon": tp["lon"], "lat": tp["lat"]})
    d = r.json()
    a, b = d["slots"]["a"], d["slots"]["b"]
    print(f"   point {tp['name']}: A row/col=({a.get('row')},{a.get('col')}) "
          f"raw={a.get('raw_value')} cal={a.get('calibrated_value')} "
          f"nodata={a.get('is_nodata')}; B cal={b.get('calibrated_value')} "
          f"nodata={b.get('is_nodata')}")
    if tp["name"] == "a_nodata_hole":
        check(a.get("is_nodata") is True, "A-hole reported nodata in A")
        check(b.get("is_nodata") is False, "B still valid at A-hole")
        check(d["screen_position_comparable"] is False,
              "screen position flagged NOT comparable at A-hole")
    if tp["name"] == "b_nodata_hole":
        check(b.get("is_nodata") is True, "B-hole reported nodata in B")
        check(a.get("is_nodata") is False, "A still valid at B-hole")
    if tp["name"] == "valid_both":
        check(a.get("raw_value") is not None
              and abs(a["calibrated_value"] - a["raw_value"] * 0.1) < 1e-3,
              "A calibrated == raw*0.1")
        check(abs(a["calibrated_value"] - tp["A_expected"]) < 20.0,
              "A reading close to analytic truth (cell-center rasterisation)")
        check(abs(b["calibrated_value"] - tp["B_expected_m"]) < 20.0,
              "B reading close to analytic truth (cell-center rasterisation)")
        check(d["point_difference"] is not None,
              "point difference exists where both valid")
        # each file reports its own containing cell; centres can be ~20 m
        # apart, so A-B tolerates combined rasterisation offsets
        check(abs(d["point_difference"]["a_minus_b"]
                  - tp["A_minus_B_expected"]) < 25.0,
              "point A-B close to analytic -delta (rasterisation tolerant)")

# 3. swipe view: two grids aligned on same mercator extent ----------------
ov = comp["overlap_wgs84"]
from rasterio.warp import transform_bounds
ml, mb, mr, mt = transform_bounds("EPSG:4326", "EPSG:3857",
                                  ov["left"], ov["bottom"],
                                  ov["right"], ov["top"], densify_pts=21)
r = client.post(f"/api/sessions/{sid}/view", json={
    "bounds": {"left": ml, "bottom": mb, "right": mr, "top": mt},
    "width": 240, "height": 240, "mode": "swipe", "resampling": "bilinear",
    "expected_ids": {"a": id_a, "b": id_b},
})
check(r.status_code == 200, f"swipe view -> {r.status_code} {r.text[:200]}")
header, layers = decode_view(r)
check(set(layers) == {"a", "b"}, "swipe payload has a and b")
both_cover = (layers["a"]["mask"] & layers["b"]["mask"]).sum()
only_a = (layers["a"]["mask"] & ~layers["b"]["mask"].astype(bool)).sum()
only_b = (layers["b"]["mask"] & ~layers["a"]["mask"].astype(bool)).sum()
check(both_cover > 1000, f"common coverage present ({both_cover} px)")
check(only_a > 0 and only_b > 0,
      f"asymmetric coverage visible (onlyA={only_a}, onlyB={only_b})")
# calibrated units comparable: diff of medians small in overlap
ma = layers["a"]["values"][layers["a"]["mask"].astype(bool)
                           & layers["b"]["mask"].astype(bool)]
mb2 = layers["b"]["values"][layers["a"]["mask"].astype(bool)
                            & layers["b"]["mask"].astype(bool)]
check(np.nanmax(np.abs(ma - mb2)) < 60,
      f"A/B values in overlap close-ish as expected (max|A-B|={np.abs(ma-mb2).max():.2f})")

# 4. diff view -------------------------------------------------------------
r = client.post(f"/api/sessions/{sid}/view", json={
    "bounds": {"left": ml, "bottom": mb, "right": mr, "top": mt},
    "width": 240, "height": 240, "mode": "diff", "resampling": "bilinear",
    "expected_ids": {"a": id_a, "b": id_b},
})
check(r.status_code == 200, f"diff view -> {r.status_code} {r.text[:200]}")
dh, dl = decode_view(r)
d = dl["diff"]
check(d["mask"].sum() > 1000, "diff valid only on common coverage")
check(np.isnan(d["values"][d["mask"] == 0]).all(),
      "diff is NaN where either side missing (no invented values)")
check(dh["diff_stats"]["count"] == int(d["mask"].sum()), "stats count matches")
check(abs(dh["diff_stats"]["max"]) < 60 and abs(dh["diff_stats"]["min"]) < 60,
      f"diff range plausible: {dh['diff_stats']['min']:.2f}..{dh['diff_stats']['max']:.2f}")

# 5. feet band -> automatic unit conversion -------------------------------
r = client.put(f"/api/sessions/{sid}/slots/b/band", json={"band": 2})
check(r.status_code == 200 and r.json()["comparison"]["comparable"],
      "switch B to feet band still comparable")
warn = [w["code"] for w in r.json()["comparison"]["warnings"]]
check("units_converted" in warn, "unit-conversion warning emitted (m vs ft)")
r = client.post(f"/api/sessions/{sid}/view", json={
    "bounds": {"left": ml, "bottom": mb, "right": mr, "top": mt},
    "width": 160, "height": 160, "mode": "diff", "resampling": "bilinear",
    "expected_ids": {"a": id_a, "b": id_b},
})
check(r.status_code == 200, f"diff with feet band -> {r.status_code} {r.text[:200]}")
dh, dl = decode_view(r)
check(abs(dh["diff_stats"]["max"]) < 60,
      f"feet converted to metres before subtracting (max {dh['diff_stats']['max']:.2f})")
client.put(f"/api/sessions/{sid}/slots/b/band", json={"band": 1})

# 6. stale version guard ---------------------------------------------------
r = client.post(f"/api/sessions/{sid}/view", json={
    "bounds": {"left": ml, "bottom": mb, "right": mr, "top": mt},
    "width": 64, "height": 64, "mode": "swipe",
    "expected_ids": {"a": "deadbeef", "b": id_b},
})
check(r.status_code == 409, "stale expected dataset_id rejected (409)")

# 7. re-upload keeps point meaning, changes id; old id now invalid ---------
r = upload(sid, "a", A_PATH)
new_id_a = r.json()["dataset_id"]
check(new_id_a != id_a, "re-upload mints a new dataset_id")
r = client.post(f"/api/sessions/{sid}/view", json={
    "bounds": {"left": ml, "bottom": mb, "right": mr, "top": mt},
    "width": 64, "height": 64, "mode": "swipe",
    "expected_ids": {"a": id_a, "b": id_b},
})
check(r.status_code == 409, "old dataset_id rejected after re-upload")
r = client.get(f"/api/sessions/{sid}/point",
               params={"lon": 11.012, "lat": 46.012})
check(r.status_code == 200 and r.json()["slots"]["a"]["dataset_id"] == new_id_a,
      "geographic point query still works against new file version")

# 8. unreadable upload does not destroy the good slot ----------------------
bad = os.path.join(SAMPLES, "not_a_raster.tif")
with open(bad, "wb") as f:
    f.write(b"this is definitely not a geotiff")
r = upload(sid, "b", bad)
check(r.status_code == 422, f"garbage upload rejected -> {r.status_code}")
state = client.get(f"/api/sessions/{sid}").json()
check(state["slots"]["b"] is not None
      and state["slots"]["b"]["dataset_id"] == id_b,
      "previous good B remains viewable after failed upload")
os.unlink(bad)

# 9. no-CRS file rejected --------------------------------------------------
import rasterio
from rasterio.transform import from_origin
nocrs = os.path.join(SAMPLES, "nocrs.tif")
with rasterio.open(nocrs, "w", driver="GTiff", width=4, height=4, count=1,
                   dtype="float32", transform=from_origin(0, 10, 1, 1)) as dst:
    dst.write(np.ones((4, 4), dtype="float32"), 1)
r = upload(sid, "a", nocrs)
check(r.status_code == 422 and "CRS" in r.json()["detail"]["message"],
      f"CRS-less file rejected with reason -> {r.status_code}")
os.unlink(nocrs)

print()
if fails:
    print(f"{len(fails)} FAILURES")
    sys.exit(1)
print("ALL BACKEND CHECKS PASSED")
