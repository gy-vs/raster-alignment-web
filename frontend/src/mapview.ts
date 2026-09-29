import {
  percentileRange,
  rasterizeDiverging,
  rasterizeMaskHatch,
  rasterizeSequential,
  type RampName,
} from "./colormap";
import {
  lonLatToMerc, mercToLonLat, panBounds, zoomAround,
} from "./projection";
import type { AppState } from "./state";
import type { DecodedView } from "./types";

/**
 * Renders both server-computed grids into ONE aligned viewport. The two
 * layers share identical bounds/transform, so geographic alignment is
 * guaranteed by construction rather than by eyeballing similar colors.
 */
export class MapView {
  private canvas: HTMLCanvasElement;
  private state: AppState;
  private ctx: CanvasRenderingContext2D;
  private layerCanvases: Record<string, HTMLCanvasElement> = {};
  private drag: { x: number; y: number; moved: boolean } | null = null;
  private dividerDrag = false;
  private cssWidth = 0;
  private cssHeight = 0;
  private resizeObserver: ResizeObserver;
  private unsub: () => void;
  private rafPending = false;
  private mouseLonLat: { lon: number; lat: number } | null = null;

  constructor(canvas: HTMLCanvasElement, state: AppState) {
    this.canvas = canvas;
    this.state = state;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("无法创建 2D 画布");
    this.ctx = ctx;

    this.unsub = state.subscribe(() => this.onStateChange());

    this.resizeObserver = new ResizeObserver(() => this.handleResize());
    this.resizeObserver.observe(canvas.parentElement!);
    this.handleResize();

    canvas.addEventListener("pointerdown", this.onPointerDown);
    window.addEventListener("pointermove", this.onPointerMove);
    window.addEventListener("pointerup", this.onPointerUp);
    canvas.addEventListener("wheel", this.onWheel, { passive: false });
    canvas.addEventListener("pointerleave", () => {
      this.mouseLonLat = null;
      this.scheduleRender();
    });
  }

  destroy() {
    this.unsub();
    this.resizeObserver.disconnect();
    this.canvas.removeEventListener("pointerdown", this.onPointerDown);
    window.removeEventListener("pointermove", this.onPointerMove);
    window.removeEventListener("pointerup", this.onPointerUp);
    this.canvas.removeEventListener("wheel", this.onWheel);
  }

  /** State mutations that change the numbers trigger a refetch; color
   *  changes only repaint. */
  private onStateChange() {
    if (this.state.viewDirty) this.requestViewIfReady();
    this.scheduleRender();
  }

  private handleResize() {
    const parent = this.canvas.parentElement!;
    const dpr = window.devicePixelRatio || 1;
    this.cssWidth = parent.clientWidth;
    this.cssHeight = parent.clientHeight;
    this.canvas.width = Math.max(1, Math.round(this.cssWidth * dpr));
    this.canvas.height = Math.max(1, Math.round(this.cssHeight * dpr));
    this.canvas.style.width = `${this.cssWidth}px`;
    this.canvas.style.height = `${this.cssHeight}px`;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.onStateChange();
  }

  private requestViewIfReady() {
    if (this.cssWidth < 2 || this.cssHeight < 2) return;
    const s = this.state;
    if (!s.sessionId || !s.bounds) return;
    if (!s.state?.slots.a && !s.state?.slots.b) return;
    const w = Math.min(900, Math.round(this.cssWidth));
    const h = Math.min(900, Math.round(this.cssHeight));
    // Continuous pan/zoom fires many state updates; state.scheduleView
    // coalesces them so the server is not asked to re-read the whole view
    // on every mouse event. The "waiting new view" overlay still shows.
    s.scheduleView(w, h);
  }

  private scheduleRender() {
    if (this.rafPending) return;
    this.rafPending = true;
    requestAnimationFrame(() => {
      this.rafPending = false;
      this.render();
    });
  }

  // ---- coordinate mapping -------------------------------------------
  private pixelToMerc(px: number, py: number): [number, number] {
    const b = this.state.bounds!;
    const x = b.left + (px / this.cssWidth) * (b.right - b.left);
    const y = b.top - (py / this.cssHeight) * (b.top - b.bottom);
    return [x, y];
  }

  private mercToPixel(mx: number, my: number): [number, number] {
    const b = this.state.bounds!;
    const px = ((mx - b.left) / (b.right - b.left)) * this.cssWidth;
    const py = ((b.top - my) / (b.top - b.bottom)) * this.cssHeight;
    return [px, py];
  }

  // ---- interaction ---------------------------------------------------
  private onPointerDown = (e: PointerEvent) => {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    if (
      this.state.mode === "swipe" &&
      Math.abs(x - this.state.divider * this.cssWidth) < 8
    ) {
      this.dividerDrag = true;
      this.canvas.setPointerCapture(e.pointerId);
      return;
    }
    this.drag = { x, y: e.clientY - rect.top, moved: false };
    this.canvas.setPointerCapture(e.pointerId);
  };

