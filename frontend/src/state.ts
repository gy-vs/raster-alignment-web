import { api, ApiError } from "./api";
import {
  lonLatToMerc, mercToLonLat, wgs84ToMercBounds,
  type BBox,
} from "./projection";
import type {
  Comparison, DecodedView, PointResponse, SessionState,
} from "./types";

export type Mode = "swipe" | "diff";
export type Resampling = "bilinear" | "nearest" | "average" | "cubic";

export interface Marker {
  // Geographic identity: fixed WGS84 lon/lat. Survives pan/zoom/band/file
  // swaps; it never follows canvas pixel indices.
  lon: number;
  lat: number;
  point: PointResponse | null;
  loading: boolean;
  error: string | null;
}

export interface ViewStatus {
  loading: boolean;
  error: string | null;
  // Monotonic generation: lets us distinguish "new view being awaited"
  // from "old completed view" and never explain old diff as new data.
  generation: number;
  completedGeneration: number;
}

export interface ColorSettings {
  rampA: "terrain" | "grayscale" | "viridis";
  rampB: "terrain" | "grayscale" | "viridis";
  opacity: number;
  // When null, ramps auto-scale to each view's 2-98% percentiles.
  manualMin: number | null;
  manualMax: number | null;
  diffAbsMax: number | null;
  showCoverage: boolean;
}

export class AppState {
  sessionId: string | null = null;
  state: SessionState | null = null;
  mode: Mode = "swipe";
  resampling: Resampling = "bilinear";
  bounds: BBox | null = null;
  divider = 0.5;
  view: DecodedView | null = null;
  status: ViewStatus = {
    loading: false, error: null, generation: 0, completedGeneration: 0,
  };
  marker: Marker | null = null;
  colors: ColorSettings = {
    rampA: "terrain", rampB: "terrain", opacity: 1,
    manualMin: null, manualMax: null, diffAbsMax: null, showCoverage: true,
  };
  uploadError: Record<"a" | "b", string | null> = { a: null, b: null };
  busy: Record<"a" | "b", boolean> = { a: false, b: false };

  // True when the on-screen grid is out of date relative to inputs
  // (bounds/mode/band/file/resampling). Color-only changes do NOT set it.
  viewDirty = false;
  private reqToken = 0;
  private pendingFetch: { w: number; h: number } | null = null;
  private debounceTimer: number | null = null;
  listeners = new Set<() => void>();

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  emit() {
    this.listeners.forEach((fn) => fn());
  }

  get comparison(): Comparison | null {
    return this.state?.comparison ?? null;
  }

  get expectedIds(): { a: string | null; b: string | null } {
    const s = this.state;
    return {
      a: s?.slots.a?.dataset_id ?? null,
      b: s?.slots.b?.dataset_id ?? null,
    };
  }

  async init() {
    const s = await api.createSession();
    this.sessionId = s.session_id;
    this.emit();
  }

  /** Inputs that change the NUMBERS on screen invalidate the view. */
  private invalidateView() {
    this.viewDirty = true;
    this.status.generation++;
    this.status.loading = true;
    this.emit();
  }

  async refreshState() {
    if (!this.sessionId) return;
    this.state = await api.state(this.sessionId);
    this.emit();
  }

  async upload(slot: "a" | "b", file: File) {
    if (!this.sessionId) return;
    this.busy[slot] = true;
    this.uploadError[slot] = null;
    this.emit();
    try {
      await api.upload(this.sessionId, slot, file);
      await this.refreshState();
      // New file invalidates any on-screen grid so stale pixels can never
      // be interpreted as the new file's result.
      this.invalidateView();
      // The marker keeps its GEOGRAPHIC meaning; re-query the new file.
      if (this.marker) await this.queryMarker(this.marker.lon, this.marker.lat);
    } catch (e) {
      this.uploadError[slot] =
        e instanceof ApiError ? e.message : String(e);
      // The slot's previous file (if any) remains usable; refresh state to
      // confirm what is actually open.
      await this.refreshState();
    } finally {
      this.busy[slot] = false;
      this.emit();
    }
  }

  async removeSlot(slot: "a" | "b") {
    if (!this.sessionId) return;
    await api.removeSlot(this.sessionId, slot);
    await this.refreshState();
    this.invalidateView();
    if (this.marker) await this.queryMarker(this.marker.lon, this.marker.lat);
  }

