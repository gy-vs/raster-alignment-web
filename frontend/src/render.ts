import type { Bounds, PointResponse, ViewGrid } from './types';

export type ColorMode = 'terrain' | 'grayscale' | 'diff';

export interface ColorSettings {
  mode: ColorMode;
  elevationMin: number | null;
  elevationMax: number | null;
  diffLimit: number | null;
  shared: boolean;
}

function clamp01(x: number): number {
  return Math.min(1, Math.max(0, x));
}

function mix(a: [number, number, number], b: [number, number, number], t: number): [number, number, number] {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

const TERRAIN: Array<[number, [number, number, number]]> = [
  [0, [29, 55, 92]],
  [0.18, [55, 105, 115]],
  [0.36, [92, 138, 84]],
  [0.58, [178, 165, 94]],
  [0.78, [139, 88, 55]],
  [1, [238, 238, 238]],
];

function terrain(t: number): [number, number, number] {
  const x = clamp01(t);
  for (let i = 0; i < TERRAIN.length - 1; i += 1) {
    const [t0, c0] = TERRAIN[i];
    const [t1, c1] = TERRAIN[i + 1];
    if (x >= t0 && x <= t1) return mix(c0, c1, (x - t0) / (t1 - t0));
  }
  return TERRAIN[TERRAIN.length - 1][1];
}

function rangeFor(values: Float32Array | null, fallbackMin = 0, fallbackMax = 1): [number, number] {
  if (!values || values.length === 0) return [fallbackMin, fallbackMax];
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  for (let i = 0; i < values.length; i += 1) {
    const v = values[i];
    if (Number.isFinite(v)) {
      min = Math.min(min, v);
      max = Math.max(max, v);
    }
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [fallbackMin, fallbackMax];
  if (min === max) return [min - 1, max + 1];
  return [min, max];
}

export function sharedElevationRange(grid: ViewGrid | null): [number, number] {
  const amin = rangeFor(grid?.a ?? null);
  const bmin = rangeFor(grid?.b ?? null);
  return [Math.min(amin[0], bmin[0]), Math.max(amin[1], bmin[1])];
}

export function colorize(grid: Float32Array | null, width: number, height: number, mode: 'terrain' | 'grayscale' | 'diff',
                        min: number, max: number): HTMLCanvasElement {
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d', { willReadFrequently: true })!;
  const image = ctx.createImageData(width, height);
  const data = image.data;

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = y * width + x;
      const out = index * 4;
      const v = grid?.[index];
      const striped = (Math.floor(x / 8) + Math.floor(y / 8)) % 2 === 0;
      if (v === undefined || v === null || !Number.isFinite(v)) {
        data[out] = striped ? 17 : 10;
        data[out + 1] = striped ? 23 : 14;
        data[out + 2] = striped ? 34 : 22;
        data[out + 3] = 230;
        continue;
      }

      if (mode === 'diff') {
        const limit = max > 0 ? max : 1;
        let c: [number, number, number];
        if (v < 0) c = mix([230, 230, 235], [198, 52, 42], clamp01(-v / limit));
        else if (v > 0) c = mix([230, 230, 235], [36, 102, 196], clamp01(v / limit));
        else c = [238, 238, 238];
        [data[out], data[out + 1], data[out + 2]] = c;
        data[out + 3] = 255;
      } else {
        const t = (v - min) / (max - min);
        const c = mode === 'grayscale'
          ? mix([20, 25, 32], [235, 240, 245], clamp01(t))
          : terrain(t);
        data[out] = c[0];
        data[out + 1] = c[1];
        data[out + 2] = c[2];
        data[out + 3] = 255;
      }
    }
  }
  ctx.putImageData(image, 0, 0);
  return canvas;
}

export function legendGradient(mode: ColorMode): string {
  if (mode === 'diff') return 'linear-gradient(90deg, #c6342a 0%, #eeeeee 50%, #2466c4 100%)';
  if (mode === 'grayscale') return 'linear-gradient(90deg, #141920, #ebf0f5)';
  const stops = TERRAIN.map(([t, c]) => `rgb(${c[0]},${c[1]},${c[2]}) ${Math.round(t * 100)}%`);
  return `linear-gradient(90deg, ${stops.join(',')})`;
}

export function boundsContains(b: Bounds, lon: number, lat: number): boolean {
  return lon >= b.west && lon <= b.east && lat >= b.south && lat <= b.north;
}

export function pointToPixel(b: Bounds, width: number, height: number, lon: number, lat: number): [number, number] {
  return [
    ((lon - b.west) / (b.east - b.west)) * width,
    ((b.north - lat) / (b.north - b.south)) * height,
  ];
}

export function pixelToLonLat(b: Bounds, width: number, height: number, x: number, y: number): [number, number] {
  return [
    b.west + (x / width) * (b.east - b.west),
    b.north - (y / height) * (b.north - b.south),
  ];
}

export function gridValue(grid: Float32Array | null, width: number, height: number, px: number, py: number): number | null {
  if (!grid) return null;
  const x = Math.floor(px);
  const y = Math.floor(py);
  if (x < 0 || y < 0 || x >= width || y >= height) return null;
  const v = grid[y * width + x];
  return Number.isFinite(v) ? v : null;
}

export function describeComparability(point: PointResponse | null): { text: string; cls: string } {
  if (!point) return { text: '尚未选点。', cls: 'muted' };
  if (point.both_valid && point.difference !== null) return { text: '该点双方都有有效原始像元，可比较。', cls: 'good' };
  return { text: point.warning ?? '该点当前不可比较。', cls: 'bad' };
}
