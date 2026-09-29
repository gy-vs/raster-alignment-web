import type { AppState } from "./state";
import type {
  BandInfo, Comparison, DatasetMetadata, PointSlotInfo, SlotSummary,
} from "./types";

const $ = (sel: string) => document.querySelector(sel);

function el(tag: string, cls = "", html = "") {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html) e.innerHTML = html;
  return e;
}

function fmt(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  if (Math.abs(v) >= 100000) return v.toFixed(1);
  if (Math.abs(v) >= 100) return v.toFixed(digits);
  return v.toFixed(Math.max(digits, 4));
}

function bandUnitLabel(b: BandInfo): string {
  if (b.unit) {
    const src = b.unit_source === "band_tag" ? "波段标记" : "CRS 单位";
    return `${b.unit}（${src}）`;
  }
  return "未声明";
}

export function renderSlotPanel(
  state: AppState,
  slot: "a" | "b",
  container: HTMLElement,
) {
  const summary: SlotSummary | null = state.state?.slots[slot] ?? null;
  container.innerHTML = "";
  const title = slot.toUpperCase();

  const head = el("div", "slot-head");
  head.appendChild(el("span", "slot-tag " + slot, title));

  const fileLabel = summary
    ? `${summary.filename} · ${summary.metadata.driver}`
    : "尚未打开文件";
  head.appendChild(el("span", "slot-file", fileLabel));
  container.appendChild(head);

  // upload controls
  const upRow = el("div", "row");
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".tif,.tiff,.vrt,image/tiff";
  input.hidden = true;
  const btn = el("button", "btn", "选择 GeoTIFF…");
  btn.addEventListener("click", () => input.click());
  input.addEventListener("change", () => {
    const f = input.files?.[0];
    if (f) void state.upload(slot, f);
    input.value = "";
  });
  upRow.appendChild(btn);
  upRow.appendChild(input);
  if (summary) {
    const rm = el("button", "btn btn-ghost", "移除");
    rm.addEventListener("click", () => state.removeSlot(slot));
    upRow.appendChild(rm);
  }
  if (state.busy[slot]) upRow.appendChild(el("span", "muted", "读取中…"));
  container.appendChild(upRow);

  if (state.uploadError[slot]) {
    container.appendChild(
      el("div", "error-box", `无法使用：${state.uploadError[slot]}`),
    );
  }

  if (!summary) return;
  const m = summary.metadata;

  // band selector
  const bandRow = el("div", "row band-row");
  bandRow.appendChild(el("label", "muted", "参与比较的波段"));
  const sel = document.createElement("select");
  m.bands.forEach((b) => {
    const o = document.createElement("option");
    o.value = String(b.index);
    o.textContent = `${b.index}. ${b.description}`;
    if (b.index === summary.band) o.selected = true;
    sel.appendChild(o);
  });
  sel.addEventListener("change", () =>
    state.selectBand(slot, Number(sel.value)));
  bandRow.appendChild(sel);
  container.appendChild(bandRow);

  container.appendChild(metaTable(m, summary.band));
}

function metaTable(m: DatasetMetadata, bandIdx: number) {
  const b = m.bands[bandIdx - 1];
  const wrap = el("div", "meta");
  const rows: [string, string][] = [
    ["坐标参考",
      `${m.crs.kind === "geographic" ? "地理坐标" : "投影坐标"}` +
      (m.crs.epsg ? ` EPSG:${m.crs.epsg}` : ` ${m.crs.name ?? "自定义"}`)],
    ["范围（原 CRS）",
      `${fmt(m.bounds_native.left, 1)}, ${fmt(m.bounds_native.bottom, 1)} →` +
      ` ${fmt(m.bounds_native.right, 1)}, ${fmt(m.bounds_native.top, 1)}`],
    ["范围（WGS84）",
      `${m.bounds_wgs84.left.toFixed(5)}, ${m.bounds_wgs84.bottom.toFixed(5)} →` +
      ` ${m.bounds_wgs84.right.toFixed(5)}, ${m.bounds_wgs84.top.toFixed(5)}`],
    ["尺寸（列 × 行）", `${m.width} × ${m.height}`],
    ["像元大小（原单位）",
      `${fmt(m.resolution_native.x, 4)} × ${fmt(m.resolution_native.y, 4)}`],
    ["像元大小（米）",
      m.resolution_metres
        ? `${fmt(m.resolution_metres.x, 2)} × ${fmt(m.resolution_metres.y, 2)}`
        : `—（地理坐标系，约 ${(m.resolution_native.x * 111320).toFixed(1)} m/度）`],
    ["波段数", String(m.band_count)],
    ["当前波段数据类型", b.dtype],
    ["存储 scale / offset", `${b.scale} / ${b.offset}`],
    ["无数据标记", b.nodata === null ? "无" : String(b.nodata)],
    ["数值单位", bandUnitLabel(b)],
    ["金字塔概览", m.tiled ? "分块存储" : "条带存储"],
  ];
  const table = el("table", "meta-table");
  rows.forEach(([k, v]) => {
    const tr = el("tr");
    tr.appendChild(el("td", "k", k));
    tr.appendChild(el("td", "v", v));
    table.appendChild(tr);
  });
  wrap.appendChild(table);

  if (b.sample_stats.sampled && b.sample_stats.count > 0) {
    wrap.appendChild(el("div", "muted small",
      `抽样存储值范围（仅用于初始配色）：` +
      `${fmt(b.sample_stats.min, 2)} … ${fmt(b.sample_stats.max, 2)}`));
  }
  return wrap;
}

