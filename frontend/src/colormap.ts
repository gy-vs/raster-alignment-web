// Color ramps used ONLY for displaying numeric grids. Colors are never an
// input to comparison/difference math, which happens on the server.

export type RampName = "terrain" | "grayscale" | "viridis";

type RGB = [number, number, number];

const TERRAIN_STOPS: [number, RGB][] = [
  [0.0, [13, 59, 102]],
  [0.15, [38, 120, 165]],
  [0.35, [120, 180, 160]],
  [0.55, [210, 215, 140]],
  [0.75, [200, 150, 90]],
  [1.0, [120, 60, 30]],
];

const VIRIDIS_STOPS: [number, RGB][] = [
  [0.0, [68, 1, 84]],
  [0.25, [59, 82, 139]],
  [0.5, [33, 145, 140]],
  [0.75, [94, 201, 98]],
  [1.0, [253, 231, 37]],
];

function rampAt(stops: [number, RGB][], t: number): RGB {
  const x = Math.max(0, Math.min(1, t));
  for (let i = 1; i < stops.length; i++) {
    if (x <= stops[i][0]) {
      const [t0, c0] = stops[i - 1];
      const [t1, c1] = stops[i];
      const f = (x - t0) / (t1 - t0);
      return [
        Math.round(c0[0] + (c1[0] - c0[0]) * f),
        Math.round(c0[1] + (c1[1] - c0[1]) * f),
        Math.round(c0[2] + (c1[2] - c0[2]) * f),
      ];
    }
  }
  return stops[stops.length - 1][1];
}

// 256-entry RGBA lookup tables built once.
function buildLut(stops: [number, RGB][]): Uint8ClampedArray {
  const lut = new Uint8ClampedArray(256 * 4);
  for (let i = 0; i < 256; i++) {
    const [r, g, b] = rampAt(stops, i / 255);
    lut[i * 4] = r;
    lut[i * 4 + 1] = g;
    lut[i * 4 + 2] = b;
    lut[i * 4 + 3] = 255;
  }
  return lut;
}

function buildGrayLut(): Uint8ClampedArray {
  const lut = new Uint8ClampedArray(256 * 4);
  for (let i = 0; i < 256; i++) {
    lut[i * 4] = lut[i * 4 + 1] = lut[i * 4 + 2] = i;
    lut[i * 4 + 3] = 255;
  }
  return lut;
}

const LUTS: Record<RampName, Uint8ClampedArray> = {
  terrain: buildLut(TERRAIN_STOPS),
  viridis: buildLut(VIRIDIS_STOPS),
  grayscale: buildGrayLut(),
};

export function rasterizeSequential(
  values: Float32Array,
  mask: Uint8Array,
  width: number,
  height: number,
  vmin: number,
  vmax: number,
  ramp: RampName,
  opacity: number,
): ImageData {
  const img = new ImageData(width, height);
  const out = img.data;
  const lut = LUTS[ramp];
  const span = vmax - vmin || 1;
  const a = Math.round(Math.max(0, Math.min(1, opacity)) * 255);
  for (let i = 0; i < values.length; i++) {
    if (!mask[i]) {
      out[i * 4 + 3] = 0;
      continue;
    }
    const v = values[i];
    if (!Number.isFinite(v)) {
      out[i * 4 + 3] = 0;
      continue;
    }
    let t = (v - vmin) / span;
    t = t < 0 ? 0 : t > 1 ? 1 : t;
    const idx = (t * 255) | 0;
    out[i * 4] = lut[idx * 4];
    out[i * 4 + 1] = lut[idx * 4 + 1];
    out[i * 4 + 2] = lut[idx * 4 + 2];
    out[i * 4 + 3] = a;
  }
  return img;
}

// Diverging red(-) / white(0) / blue(+) ramp centred on zero.
export function rasterizeDiverging(
  values: Float32Array,
  mask: Uint8Array,
  width: number,
  height: number,
  absMax: number,
  saturationCap: number,
  showOnlyA: Uint8Array | null,
  showOnlyB: Uint8Array | null,
): ImageData {
  const img = new ImageData(width, height);
  const out = img.data;
  const scale = absMax > 0 ? absMax : 1;
  for (let i = 0; i < values.length; i++) {
    const o = i * 4;
    if (showOnlyA && showOnlyA[i] && (!mask[i])) {
      // A has data, B does not: amber hatch fill
      out[o] = 230; out[o + 1] = 170; out[o + 2]  = 40; out[o + 3] = 110;
      continue;
    }
    if (showOnlyB && showOnlyB[i] && (!mask[i])) {
      // B has data, A does not: teal fill
      out[o] = 40; out[o + 1] = 180; out[o + 2] = 170; out[o + 3] = 110;
      continue;
    }
    if (!mask[i] || !Number.isFinite(values[i])) {
      out[o + 3] = 0;
      continue;
    }
    let t = values[i] / scale;
    t = t < -1 ? -1 : t > 1 ? 1 : t;
    // white at 0 -> deep red negative, deep blue positive
    const intensity = Math.round(255 * (1 - Math.abs(t)));
    if (t >= 0) {
      out[o] = intensity;
      out[o + 1] = intensity;
      out[o + 2] = 255;
    } else {
      out[o] = 255;
      out[o + 1] = intensity;
      out[o + 2] = intensity;
    }
    out[o + 3] = 255;
  }
  // saturationCap kept for API stability of future histogram clipping;
  // referenced to satisfy linters without affecting colors.
  void saturationCap;
  return img;
}

// Diagonal hatch marking cells that are nodata or outside the raster. It
// is a display cue only; the underlying numeric grid keeps them NaN.
export function rasterizeMaskHatch(
  mask: Uint8Array,
  width: number,
  height: number,
): HTMLCanvasElement {
  const c = document.createElement("canvas");
  c.width = width;
  c.height = height;
  const g = c.getContext("2d")!;
  g.fillStyle = "rgba(255,255,255,0.05)";
  for (let y = 0; y < height; y += 2) {
    for (let x = 0; x < width; x += 2) {
      if (!mask[y * width + x]) g.fillRect(x, y, 1, 1);
    }
  }
  return c;
}

export function percentileRange(
  values: Float32Array,
  mask: Uint8Array,
  low = 0.02,
  high = 0.98,
): [number, number] {
  const valid: number[] = [];
  for (let i = 0; i < values.length; i++) {
    if (mask[i] && Number.isFinite(values[i])) valid.push(values[i]);
  }
  if (valid.length === 0) return [0, 1];
  valid.sort((a, b) => a - b);
  const lo = valid[Math.floor(low * (valid.length - 1))];
  const hi = valid[Math.floor(high * (valid.length - 1))];
  if (hi - lo < 1e-9) return [lo - 1, hi + 1];
  return [lo, hi];
}
