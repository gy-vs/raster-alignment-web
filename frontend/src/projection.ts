// Web-Mercator (EPSG:3857) <-> WGS84 (EPSG:4326) math, standard formulas.
// The backend works in EPSG:3857 for view alignment; points use WGS84.

const R = 6378137.0;
export const MERC_MAX = 20037508.342789244;

export function lonLatToMerc(lon: number, lat: number): [number, number] {
  const x = R * (lon * Math.PI) / 180;
  const clampedLat = Math.max(Math.min(lat, 85.05112878), -85.05112878);
  const y = R * Math.log(Math.tan(Math.PI / 4 + (clampedLat * Math.PI / 180) / 2));
  return [x, y];
}

export function mercToLonLat(x: number, y: number): [number, number] {
  const lon = (x / R) * 180 / Math.PI;
  const lat = (2 * Math.atan(Math.exp(y / R)) - Math.PI / 2) * 180 / Math.PI;
  return [lon, lat];
}

export interface BBox {
  left: number;
  bottom: number;
  right: number;
  top: number;
}

export function wgs84ToMercBounds(b: BBox): BBox {
  const [l, bo] = lonLatToMerc(b.left, b.bottom);
  const [r, t] = lonLatToMerc(b.right, b.top);
  return { left: l, bottom: bo, right: r, top: t };
}

export function clampMercBounds(b: BBox): BBox {
  return {
    left: Math.max(b.left, -MERC_MAX),
    bottom: Math.max(b.bottom, -MERC_MAX),
    right: Math.min(b.right, MERC_MAX),
    top: Math.min(b.top, MERC_MAX),
  };
}

// Compute a destination bounds anchored at a mercator point after a pixel
// drag (keeps geographic alignment identical for both layers).
export function panBounds(b: BBox, dxPixels: number, dyPixels: number,
                          canvasW: number, canvasH: number): BBox {
  const sx = (b.right - b.left) / canvasW;
  const sy = (b.top - b.bottom) / canvasH;
  return {
    left: b.left - dxPixels * sx,
    right: b.right - dxPixels * sx,
    bottom: b.bottom + dyPixels * sy,
    top: b.top + dyPixels * sy,
  };
}

export function zoomAround(b: BBox, factor: number, cxRatio: number,
                           cyRatio: number): BBox {
  // factor > 1 zooms in (smaller extent); anchor stays geographically fixed.
  const w = b.right - b.left;
  const h = b.top - b.bottom;
  const ax = b.left + cxRatio * w;
  const ay = b.bottom + (1 - cyRatio) * h;
  const nw = w / factor;
  const nh = h / factor;
  return {
    left: ax - cxRatio * nw,
    right: ax + (1 - cxRatio) * nw,
    bottom: ay - (1 - cyRatio) * nh,
    top: ay + cyRatio * nh,
  };
}

export function boundsIntersect(a: BBox, b: BBox): boolean {
  return !(a.right < b.left || a.left > b.right ||
           a.top < b.bottom || a.bottom > b.top);
}