  async selectBand(slot: "a" | "b", band: number) {
    if (!this.sessionId) return;
    await api.selectBand(this.sessionId, slot, band);
    await this.refreshState();
    this.invalidateView();
    if (this.marker) await this.queryMarker(this.marker.lon, this.marker.lat);
  }

  setMode(mode: Mode) {
    // Hard-guard on the client too: never show a diff unless the server
    // says the bands are numerically comparable.
    if (mode === "diff" && !this.comparison?.comparable) return;
    if (this.mode === mode) return;
    this.mode = mode;
    this.invalidateView();
  }

  setResampling(r: Resampling) {
    if (this.resampling === r) return;
    this.resampling = r;
    this.invalidateView();
  }

  setBounds(b: BBox) {
    this.bounds = b;
    this.viewDirty = true;
    this.status.generation++;
    this.status.loading = true;
    this.emit();
  }

  updateColors(patch: Partial<ColorSettings>) {
    this.colors = { ...this.colors, ...patch };
    // Display-only: repaint, but no server recompute.
    this.emit();
  }

  /** Initial geographic framing: common coverage, else the available file. */
  initialBounds(): BBox | null {
    const s = this.state;
    if (!s) return null;
    const ov = s.comparison.overlap_wgs84;
    if (ov) {
      return wgs84ToMercBounds(ov);
    }
    const one = s.slots.a ?? s.slots.b;
    if (one) {
      const w = one.metadata.bounds_wgs84;
      return wgs84ToMercBounds(w);
    }
    return null;
  }

  /** Schedule a (possibly debounced) view fetch. */
  scheduleView(width: number, height: number, immediate = false) {
    this.pendingFetch = { w: width, h: height };
    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = null;
    }
    const delay = immediate ? 0 : 140;
    this.debounceTimer = window.setTimeout(() => {
      this.debounceTimer = null;
      const p = this.pendingFetch;
      this.pendingFetch = null;
      if (p) void this.fetchView(p.w, p.h);
    }, delay);
  }

  /** Fetch the current view; stale responses are discarded by token. */
  async fetchView(width: number, height: number) {
    if (!this.sessionId || !this.bounds) return;
    if (width <= 0 || height <= 0) return;
    const token = ++this.reqToken;
    const myGen = this.status.generation;
    const body = {
      bounds: this.bounds,
      width, height,
      mode: this.mode,
      resampling: this.resampling,
      expected_ids: this.expectedIds,
    };
    try {
      const decoded = await api.view(this.sessionId, body);
      if (token !== this.reqToken) return; // a newer request superseded us
      // Also guard against a response whose file versions moved on.
      if (!this.idsMatch(decoded.header.dataset_ids)) return;
      this.view = decoded;
      this.status.error = null;
      this.status.completedGeneration = myGen;
      this.viewDirty = false;
      this.emit();
    } catch (e) {
      if (token !== this.reqToken) return;
      const msg = e instanceof ApiError ? e.message : String(e);
      // 409 version mismatch: the newer upload triggers its own fetch.
      if (e instanceof ApiError && e.status === 409) {
        return;
      }
      this.status.error = msg;
      this.viewDirty = false;
      this.emit();
    } finally {
      if (token === this.reqToken) {
        this.status.loading = false;
        this.emit();
      }
    }
  }

  private idsMatch(ids: { a: string | null; b: string | null }): boolean {
    const cur = this.expectedIds;
    return ids.a === cur.a && ids.b === cur.b;
  }

  private markerToken = 0;

  async setMarkerByMerc(mx: number, my: number) {
    const [lon, lat] = mercToLonLat(mx, my);
    await this.queryMarker(lon, lat);
  }

  async queryMarker(lon: number, lat: number) {
    if (!this.sessionId) return;
    const token = ++this.markerToken;
    // Geographic coordinate is the identity; canvas pixel indices are not.
    this.marker = { lon, lat, point: null, loading: true, error: null };
    this.emit();
    try {
      const point = await api.point(this.sessionId, lon, lat);
      if (token !== this.markerToken) return; // a newer click superseded
      this.marker = { lon, lat, point, loading: false, error: null };
      this.emit();
    } catch (e) {
      if (token !== this.markerToken) return;
      this.marker = {
        lon, lat, point: null, loading: false,
        error: e instanceof ApiError ? e.message : String(e),
      };
      this.emit();
    }
  }

  clearMarker() {
    this.marker = null;
    this.emit();
  }

  markerMerc(): [number, number] | null {
    if (!this.marker) return null;
    return lonLatToMerc(this.marker.lon, this.marker.lat);
  }
}
