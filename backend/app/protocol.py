"""Compact view response encoding.

JSON header (UTF-8) + '\\n' + raw little-endian float32 / uint8 arrays, the
whole body DEFLATE-compressed by FastAPI's GZipMiddleware... instead we
zlib-compress here with a 6-byte magic prefix so the client can decode
without pulling a base64-expanded payload through JSON.

Body layout after decompression:
  GEOCP01
  <uint32 header_json_length><header json bytes>
  per layer, in order listed by header["order"]:
      float32 values (w*h)
      uint8  mask   (w*h)
      [diff layer only] uint8 mask_a, uint8 mask_b, uint8 only_a, uint8 only_b
"""
from __future__ import annotations

import json
import struct
import zlib
from typing import Any

import numpy as np

MAGIC = b"GEOCP01"


def encode_view(header: dict[str, Any], layers: dict[str, dict]) -> bytes:
    """layers: {name: {"values": float32 ndarray, "mask": uint8 ndarray,
    optional extra uint8 arrays named in header.layers[name].extras}}."""
    order = list(layers.keys())
    header = dict(header)
    header["order"] = order
    header.setdefault("format", {
        "values": "float32_le", "mask": "uint8", "nodata": "NaN",
    })
    layer_specs = {}
    body = bytearray()
    for name in order:
        layer = layers[name]
        vals = np.ascontiguousarray(layer["values"], dtype="<f4")
        mask = np.ascontiguousarray(layer["mask"], dtype="uint8")
        body += vals.tobytes()
        body += mask.tobytes()
        extras = []
        for extra_name in layer.get("extras", []):
            arr = np.ascontiguousarray(layer[extra_name], dtype="uint8")
            body += arr.tobytes()
            extras.append(extra_name)
        layer_specs[name] = {"extras": extras}
    header["layers"] = layer_specs

    hb = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    raw = MAGIC + struct.pack("<I", len(hb)) + hb + bytes(body)
    # mtime fixed -> deterministic compression for tests/repeatability
    co = zlib.compressobj(6, zlib.DEFLATED, 15, 8, zlib.Z_DEFAULT_STRATEGY)
    return co.compress(raw) + co.flush()
