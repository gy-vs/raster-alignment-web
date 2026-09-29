import type {
  DecodedView, MercBounds, PointResponse, SessionState,
} from "./types";
import { decodeViewPayload } from "./viewCodec";

const BASE = "/api";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    const message =
      (detail && typeof detail === "object" && "message" in detail)
        ? String((detail as { message: unknown }).message)
        : typeof detail === "string" ? detail : JSON.stringify(detail);
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function asJson<T>(p: Promise<Response>): Promise<T> {
  const r = await p;
  if (!r.ok) {
    let detail: unknown;
    try { detail = (await r.json()).detail; } catch { detail = await r.text(); }
    throw new ApiError(r.status, detail);
  }
  return r.json() as Promise<T>;
}

export const api = {
  createSession(): Promise<{ session_id: string }> {
    return asJson(fetch(`${BASE}/sessions`, { method: "POST" }));
  },

  state(sid: string): Promise<SessionState> {
    return asJson(fetch(`${BASE}/sessions/${sid}`));
  },

  async upload(sid: string, slot: "a" | "b", file: File) {
    const fd = new FormData();
    fd.append("file", file);
    return asJson<{
      slot: string;
      dataset_id: string;
      filename: string;
      metadata: import("./types").DatasetMetadata;
      comparison: import("./types").Comparison;
    }>(fetch(`${BASE}/sessions/${sid}/slots/${slot}/upload`, {
      method: "POST", body: fd,
    }));
  },

  removeSlot(sid: string, slot: "a" | "b") {
    return asJson<{ ok: boolean; comparison: import("./types").Comparison }>(
      fetch(`${BASE}/sessions/${sid}/slots/${slot}`, { method: "DELETE" }),
    );
  },

  selectBand(sid: string, slot: "a" | "b", band: number) {
    return asJson<{
      slot: string; band: number;
      comparison: import("./types").Comparison;
    }>(fetch(`${BASE}/sessions/${sid}/slots/${slot}/band`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ band }),
    }));
  },

  comparison(sid: string): Promise<import("./types").Comparison> {
    return asJson(fetch(`${BASE}/sessions/${sid}/comparison`));
  },

  async view(
    sid: string,
    body: {
      bounds: MercBounds;
      width: number;
      height: number;
      mode: "swipe" | "diff";
      resampling: string;
      expected_ids: { a: string | null; b: string | null };
    },
  ): Promise<DecodedView> {
    const r = await fetch(`${BASE}/sessions/${sid}/view`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      let detail: unknown;
      try { detail = (await r.json()).detail; } catch { detail = await r.text(); }
      throw new ApiError(r.status, detail);
    }
    return decodeViewPayload(r);
  },

  point(sid: string, lon: number, lat: number): Promise<PointResponse> {
    return asJson(
      fetch(`${BASE}/sessions/${sid}/point?lon=${lon}&lat=${lat}`),
    );
  },
};
