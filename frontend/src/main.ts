import { createSession, fetchSample, fetchView, getSession, queryPoint, uploadFile } from './api';
import {
  boundsContains,
  colorize,
  gridValue,
  legendGradient,
  pixelToLonLat,
  pointToPixel,
  sharedElevationRange,
  type ColorSettings,
} from './render';
import type {
  Bounds,
  PointResponse,
  RasterMetadata,
  SessionState,
  Slot,
  ViewGrid,
} from './types';
import './style.css';

type Mode = 'swipe' | 'diff';

interface SelectedPoint {
  lon: number;
  lat: number;
}

interface AppState {
  session: SessionState | null;
  bandA: number;
  bandB: number;
  bounds: Bounds | null;
  view: ViewGrid | null;
  pendingRequestId: string | null;
  requestError: string | null;
  uploadError: string | null;
  mode: Mode;
  divider: number;
  colors: ColorSettings;
  point: SelectedPoint | null;
  pointData: PointResponse | null;
  pointLoading: boolean;
  pointError: string | null;
  busySlot: Slot | null;
}

const state: AppState = {
  session: null,
  bandA: 1,
  bandB: 1,
  bounds: null,
  view: null,
  pendingRequestId: null,
  requestError: null,
  uploadError: null,
  mode: 'swipe',
  divider: 0.5,
  colors: { mode: 'terrain', elevationMin: null, elevationMax: null, diffLimit: null, shared: true },
  point: null,
  pointData: null,
  pointLoading: false,
  pointError: null,
  busySlot: null,
};

let viewAbort: AbortController | null = null;
let pointAbort: AbortController | null = null;
let viewTimer: number | null = null;
let viewSeq = 0;
let canvas: HTMLCanvasElement | null = null;
let host: HTMLDivElement | null = null;
let draggingDivider = false;

const $ = <T extends Element = Element>(selector: string): T => document.querySelector(selector) as T;

function initDom(): void {
  $('#app').innerHTML = `
    <header class="app-header">
      <div>
        <h1>GeoTIFF 地理位置数值对照</h1>
        <div class="subtle">本地检查会话 · Rasterio 窗口读取 · WGS84 地理对齐</div>
      </div>
    </header>
    <main class="layout">
      <aside class="sidebar" id="sidebar"></aside>
      <section class="map-pane">
        <div class="viewport-wrap" id="canvasHost">
          <canvas id="mapCanvas"></canvas>
          <div class="overlay" id="statusOverlay"></div>
          <div class="overlay" id="dividerLabel"></div>
          <div class="overlay" id="coordsOverlay">经度 —，纬度 —</div>
        </div>
        <div class="info-panel" id="infoPanel"></div>
      </section>
    </main>`;
  canvas = $<HTMLCanvasElement>('#mapCanvas');
  host = $<HTMLDivElement>('#canvasHost');
  bindMapEvents();
}

