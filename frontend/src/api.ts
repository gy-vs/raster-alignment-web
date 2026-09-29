import type { Bounds, PointResponse, SessionState, ViewGrid, ViewHeader } from './types';

const SESSION_KEY = 'geotiff-check-session-id';

function storedSession(): string {
  let id = sessionStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID().replaceAll('-', '');
    sessionStorage.setItem(SESSION_KEY, id);
  }
  return id;
}

export const sessionId = storedSession();

async function readError(response: Response): Promise<Error> {
  let message = `HTTP ${response.status}`;
  try {
    const data = await response.json();
    if (data?.detail) message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
  } catch {
    // keep default
  }
  return new Error(message);
}

export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'X-Session-ID': sessionId,
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) throw await readError(response);
  return response.json() as Promise<T>;
}

export async function getSession(): Promise<SessionState> {
  return apiJson<SessionState>('/api/session');
}

export async function createSession(): Promise<SessionState> {
  return apiJson<SessionState>('/api/session', { method: 'POST' });
}

export async function uploadFile(slot: 'a' | 'b', file: Blob, filename: string): Promise<SessionState> {
  const form = new FormData();
  form.append('file', file, filename);
  return apiJson<SessionState>(`/api/upload/${slot}`, { method: 'POST', body: form });
}

export async function fetchSample(filename: 'sample_a_utm.tif' | 'sample_b_wgs84.tif'): Promise<Blob> {
  const response = await fetch(`/samples/${filename}`);
  if (!response.ok) throw new Error(`无法读取内置样本 ${filename}`);
  return response.blob();
}

export async function fetchView(input: {
  bandA: number;
  bandB: number;
  bounds: Bounds;
  width: number;
  height: number;
  requestId: string;
  signal: AbortSignal;
}): Promise<ViewGrid> {
  const response = await fetch('/api/view', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Session-ID': sessionId,
    },
    body: JSON.stringify({
      band_a: input.bandA,
      band_b: input.bandB,
      bounds: input.bounds,
      width: input.width,
      height: input.height,
      request_id: input.requestId,
    }),
    signal: input.signal,
  });
  if (!response.ok) throw await readError(response);
  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  if (bytes.length < 8 || String.fromCharCode(...bytes.slice(0, 4)) !== 'GTCG') {
    throw new Error('视图响应格式无效');
  }
  const view = new DataView(buffer);
  const headerLength = view.getUint32(4, true);
  const headerStart = 8;
  const header = JSON.parse(new TextDecoder().decode(bytes.subarray(headerStart, headerStart + headerLength))) as ViewHeader;
  let offset = headerStart + headerLength;
  const expected = header.width * header.height;
  const byteCount = expected * 4;
  function readArray(exists: boolean): Float32Array | null {
    if (!exists) return null;
    const array = new Float32Array(buffer, offset, expected).slice();
    offset += byteCount;
    return array;
  }
  const a = readArray(header.arrays.a);
  const b = readArray(header.arrays.b);
  const diff = readArray(header.arrays.diff);
  return { header, a, b, diff, receivedAt: Date.now() };
}

export async function queryPoint(input: {
  lon: number;
  lat: number;
  bandA: number;
  bandB: number;
  signal?: AbortSignal;
}): Promise<PointResponse> {
  return apiJson<PointResponse>('/api/point', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ lon: input.lon, lat: input.lat, band_a: input.bandA, band_b: input.bandB }),
    signal: input.signal,
  });
}