export function renderComparison(comp: Comparison, container: HTMLElement) {
  container.innerHTML = "";
  const box = el("div", comp.comparable ? "comp ok" : "comp blocked");
  box.appendChild(el("div", "comp-title",
    comp.comparable ? "可以进行数值比较" : "当前不能生成差值"));
  if (comp.comparable) {
    box.appendChild(el("div", "small",
      `差值单位：${comp.difference_unit ?? "（未声明单位，按原值）"}` +
      (Math.abs((comp.unit_conversion_a_to_b ?? 1) - 1) > 1e-12
        ? `；A→B 换算系数 ${(comp.unit_conversion_a_to_b ?? 1).toFixed(6)}`
        : "")));
  }
  for (const is2 of comp.blockers) {
    box.appendChild(el("div", "issue block", `⛔ ${is2.message}`));
  }
  for (const w of comp.warnings) {
    box.appendChild(el("div", "issue warn", `⚠️ ${w.message}`));
  }
  if (comp.overlap_wgs84) {
    const o = comp.overlap_wgs84;
    box.appendChild(el("div", "small muted",
      `共同覆盖区 WGS84：${o.left.toFixed(5)}, ${o.bottom.toFixed(5)} →` +
      ` ${o.right.toFixed(5)}, ${o.top.toFixed(5)}`));
  }
  container.appendChild(box);
}

export function renderPointPanel(state: AppState, container: HTMLElement) {
  container.innerHTML = "";
  const mk = state.marker;
  const head = el("div", "point-head");
  head.appendChild(el("strong", "", "选点核对"));
  if (mk) {
    const clear = el("button", "btn btn-ghost small-btn", "清除");
    clear.addEventListener("click", () => state.clearMarker());
    head.appendChild(clear);
  }
  container.appendChild(head);

  if (!mk) {
    container.appendChild(el(
      "div", "muted",
      "在图上单击任一位置：选点以经纬度固定，平移/缩放/换波段后仍指向同一地点。",
    ));
    return;
  }
  container.appendChild(el(
    "div", "small mono", `lon ${mk.lon.toFixed(7)}  lat ${mk.lat.toFixed(7)}`,
  ));
  if (mk.loading) {
    container.appendChild(el("div", "muted", "正在读取原始像元…"));
    return;
  }
  if (mk.error) {
    container.appendChild(el("div", "error-box", mk.error));
    return;
  }
  const p = mk.point!;

  // comparability banner for THIS exact screen/geographic point
  const banner = p.screen_position_comparable
    ? el("div", "issue ok", "✔ 两份文件在此点都有可比较的测量值")
    : el("div", "issue warn",
      "✖ 此点当前不可比较（至少一份文件缺测或在范围外），不产生点差值");
  container.appendChild(banner);

  (["a", "b"] as const).forEach((slot) => {
    container.appendChild(pointSlotCard(slot, p.slots[slot]));
  });

  if (p.point_difference) {
    const d = p.point_difference;
    const box = el("div", "diff-box");
    box.appendChild(el("div", "",
      `点差值（源像元标定值，A − B）：${fmt(d.a_minus_b, 4)} ${d.unit ?? ""}`));
    box.appendChild(el("div", "small muted",
      `A（归一后）${fmt(d.a_value_normalized, 3)} − B ${fmt(d.b_value, 3)}`));
    container.appendChild(box);
  }
}

