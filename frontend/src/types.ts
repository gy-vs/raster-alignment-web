// API response types (mirrors backend schemas).

export interface CrsInfo {
  epsg: number | null;
  kind: "geographic" | "projected" | "unknown";
  name: string | null;
  proj4: string;
  wkt_preview: string;
  linear_units: string | null;
  linear_units_factor: number | null;
}

export interface SampleStats {
  sampled: boolean;
  count: number;
  step?: number;
  min?: number;
  max?: number;
  mean?: number;
}

export interface BandInfo {
  index: number;
  description: string;
  dtype: string;
  nodata: number | "NaN" | null;
  scale: number;
  offset: number;
  unit: string | null;
  unit_source: "band_tag" | "crs_linear_units" | null;
  unit_raw: string | null;
  color_interpretation: string | null;
  sample_stats: SampleStats;
  tags: Record<string, string>;
}

export interface DatasetMetadata {
  dataset_id: string;
  filename: string;
  driver: string;
  width: number;
  height: number;
  band_count: number;
  crs: CrsInfo;
  bounds_native: { left: number; bottom: number; right: number; top: number };
  bounds_wgs84: { left: number; bottom: number; right: number; top: number };
  resolution_native: { x: number; y: number };
  resolution_metres: { x: number; y: number } | null;
  transform: number[];
  bands: BandInfo[];
  tiled: boolean;
}

export interface SlotSummary {
  dataset_id: string;
  filename: string;
  band: number;
  metadata: DatasetMetadata;
}

export interface ComparabilityIssue {
  code: string;
  message: string;
}

export interface Comparison {
  comparable: boolean;
  blockers: ComparabilityIssue[];
  warnings: ComparabilityIssue[];
  overlap_wgs84:
    | { left: number; bottom: number; right: number; top: number }
    | null;
  unit_a?: string | null;
  unit_b?: string | null;
  unit_conversion_a_to_b?: number;
  difference_unit?: string | null;
  scale_a?: number;
  offset_a?: number;
  nodata_a?: number | "NaN" | null;
  scale_b?: number;
  offset_b?: number;
  nodata_b?: number | "NaN" | null;
}

export interface SessionState {
  session_id: string;
  slots: { a: SlotSummary | null; b: SlotSummary | null };
  comparison: Comparison;
}

export interface MercBounds {
  left: number;
  bottom: number;
  right: number;
  top: number;
}

export interface ViewLayerStats {
  min: number;
  max: number;
  mean: number;
  count: number;
}

export interface DecodedLayer {
  values: Float32Array;
  mask: Uint8Array;
  maskA?: Uint8Array;
  maskB?: Uint8Array;
  onlyA?: Uint8Array;
  onlyB?: Uint8Array;
}

export interface ViewHeader {
  mode: "swipe" | "diff";
  resampling: string;
  width: number;
  height: number;
  bounds: MercBounds;
  dataset_ids: { a: string | null; b: string | null };
  bands: { a: number; b: number };
  comparison: Comparison;
  order: string[];
  layers: Record<string, { extras: string[] }>;
  layer_stats?: Record<string, ViewLayerStats | null>;
  diff_stats?: ViewLayerStats & {
    abs_mean: number;
    rmse: number;
    std: number;
  } | null;
  diff_unit?: string;
  diff_order?: string;
}

export interface DecodedView {
  header: ViewHeader;
  layers: Record<string, DecodedLayer>;
}

export interface PointSlotInfo {
  available: boolean;
  reason?: string;
  filename?: string;
  dataset_id?: string;
  native_x?: number;
  native_y?: number;
  row_float?: number;
  col_float?: number;
  row?: number;
  col?: number;
  fractional_row?: number;
  fractional_col?: number;
  inside_bounds?: boolean;
  raw_value?: number | null;
  raw_dtype?: string;
  calibrated_value?: number | null;
  is_nodata?: boolean;
  nodata_value?: number | "NaN" | null;
  scale?: number;
  offset?: number;
  comparable?: boolean;
  pixel_size_native?: { x: number; y: number };
  window?: {
    row_off: number;
    col_off: number;
    height: number;
    width: number;
    raw: (number | null)[];
  };
}

export interface PointResponse {
  session_id: string;
  lon: number;
  lat: number;
  comparison: Comparison;
  slots: { a: PointSlotInfo; b: PointSlotInfo };
  point_difference: {
    a_minus_b: number;
    unit: string | null;
    a_value_normalized: number;
    b_value: number;
  } | null;
  screen_position_comparable: boolean;
}
