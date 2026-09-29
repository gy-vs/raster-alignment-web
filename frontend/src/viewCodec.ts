// Decoder for the backend view payload (see backend/app/protocol.py):
//   zlib(deflate) of: MAGIC(7) + uint32 headerLen + JSON + float32/u8 arrays.
// Browsers expose DecompressionStream('deflate') which handles zlib-wrapped
// deflate; a tiny manual inflate fallback is unnecessary in evergreen
// browsers, but we detect the case and report it clearly.

import type { DecodedView, ViewHeader } from "./types";

const MAGIC = "GEOCP01";

async function inflate(buf: ArrayBuffer): Promise<Uint8Array> {
  if (typeof DecompressionStream === "undefined") {
    throw new Error("浏览器不支持 DecompressionStream，无法解码视图数据。");
  }
  const stream = new Response(buf).body!
    .pipeThrough(new DecompressionStream("deflate"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

export async function decodeViewPayload(resp: Response): Promise<DecodedView> {
  const ab = await resp.arrayBuffer();
  const raw = await inflate(ab);
  const magicBytes = new TextDecoder().decode(raw.slice(0, 7));
  if (magicBytes !== MAGIC) {
    throw new Error("视图响应头不匹配（不是本工具的计算结果）。");
  }
  const headerLen = new DataView(raw.buffer, raw.byteOffset, raw.byteLength)
    .getUint32(7, true);
  const header: ViewHeader = JSON.parse(
    new TextDecoder().decode(raw.slice(11, 11 + headerLen))
  );
  let pos = 11 + headerLen;
  const w = header.width;
  const h = header.height;
  const n = w * h;
  const dv = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);

  const layers: DecodedView["layers"] = {};
  for (const name of header.order) {
    const values = new Float32Array(n);
    for (let i = 0; i < n; i++) values[i] = dv.getFloat32(pos + i * 4, true);
    pos += n * 4;

    const mask = new Uint8Array(n);
    mask.set(raw.subarray(pos, pos + n));
    pos += n;

    const layer: DecodedView["layers"][string] = { values, mask };
    for (const extra of header.layers[name].extras) {
      const arr = new Uint8Array(n);
      arr.set(raw.subarray(pos, pos + n));
      pos += n;
      if (extra === "mask_a") layer.maskA = arr;
      if (extra === "mask_b") layer.maskB = arr;
      if (extra === "only_a") layer.onlyA = arr;
      if (extra === "only_b") layer.onlyB = arr;
    }
    layers[name] = layer;
  }
  return { header, layers };
}