function pointSlotCard(slot: "a" | "b", info: PointSlotInfo) {
  const card = el("div", `point-card ${slot}`);
  card.appendChild(el("div", "point-card-title", slot.toUpperCase()));
  if (!info.available) {
    card.appendChild(el("div", "muted", "该槽位没有文件"));
    return card;
  }
  card.appendChild(el("div", "small", info.filename ?? ""));
  if (!info.inside_bounds) {
    card.appendChild(el("div", "issue warn", "位置在文件范围外"));
    return card;
  }
  const rows: [string, string][] = [
    ["行, 列（整数像元）", `row ${info.row}, col ${info.col}`],
    ["像元内位置", `fr ${fmt(info.fractional_row, 3)}, ${fmt(info.fractional_col, 3)}`],
    ["原 CRS 坐标", `${fmt(info.native_x, 2)}, ${fmt(info.native_y, 2)}`],
    ["原始值（存储读数）",
      info.is_nodata ? `<无数据 ${String(info.nodata_value)}>` : fmt(info.raw_value, 4)],
    ["数据类型", info.raw_dtype ?? "—"],
    ["scale × raw + offset",
      `${info.scale} × ${fmt(info.raw_value, 3)} + ${info.offset}`],
    ["标定后值",
      info.is_nodata ? "缺测" : `${fmt(info.calibrated_value, 4)}`],
    ["像元尺寸（原单位）",
      info.pixel_size_native
        ? `${fmt(info.pixel_size_native.x, 3)} × ${fmt(info.pixel_size_native.y, 3)}`
        : "—"],
  ];
  const table = el("table", "meta-table");
  rows.forEach(([k, v]) => {
    const tr = el("tr");
    tr.appendChild(el("td", "k", k));
    tr.appendChild(el("td", "v mono", v));
    table.appendChild(tr);
  });
  card.appendChild(table);
  card.appendChild(el("div", "small muted",
    "以上为源文件该行列的读数；画布上显示的是重采样网格，两者来源不同。"));
  return card;
}

export function bindUi(state: AppState) {
  const modeSwipe = $("#mode-swipe") as HTMLButtonElement;
  const modeDiff = $("#mode-diff") as HTMLButtonElement;
  const resampling = $("#resampling") as HTMLSelectElement;
  const rampA = $("#ramp-a") as HTMLSelectElement;
  const rampB = $("#ramp-b") as HTMLSelectElement;
  const opacity = $("#opacity") as HTMLInputElement;
  const diffScale = $("#diff-scale") as HTMLInputElement;
  const coverage = $("#show-coverage") as HTMLInputElement;

  modeSwipe.addEventListener("click", () => state.setMode("swipe"));
  modeDiff.addEventListener("click", () => state.setMode("diff"));
  resampling.addEventListener("change", () => {
    state.setResampling(resampling.value as AppState["resampling"]);
  });
  rampA.addEventListener("change", () =>
    state.updateColors({ rampA: rampA.value as never }));
  rampB.addEventListener("change", () =>
    state.updateColors({ rampB: rampB.value as never }));
  opacity.addEventListener("input", () =>
    state.updateColors({ opacity: Number(opacity.value) }));
  diffScale.addEventListener("change", () => {
    const v = Number(diffScale.value);
    state.updateColors({ diffAbsMax: v > 0 ? v : null });
  });
  coverage.addEventListener("change", () =>
    state.updateColors({ showCoverage: coverage.checked }));
}

export function syncControls(state: AppState) {
  const modeSwipe = $("#mode-swipe");
  const modeDiff = $("#mode-diff");
  modeSwipe?.classList.toggle("active", state.mode === "swipe");
  modeDiff?.classList.toggle("active", state.mode === "diff");

  const comp = state.comparison;
  if (modeDiff) {
    modeDiff.classList.toggle("disabled-btn", !(comp?.comparable ?? false));
    modeDiff.setAttribute(
      "title",
      comp?.comparable ? "" : "两份数据当前不可数值比较",
    );
  }

  const ds = $("#diff-stats");
  if (ds && state.view?.header.diff_stats) {
    const st = state.view.header.diff_stats;
    ds.textContent =
      `共同像元 ${st.count}　min ${fmt(st.min, 3)}　max ${fmt(st.max, 3)}` +
      `　均值 ${fmt(st.mean, 3)}　RMSE ${fmt(st.rmse, 3)}` +
      ` (${state.view.header.diff_unit ?? ""})`;
  } else if (ds) {
    ds.textContent = "";
  }
}