function formatBytes(n: number): string {
  if (n > 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
  if (n > 1024 ** 2) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  if (n > 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

function fmt(v: number | null | undefined, digits = 3): string {
  return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : '—';
}

function metadata(meta: RasterMetadata | null, slot: Slot): string {
  if (!meta) {
    return `<div>
      <h3>${slot.toUpperCase()} 文件</h3><p class="muted">尚未成功打开。</p>
      <div class="slot-file"><input type="file" accept=".tif,.tiff,.geotiff,image/tiff" data-slot="${slot}" /></div>
    </div>`;
  }
  const b = meta.bounds_wgs84;
  const nb = meta.bounds_native;
  return `<div>
    <h3>${slot.toUpperCase()} 文件 <span class="pill">${meta.driver ?? 'raster'}</span></h3>
    <div class="file-name">${meta.filename}</div>
    <div class="small">ID <code>${meta.file_id}</code> · ${formatBytes(meta.size_bytes)}</div>
    <dl>
      <dt>坐标参考</dt><dd>${meta.crs_name ?? meta.crs ?? '—'} ${meta.projected ? '<span class="pill">投影</span>' : meta.geographic ? '<span class="pill">地理</span>' : ''}</dd>
      <dt>像元矩阵</dt><dd>${meta.width} 列 × ${meta.height} 行 × ${meta.band_count} 波段</dd>
      <dt>WGS84范围</dt><dd>[${fmt(b[0], 6)}, ${fmt(b[1], 6)}, ${fmt(b[2], 6)}, ${fmt(b[3], 6)}]</dd>
      <dt>原生范围</dt><dd>[${fmt(nb[0], 3)}, ${fmt(nb[1], 3)}, ${fmt(nb[2], 3)}, ${fmt(nb[3], 3)}]</dd>
    </dl>
    <h3>波段</h3>
    <select data-band-select="${slot}">
      ${meta.bands.map((band) => `<option value="${band.index}" ${band.index === (slot === 'a' ? state.bandA : state.bandB) ? 'selected' : ''}>
        ${band.index}: ${band.description ?? `Band ${band.index}`} · ${band.dtype} · ${band.units || '无单位'} · scale ${band.scale} offset ${band.offset}
      </option>`).join('')}
    </select>
    <div class="slot-file" style="margin-top:8px">
      <label class="small">重新上传</label>
      <input type="file" accept=".tif,.tiff,.geotiff,image/tiff" data-slot="${slot}" />
    </div>
  </div>`;
}

function bandOptions(): string {
  const a = state.session?.a ?? null;
  const b = state.session?.b ?? null;
  return `<div class="card"><h2>文件与波段</h2><div class="band-grid">
    ${metadata(a, 'a')}${metadata(b, 'b')}
  </div></div>`;
}

function sampleCard(): string {
  return `<div class="card">
    <h2>可重复生成的小样本</h2>
    <p class="small">样本仍通过普通上传接口读取和计算；不会自动加载。</p>
    <div class="two-col">
      <button data-sample="sample_a_utm.tif">载入样本 A（UTM/scale）</button>
      <button data-sample="sample_b_wgs84.tif">载入样本 B（WGS84/缺测）</button>
    </div>
    <p class="small">生成命令：<code>python3 scripts/make_samples.py</code>；样本说明见 <code>samples/README.md</code>。</p>
  </div>`;
}

function comparisonCard(): string {
  const c = state.session?.comparison;
  if (!c) return '';
  const common = c.common_bounds_wgs84;
  return `<div class="card">
    <h2>共同覆盖与比较条件</h2>
    <div class="banner ${c.comparable ? 'ok' : 'warn'}">
      ${c.comparable ? '两份所选波段可以进行数值比较。' : `不会计算差值：${c.reason ?? '条件未满足'}`}
    </div>
    ${common ? `<dl><dt>共同范围</dt><dd>[${fmt(common[0], 6)}, ${fmt(common[1], 6)}, ${fmt(common[2], 6)}, ${fmt(common[3], 6)}]</dd>
      <dt>差值定义</dt><dd>B − A</dd><dt>单位</dt><dd>${c.difference_units ?? '—'}</dd></dl>` : ''}
    <div class="row"><button id="zoomCommon" ${common ? '' : 'disabled'}>进入共同覆盖区</button></div>
  </div>`;
}

function displayCard(): string {
  const c = state.colors;
  return `<div class="card">
    <h2>显示</h2>
    <div class="segmented">
      <button data-mode="swipe" class="${state.mode === 'swipe' ? 'active' : ''}">分隔线</button>
      <button data-mode="diff" class="${state.mode === 'diff' ? 'active' : ''}">差值</button>
    </div>
    <div class="banner ${state.pendingRequestId ? 'info' : 'ok'}">
      ${state.pendingRequestId ? '<span class="spinner"></span>正在等待新视图；当前画面仍标注为已完成的旧视图。' : '当前显示的是最近一次完成的服务端视图。'}
    </div>
    ${state.requestError ? `<div class="banner error">${state.requestError}</div>` : ''}
    ${state.uploadError ? `<div class="banner error">${state.uploadError}</div>` : ''}
    <h3>影像配色（不参与计算）</h3>
    <div class="segmented">
      <button data-color="terrain" class="${c.mode === 'terrain' ? 'active' : ''}">地形色</button>
      <button data-color="grayscale" class="${c.mode === 'grayscale' ? 'active' : ''}">灰度</button>
    </div>
    <label class="small"><input type="checkbox" id="sharedRange" ${c.shared ? 'checked' : ''}/> 两图共用同一高程范围</label>
    <div class="legend" style="background:${legendGradient(c.mode)}"></div>
    <div class="range-row"><span>最小值</span><input type="number" step="any" id="minInput" value="${fmt(c.elevationMin, 2)}" /><span class="small">标定值</span></div>
    <div class="range-row"><span>最大值</span><input type="number" step="any" id="maxInput" value="${fmt(c.elevationMax, 2)}" /><span class="small">标定值</span></div>
    <button id="resetRange">按当前视图重置范围</button>
    <h3>差值配色</h3>
    <div class="legend" style="background:${legendGradient('diff')}"></div>
    <div class="range-row"><span>±范围</span><input type="number" step="any" id="diffLimitInput" value="${fmt(c.diffLimit, 2)}" /><span class="small">B−A</span></div>
    <p class="small">斜纹深色表示缺测或无覆盖；缺测不补零、不推测。</p>
  </div>`;
}

function renderSidebar(): void {
  $('#sidebar').innerHTML = sampleCard() + comparisonCard() + bandOptions() + displayCard();
  bindSidebarEvents();
}

function defaultBand(slot: Slot): number {
  const meta = state.session?.[slot];
  return meta?.bands[0]?.index ?? 1;
}

function syncBandsFromSession(): void {
  const a = state.session?.a;
  const b = state.session?.b;
  if (a && !a.bands.some((x) => x.index === state.bandA)) state.bandA = defaultBand('a');
  if (b && !b.bands.some((x) => x.index === state.bandB)) state.bandB = defaultBand('b');
}

function commonBounds(): Bounds | null {
  const c = state.session?.comparison.common_bounds_wgs84;
  if (!c) return null;
  return { west: c[0], south: c[1], east: c[2], north: c[3] };
}

function expandedBounds(meta: RasterMetadata): Bounds {
  const [w, s, e, n] = meta.bounds_wgs84;
  return { west: w, south: s, east: e, north: n };
}

function fitBounds(rect: { width: number; height: number }, target: Bounds): Bounds {
  const dLon = target.east - target.west;
  const dLat = target.north - target.south;
  const viewAspect = rect.width / Math.max(1, rect.height);
  const boundsAspect = dLon / dLat;
  const cx = (target.west + target.east) / 2;
  const cy = (target.south + target.north) / 2;
  if (viewAspect > boundsAspect) {
    const half = (dLon * (viewAspect / boundsAspect)) / 2;
    return { west: cx - half, east: cx + half, south: target.south, north: target.north };
  }
  const halfY = (dLat * (boundsAspect / viewAspect)) / 2;
  return { west: target.west, east: target.east, south: cy - halfY, north: cy + halfY };
}

async function handleUpload(slot: Slot, blob: Blob, filename: string): Promise<void> {
  state.busySlot = slot;
  state.uploadError = null;
  state.pendingRequestId = crypto.randomUUID();
  renderAll();
  try {
    state.session = await uploadFile(slot, blob, filename);
    state.view = null;
    state.pointData = null;
    state.pointError = null;
    syncBandsFromSession();
    state.pendingRequestId = null;
    const rect = canvasRect();
    const common = commonBounds();
    const own = state.session[slot] ? expandedBounds(state.session[slot]!) : null;
    state.bounds = fitBounds(rect, common ?? own ?? state.bounds ?? { west: -180, south: -85, east: 180, north: 85 });
    await scheduleView(0);
    if (state.point) await refreshPoint();
  } catch (err) {
    state.pendingRequestId = null;
    state.uploadError = err instanceof Error ? err.message : String(err);
  } finally {
    state.busySlot = null;
    renderAll();
  }
}

function bindSidebarEvents(): void {
  document.querySelectorAll<HTMLInputElement>('input[type="file"][data-slot]').forEach((input) => {
    input.addEventListener('change', async () => {
      const file = input.files?.[0];
      const slot = input.dataset.slot as Slot;
      if (file) await handleUpload(slot, file, file.name);
    });
  });
  document.querySelectorAll<HTMLButtonElement>('[data-sample]').forEach((button) => {
    button.addEventListener('click', async () => {
      const name = button.dataset.sample as 'sample_a_utm.tif' | 'sample_b_wgs84.tif';
      const slot: Slot = name.includes('_a_') ? 'a' : 'b';
      button.disabled = true;
      try {
        const blob = await fetchSample(name);
        await handleUpload(slot, blob, name);
      } catch (err) {
        state.uploadError = err instanceof Error ? err.message : String(err);
        renderAll();
      }
    });
  });
  document.querySelectorAll<HTMLSelectElement>('[data-band-select]').forEach((select) => {
    select.addEventListener('change', async () => {
      const slot = select.dataset.bandSelect as Slot;
      const value = Number(select.value);
      if (slot === 'a') state.bandA = value;
      else state.bandB = value;
      state.pointData = null;
      await scheduleView(0);
      if (state.point) refreshPoint();
      renderSidebar();
    });
  });
  document.querySelectorAll<HTMLButtonElement>('[data-mode]').forEach((button) => {
    button.addEventListener('click', () => {
      state.mode = button.dataset.mode as Mode;
      renderAll();
    });
  });
  document.querySelectorAll<HTMLButtonElement>('[data-color]').forEach((button) => {
    button.addEventListener('click', () => {
      state.colors.mode = button.dataset.color as 'terrain' | 'grayscale';
      renderAll();
    });
  });
  $('#zoomCommon').addEventListener('click', () => {
    const common = commonBounds();
    if (common) {
      state.bounds = fitBounds(canvasRect(), common);
      void scheduleView(0);
    }
  });
  $('#sharedRange').addEventListener('change', (event) => {
    state.colors.shared = (event.target as HTMLInputElement).checked;
    state.colors.elevationMin = null;
    state.colors.elevationMax = null;
    renderAll();
  });
  $('#minInput').addEventListener('change', (event) => {
    state.colors.elevationMin = Number((event.target as HTMLInputElement).value);
    renderAll();
  });
  $('#maxInput').addEventListener('change', (event) => {
    state.colors.elevationMax = Number((event.target as HTMLInputElement).value);
    renderAll();
  });
  $('#resetRange').addEventListener('click', () => {
    const [mn, mx] = sharedElevationRange(state.view);
    state.colors.elevationMin = mn;
    state.colors.elevationMax = mx;
    const stats = state.view?.header.difference.stats ?? null;
    if (stats?.valid_pixels) {
      state.colors.diffLimit = Math.max(Math.abs(stats.min ?? 0), Math.abs(stats.max ?? 0)) || 1;
    }
    renderAll();
  });
  $('#diffLimitInput').addEventListener('change', (event) => {
    state.colors.diffLimit = Math.abs(Number((event.target as HTMLInputElement).value)) || 1;
    renderAll();
  });
}

function canvasRect(): { width: number; height: number } {
  const rect = host?.getBoundingClientRect() ?? { width: 800, height: 600 };
  const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
  return {
    width: Math.max(64, Math.min(2048, Math.floor(rect.width * dpr))),
    height: Math.max(64, Math.min(2048, Math.floor(rect.height * dpr))),
  };
}

async function scheduleView(delay = 180): Promise<void> {
  if (!state.session?.a && !state.session?.b) return;
  if (viewTimer) window.clearTimeout(viewTimer);
  viewSeq += 1;
  const seq = viewSeq;
  const requestId = `view-${seq}-${crypto.randomUUID()}`;
  state.pendingRequestId = requestId;
  state.requestError = null;
  renderStatus();
  viewTimer = window.setTimeout(() => void requestView(requestId, seq), delay);
}

async function requestView(requestId: string, seq: number): Promise<void> {
  if (!state.bounds) return;
  const rect = canvasRect();
  viewAbort?.abort();
  const controller = new AbortController();
  viewAbort = controller;
  const sessionAtRequest = state.session;
  try {
    const grid = await fetchView({
      bandA: state.bandA,
      bandB: state.bandB,
      bounds: state.bounds,
      width: rect.width,
      height: rect.height,
      requestId,
      signal: controller.signal,
    });
    if (viewSeq !== seq || sessionAtRequest !== state.session) return;
    if (grid.header.request_id !== requestId) return;
    if (grid.header.a.file_id !== (sessionAtRequest?.a?.file_id ?? null) ||
        grid.header.b.file_id !== (sessionAtRequest?.b?.file_id ?? null)) return;
    state.view = grid;
    state.pendingRequestId = null;
    autoColorRanges(false);
    renderAll();
    if (state.point) updatePointDisplayValues();
  } catch (err) {
    if ((err as Error).name === 'AbortError' || viewSeq !== seq) return;
    state.requestError = err instanceof Error ? err.message : String(err);
    state.pendingRequestId = null;
    renderAll();
  }
}

function autoColorRanges(force = true): void {
  if (!state.view) return;
  const [mn, mx] = sharedElevationRange(state.view);
  if (force || state.colors.elevationMin === null || state.colors.elevationMax === null) {
    state.colors.elevationMin = mn;
    state.colors.elevationMax = mx;
  }
  const stats = state.view.header.difference.stats;
  if (stats?.valid_pixels && (force || state.colors.diffLimit === null)) {
    state.colors.diffLimit = Math.max(Math.abs(stats.min ?? 0), Math.abs(stats.max ?? 0)) || 1;
  }
  if (state.colors.diffLimit === null || state.colors.diffLimit === 0) state.colors.diffLimit = 1;
}

function renderGridImage(): HTMLCanvasElement | null {
  if (!state.view) return null;
  const h = state.view.header;
  const min = state.colors.elevationMin ?? 0;
  const max = state.colors.elevationMax ?? 1;
  if (state.mode === 'diff') {
    if (!h.difference.available) return null;
    return colorize(state.view.diff, h.width, h.height, 'diff', -Math.abs(state.colors.diffLimit ?? 1), Math.abs(state.colors.diffLimit ?? 1));
  }
  const side = h.arrays.a && state.view.a ? state.view.a : state.view.b;
  const arr = side === state.view.a ? state.view.a : state.view.b;
  return colorize(arr, h.width, h.height, state.colors.mode, min, max);
}

function renderMap(): void {
  if (!canvas || !host) return;
  const rect = canvasRect();
  canvas.width = rect.width;
  canvas.height = rect.height;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = '#05080d';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (!state.view) {
    drawDividerLabel();
    return;
  }
  const h = state.view.header;
  if (state.mode === 'swipe') {
    const min = state.colors.elevationMin ?? 0;
    const max = state.colors.elevationMax ?? 1;
    const imgA = colorize(state.view.a, h.width, h.height, state.colors.mode, min, max);
    const imgB = colorize(state.view.b, h.width, h.height, state.colors.mode, min, max);
    const split = Math.floor(state.divider * canvas.width);
    ctx.drawImage(imgA, 0, 0, split, canvas.height, 0, 0, split, canvas.height);
    ctx.drawImage(imgB, split, 0, canvas.width - split, canvas.height, split, 0, canvas.width - split, canvas.height);
    ctx.fillStyle = '#f8fafc';
    ctx.fillRect(split - 1, 0, 2, canvas.height);
  } else {
    const diffImage = renderGridImage();
    if (diffImage) ctx.drawImage(diffImage, 0, 0, canvas.width, canvas.height);
    else {
      ctx.font = '16px sans-serif';
      ctx.fillStyle = '#fca5a5';
      ctx.fillText(`差值不可计算：${h.difference.reason ?? h.comparison.reason ?? '未知原因'}`, 28, 42);
    }
  }
  drawPointMarker(ctx);
  drawDividerLabel();
}

function drawDividerLabel(): void {
  const label = $('#dividerLabel');
  if (!canvas) return;
  if (state.mode === 'diff') {
    label.textContent = state.view ? `差值 B − A · ±${fmt(state.colors.diffLimit, 2)}` : '差值视图';
  } else {
    label.textContent = `A | B 分隔线 ${Math.round(state.divider * 100)}%（拖动白线）`;
  }
}

function drawPointMarker(ctx: CanvasRenderingContext2D): void {
  if (!state.point || !state.bounds || !state.view) return;
  const h = state.view.header;
  if (!boundsContains({ west: h.bounds[0], south: h.bounds[1], east: h.bounds[2], north: h.bounds[3] }, state.point.lon, state.point.lat)) return;
  const [x, y] = pointToPixel({ west: h.bounds[0], south: h.bounds[1], east: h.bounds[2], north: h.bounds[3] }, h.width, h.height, state.point.lon, state.point.lat);
  ctx.save();
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.arc(x, y, 7, 0, Math.PI * 2);
  ctx.moveTo(x - 12, y); ctx.lineTo(x + 12, y);
  ctx.moveTo(x, y - 12); ctx.lineTo(x, y + 12);
  ctx.stroke();
  ctx.strokeStyle = '#111827';
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.restore();
}

function renderStatus(): void {
  const el = $('#statusOverlay');
  if (!el) return;
  const v = state.view;
  if (state.pendingRequestId) {
    el.innerHTML = `<span class="spinner"></span>正在等待新视图（波段/文件 ${state.busySlot ? state.busySlot.toUpperCase() : ''}）。旧画面不会用于解释新文件。`;
  } else if (state.requestError) {
    el.textContent = `视图请求失败：${state.requestError}`;
  } else if (v) {
    const ha = v.header.a;
    const hb = v.header.b;
    el.innerHTML = `已完成视图 · A <code>${ha.file_id ? ha.file_id.slice(0, 8) : '—'}</code> · B <code>${hb.file_id ? hb.file_id.slice(0, 8) : '—'}</code><br>
      ${ha.reason ? `A：${ha.reason}` : ''} ${hb.reason ? `B：${hb.reason}` : ''}`;
  } else {
    el.textContent = '上传至少一份 GeoTIFF 后读取当前范围。';
  }
}

function pointSideTable(title: string, side: PointResponse['a'] | null, displayValue: number | null): string {
  if (!side) return `<td><h3>${title}</h3><p class="muted">无数据。</p></td>`;
  return `<td>
    <h3>${title} ${side.filename ? `<span class="pill">${side.file_id?.slice(0, 8)}</span>` : ''}</h3>
    <table>
      <tr><th>来源文件</th><td>${side.filename ?? '—'}</td></tr>
      <tr><th>原始行列</th><td class="num">row ${side.row ?? '—'}, col ${side.col ?? '—'}<br><span class="small">连续坐标 row ${fmt(side.row_float, 3)}, col ${fmt(side.col_float, 3)}</span></td></tr>
      <tr><th>投影坐标</th><td class="num">x ${fmt(side.native_x, 3)}<br>y ${fmt(side.native_y, 3)}</td></tr>
      <tr><th>原始值</th><td class="num">${side.raw_value ?? '—'} <span class="small">(${side.available ? side.band : '—'})</span></td></tr>
      <tr><th>标定值</th><td class="num">${side.scaled_value === null ? '—' : fmt(side.scaled_value, 4)} ${side.units ?? ''}</td></tr>
      <tr><th>scale/offset</th><td>${fmt(side.scale, 6)} / ${fmt(side.offset, 6)}</td></tr>
      <tr><th>无数据</th><td>${side.is_nodata === null ? '—' : side.is_nodata ? `是（${side.nodata ?? 'mask'}）` : '否'}</td></tr>
      <tr><th>显示重采样值</th><td class="num">${displayValue === null ? '—' : fmt(displayValue, 4)} <span class="small">bilinear，仅用于当前画面</span></td></tr>
      <tr><th>状态</th><td>${side.reason ?? (side.available ? '可读取' : '不可用')}</td></tr>
    </table>
  </td>`;
}

function updatePointDisplayValues(): void {
  renderInfo();
}

function renderInfo(): void {
  const p = state.point;
  const d = state.pointData;
  let displayA: number | null = null;
  let displayB: number | null = null;
  if (p && state.view && state.bounds) {
    const h = state.view.header;
    const bb = { west: h.bounds[0], south: h.bounds[1], east: h.bounds[2], north: h.bounds[3] };
    if (boundsContains(bb, p.lon, p.lat)) {
      const [x, y] = pointToPixel(bb, h.width, h.height, p.lon, p.lat);
      displayA = gridValue(state.view.a, h.width, h.height, x, y);
      displayB = gridValue(state.view.b, h.width, h.height, x, y);
    }
  }
  const comparable = d?.both_valid && d.difference !== null;
  $('#infoPanel').innerHTML = `
    <div class="point-grid">
      <div>
        <h2 style="margin-top:0">选点（地理坐标固定）</h2>
        ${p ? `<dl><dt>经度</dt><dd>${fmt(p.lon, 8)}</dd><dt>纬度</dt><dd>${fmt(p.lat, 8)}</dd></dl>` : '<p class="muted">在影像上单击一个位置。平移、缩放或改色后，它仍表示该经纬度。</p>'}
        <div class="row"><button id="clearPoint" ${p ? '' : 'disabled'}>清除选点</button></div>
        <div class="banner ${comparable ? 'ok' : 'warn'}">${state.pointLoading ? '正在读取原始像元...' : d?.warning ?? (comparable ? '双方有效，可比较。' : '尚未得到可比较读数。')}</div>
        ${d ? `<dl><dt>选点差值</dt><dd class="num">${d.difference === null ? '不计算' : `${fmt(d.difference, 4)} ${d.difference_units ?? ''}`}</dd></dl>` : ''}
        ${state.pointError ? `<div class="banner error">${state.pointError}</div>` : ''}
      </div>
      ${pointSideTable('A 原始文件', d?.a ?? null, displayA)}
      ${pointSideTable('B 原始文件', d?.b ?? null, displayB)}
    </div>`;
  $('#clearPoint').addEventListener('click', () => {
    state.point = null;
    state.pointData = null;
    state.pointError = null;
    renderAll();
  });
}

function renderAll(): void {
  renderSidebar();
  renderMap();
  renderStatus();
  renderInfo();
}

async function refreshPoint(): Promise<void> {
  if (!state.point) return;
  pointAbort?.abort();
  const controller = new AbortController();
  pointAbort = controller;
  state.pointLoading = true;
  state.pointError = null;
  renderInfo();
  try {
    state.pointData = await queryPoint({
      lon: state.point.lon,
      lat: state.point.lat,
      bandA: state.bandA,
      bandB: state.bandB,
      signal: controller.signal,
    });
    renderInfo();
  } catch (err) {
    if ((err as Error).name !== 'AbortError') state.pointError = err instanceof Error ? err.message : String(err);
  } finally {
    state.pointLoading = false;
    renderInfo();
  }
}

function bindMapEvents(): void {
  if (!canvas || !host) return;
  const mapCanvas = canvas;
  let drag: { x: number; y: number; bounds: Bounds } | null = null;
  let moved = false;

  mapCanvas.addEventListener('pointerdown', (event) => {
    const rect = mapCanvas.getBoundingClientRect();
    moved = false;
    const ratio = event.offsetX / rect.width;
    if (state.mode === 'swipe' && Math.abs(ratio - state.divider) < 0.018) {
      draggingDivider = true;
      mapCanvas.setPointerCapture(event.pointerId);
      return;
    }
    if (state.bounds) drag = { x: event.offsetX, y: event.offsetY, bounds: { ...state.bounds } };
    mapCanvas.setPointerCapture(event.pointerId);
  });

  mapCanvas.addEventListener('pointermove', (event) => {
    const rect = mapCanvas.getBoundingClientRect();
    updateCoords(event.offsetX, event.offsetY, rect.width, rect.height);
    if (draggingDivider) {
      state.divider = Math.min(0.98, Math.max(0.02, event.offsetX / rect.width));
      renderMap();
      return;
    }
    if (drag && state.bounds) {
      moved = true;
      const dx = event.offsetX - drag.x;
      const dy = event.offsetY - drag.y;
      const dLon = drag.bounds.east - drag.bounds.west;
      const dLat = drag.bounds.north - drag.bounds.south;
      state.bounds = {
        west: drag.bounds.west - (dx / rect.width) * dLon,
        east: drag.bounds.east - (dx / rect.width) * dLon,
        south: drag.bounds.south + (dy / rect.height) * dLat,
        north: drag.bounds.north + (dy / rect.height) * dLat,
      };
      void scheduleView(180);
    }
  });

  mapCanvas.addEventListener('pointerup', (event) => {
    const rect = mapCanvas.getBoundingClientRect();
    draggingDivider = false;
    if (drag && !moved) {
      void selectAt(event.offsetX, event.offsetY, rect.width, rect.height);
    }
    drag = null;
    if (!moved) return;
    void scheduleView(0);
  });

  mapCanvas.addEventListener('wheel', (event) => {
    event.preventDefault();
    if (!state.bounds) return;
    const rect = mapCanvas.getBoundingClientRect();
    const factor = event.deltaY > 0 ? 1.25 : 0.8;
    const [lon, lat] = pixelToLonLat(state.bounds, rect.width, rect.height, event.offsetX, event.offsetY);
    zoomAt(lon, lat, factor);
  }, { passive: false });

  mapCanvas.addEventListener('dblclick', (event) => {
    const rect = mapCanvas.getBoundingClientRect();
    if (!state.bounds) return;
    const [lon, lat] = pixelToLonLat(state.bounds, rect.width, rect.height, event.offsetX, event.offsetY);
    zoomAt(lon, lat, 0.6);
  });

  window.addEventListener('resize', () => {
    renderAll();
    void scheduleView(120);
  });
}

function updateCoords(x: number, y: number, width: number, height: number): void {
  const el = $('#coordsOverlay');
  if (!state.bounds) {
    el.textContent = '经度 —，纬度 —';
    return;
  }
  const [lon, lat] = pixelToLonLat(state.bounds, width, height, x, y);
  el.textContent = `经度 ${lon.toFixed(7)}，纬度 ${lat.toFixed(7)}`;
}

function zoomAt(lon: number, lat: number, factor: number): void {
  if (!state.bounds) return;
  const b = state.bounds;
  state.bounds = {
    west: lon - (lon - b.west) * factor,
    east: lon + (b.east - lon) * factor,
    south: lat - (lat - b.south) * factor,
    north: lat + (b.north - lat) * factor,
  };
  void scheduleView(120);
}

async function selectAt(x: number, y: number, width: number, height: number): Promise<void> {
  if (!state.bounds) return;
  const [lon, lat] = pixelToLonLat(state.bounds, width, height, x, y);
  state.point = { lon, lat };
  renderMap();
  await refreshPoint();
}

async function init(): Promise<void> {
  initDom();
  renderAll();
  try {
    state.session = await getSession();
  } catch {
    try {
      state.session = await createSession();
    } catch (err) {
      state.uploadError = `无法建立本地检查会话：${err instanceof Error ? err.message : String(err)}`;
    }
  }
  syncBandsFromSession();
  autoColorRanges(true);
  renderAll();
}

void init();