  private onPointerMove = (e: PointerEvent) => {
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    if (this.dividerDrag) {
      this.state.divider = Math.max(0, Math.min(1, x / this.cssWidth));
      this.scheduleRender();
      return;
    }
    if (this.drag && this.state.bounds) {
      const dx = x - this.drag.x;
      const dy = y - this.drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 2) this.drag.moved = true;
      this.state.setBounds(panBounds(
        this.state.bounds, dx, dy, this.cssWidth, this.cssHeight,
      ));
      this.drag.x = x;
      this.drag.y = y;
    }
    if (this.state.bounds && x >= 0 && x <= this.cssWidth &&
        y >= 0 && y <= this.cssHeight) {
      const [mx, my] = this.pixelToMerc(x, y);
      const [lon, lat] = mercToLonLat(mx, my);
      this.mouseLonLat = { lon, lat };
      this.scheduleRender();
    }
  };

  private onPointerUp = (e: PointerEvent) => {
    const wasDrag = this.drag;
    this.dividerDrag = false;
    this.drag = null;
    if (wasDrag && !wasDrag.moved && this.state.bounds) {
      const rect = this.canvas.getBoundingClientRect();
      const [mx, my] = this.pixelToMerc(
        e.clientX - rect.left, e.clientY - rect.top,
      );
      void this.state.setMarkerByMerc(mx, my);
    }
  };

  private onWheel = (e: WheelEvent) => {
    if (!this.state.bounds) return;
    e.preventDefault();
    const rect = this.canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.35 : 1 / 1.35;
    this.state.setBounds(zoomAround(
      this.state.bounds, factor,
      x / this.cssWidth, y / this.cssHeight,
    ));
  };

  // ---- rendering -----------------------------------------------------
  private buildLayerCanvas(
    decoded: DecodedView, name: string, ramp: RampName,
    vmin: number, vmax: number, opacity: number,
  ): HTMLCanvasElement {
    const layer = decoded.layers[name];
    const { width, height } = decoded.header;
    const img = rasterizeSequential(
      layer.values, layer.mask, width, height,
      vmin, vmax, ramp, opacity,
    );
    let c = this.layerCanvases[name];
    if (!c || c.width !== width || c.height !== height) {
      c = document.createElement("canvas");
      c.width = width;
      c.height = height;
      this.layerCanvases[name] = c;
    }
    c.getContext("2d")!.putImageData(img, 0, 0);
    return c;
  }

  private render() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.cssWidth, this.cssHeight);
    ctx.fillStyle = "#0c0f14";
    ctx.fillRect(0, 0, this.cssWidth, this.cssHeight);

    const decoded = this.state.view;
    if (decoded) {
      if (this.state.mode === "diff") this.renderDiff(decoded);
      else this.renderSwipe(decoded);
    }
    this.renderMarker();
    this.renderDivider();
    this.renderOverlays();
  }

  private renderSwipe(decoded: DecodedView) {
    const s = this.state;
    const layers = decoded.layers;
    const split = s.divider * this.cssWidth;

    const drawSide = (name: "a" | "b", ramp: RampName) => {
      const layer = layers[name];
      if (!layer) return;
      const [vmin, vmax] =
        s.colors.manualMin !== null && s.colors.manualMax !== null
          ? [s.colors.manualMin, s.colors.manualMax]
          : percentileRange(layer.values, layer.mask);
      const c = this.buildLayerCanvas(
        decoded, name, ramp, vmin, vmax, s.colors.opacity,
      );
      this.ctx.imageSmoothingEnabled = true;
      this.ctx.drawImage(c, 0, 0, this.cssWidth, this.cssHeight);
      // nodata / out-of-raster hatch within this layer's half
      const hatch = rasterizeMaskHatch(
        layer.mask, decoded.header.width, decoded.header.height,
      );
      this.ctx.imageSmoothingEnabled = false;
      this.ctx.globalAlpha = 0.8;
      this.ctx.drawImage(hatch, 0, 0, this.cssWidth, this.cssHeight);
      this.ctx.globalAlpha = 1;
    };

    if (layers.b) {
      this.ctx.save();
      this.ctx.beginPath();
      this.ctx.rect(0, 0, split, this.cssHeight);
      this.ctx.clip();
      drawSide("b", s.colors.rampB);
      this.ctx.restore();
    }
    if (layers.a) {
      this.ctx.save();
      this.ctx.beginPath();
      this.ctx.rect(split, 0, this.cssWidth - split, this.cssHeight);
      this.ctx.clip();
      drawSide("a", s.colors.rampA);
      this.ctx.restore();
    }

    // labels
    this.ctx.fillStyle = "rgba(0,0,0,0.45)";
    this.ctx.font = "12px sans-serif";
    if (layers.b) {
      this.ctx.fillRect(6, this.cssHeight - 26, 22, 18);
      this.ctx.fillStyle = "#fff";
      this.ctx.fillText("B", 13, this.cssHeight - 13);
    }
    if (layers.a) {
      this.ctx.fillStyle = "rgba(0,0,0,0.45)";
      this.ctx.fillRect(this.cssWidth - 28, this.cssHeight - 26, 22, 18);
      this.ctx.fillStyle = "#fff";
      this.ctx.fillText("A", this.cssWidth - 21, this.cssHeight - 13);
    }
  }

  private renderDiff(decoded: DecodedView) {
    const s = this.state;
    const diff = decoded.layers.diff;
    if (!diff) return;
    let absMax = s.colors.diffAbsMax;
    if (absMax === null || absMax <= 0) {
      let m = 0;
      for (let i = 0; i < diff.values.length; i++) {
        if (diff.mask[i] && Number.isFinite(diff.values[i])) {
          m = Math.max(m, Math.abs(diff.values[i]));
        }
      }
      absMax = m || 1;
    }
    const img = rasterizeDiverging(
      diff.values, diff.mask,
      decoded.header.width, decoded.header.height,
      absMax, absMax,
      s.colors.showCoverage ? (diff.onlyA ?? null) : null,
      s.colors.showCoverage ? (diff.onlyB ?? null) : null,
    );
    let c = this.layerCanvases["diff"];
    if (!c || c.width !== decoded.header.width ||
        c.height !== decoded.header.height) {
      c = document.createElement("canvas");
      c.width = decoded.header.width;
      c.height = decoded.header.height;
      this.layerCanvases["diff"] = c;
    }
    c.getContext("2d")!.putImageData(img, 0, 0);
    this.ctx.imageSmoothingEnabled = false;
    this.ctx.drawImage(c, 0, 0, this.cssWidth, this.cssHeight);
  }

  private renderMarker() {
    const s = this.state;
    if (!s.marker || !s.bounds) return;
    const [mx, my] = lonLatToMerc(s.marker.lon, s.marker.lat);
    const b = s.bounds;
    if (mx < b.left || mx > b.right || my < b.bottom || my > b.top) return;
    const [px, py] = this.mercToPixel(mx, my);
    const comparable = s.marker.point?.screen_position_comparable;
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = "rgba(0,0,0,0.65)";
    ctx.lineWidth = 3.5;
    ctx.beginPath();
    ctx.moveTo(px - 9, py); ctx.lineTo(px + 9, py);
    ctx.moveTo(px, py - 9); ctx.lineTo(px, py + 9);
    ctx.stroke();
    ctx.strokeStyle = comparable ? "#ffe238" : "#ff7a5c";
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(px - 9, py); ctx.lineTo(px + 9, py);
    ctx.moveTo(px, py - 9); ctx.lineTo(px, py + 9);
    ctx.stroke();
    ctx.restore();
  }

  private renderDivider() {
    if (this.state.mode !== "swipe") return;
    const x = this.state.divider * this.cssWidth;
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = "rgba(255,255,255,0.9)";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 4]);
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, this.cssHeight);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(255,255,255,0.95)";
    ctx.beginPath();
    ctx.arc(x, this.cssHeight / 2, 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
  }

  private renderOverlays() {
    const s = this.state;
    const ctx = this.ctx;
    const stale = s.status.loading &&
      s.status.generation !== s.status.completedGeneration;
    if (stale) {
      ctx.fillStyle = "rgba(8,12,18,0.45)";
      ctx.fillRect(0, 0, this.cssWidth, this.cssHeight);
      ctx.fillStyle = "#cfe3ff";
      ctx.font = "13px sans-serif";
      ctx.fillText("正在计算新视图…（当前画面仍是已完成的旧视图）", 14, 22);
    }
    if (s.status.error) {
      ctx.fillStyle = "rgba(60,10,8,0.82)";
      ctx.fillRect(10, 34, Math.min(this.cssWidth - 20, 540), 58);
      ctx.fillStyle = "#ffb3a8";
      ctx.font = "13px sans-serif";
      this.wrapText(
        ctx, s.status.error, 20, 54, Math.min(this.cssWidth - 40, 520), 16,
      );
    }
    if (this.mouseLonLat) {
      ctx.fillStyle = "rgba(0,0,0,0.55)";
      ctx.fillRect(this.cssWidth - 248, 8, 240, 22);
      ctx.fillStyle = "#d8e6f5";
      ctx.font = "12px monospace";
      ctx.fillText(
        `lon ${this.mouseLonLat.lon.toFixed(6)}  lat ${this.mouseLonLat.lat.toFixed(6)}`,
        this.cssWidth - 242, 23,
      );
    }
  }

  private wrapText(
    ctx: CanvasRenderingContext2D, text: string,
    x: number, y: number, maxW: number, lh: number,
  ) {
    let line = "";
    let yy = y;
    for (const ch of text) {
      const test = line + ch;
      if (ctx.measureText(test).width > maxW && line) {
        ctx.fillText(line, x, yy);
        line = ch;
        yy += lh;
      } else {
        line = test;
      }
    }
    ctx.fillText(line, x, yy);
  }
}
