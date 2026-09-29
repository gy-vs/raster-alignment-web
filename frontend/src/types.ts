export type Slot = 'a' | 'b';

export interface BandInfo {
  index: number;
  description: string | null;
  dtype: string;
  units: string;
  scale: number;
  offset: number;
  nodata: number | string | null;
  block_width: number;
  block_height: number;
}

export interface RasterMetadata {
  file_id: string;
  slot: Slot;
  filename: string;
  size_bytes: number;
  opened_at: number;
  driver: string | null;
  width: number;
  height: number;
  band_count: number;
  crs: string | null;
  crs_name: string | null;
  projected: boolean;
  geographic: boolean;
  bounds_native: [number, number, number, number];
  bounds_wgs84: [number, number, number, number];
  transform: number[];
  bands: BandInfo[];
  tags: Record<string, string>;
}

export interface ComparisonInfo {
  comparable: boolean;
  reason: string | null;
  common_bounds_wgs84?: [number, number, number, number];
  band_a?: number | null;
  band_b?: number | null;
  unit_a?: string | null;
  unit_b?: string | null;
  normalized_unit?: string | null;
  difference_label?: string;
  difference_units?: string;
}

export interface SessionState {
  session_id: string;
  a: RasterMetadata | null;
  b: RasterMetadata | null;
  comparison: ComparisonInfo;
}

export interface Bounds {
  west: number;
  south: number;
  east: number;
  north: number;
}

export interface GridStats {
  valid_pixels: number;
  total_pixels: number;
  min: number | null;
  max: number | null;
  p02: number | null;
  p98: number | null;
}

export interface ViewSideHeader {
  file_id: string | null;
  filename?: string;
  band: number;
  available: boolean;
  reason: string | null;
  crs?: string | null;
  units?: string;
  stats?: GridStats;
  resampling?: string;
  source_resampling_note?: string;
}

export interface DifferenceHeader {
  available: boolean;
  reason: string | null;
  label: string;
  stats: GridStats | null;
}

export interface ViewHeader {
  format: string;
  request_id: string | null;
  crs: string;
  bounds: [number, number, number, number];
  width: number;
  height: number;
  arrays: { a: boolean; b: boolean; diff: boolean };
  a: ViewSideHeader;
  b: ViewSideHeader;
  difference: DifferenceHeader;
  comparison: ComparisonInfo;
}

export interface ViewGrid {
  header: ViewHeader;
  a: Float32Array | null;
  b: Float32Array | null;
  diff: Float32Array | null;
  receivedAt: number;
}

export interface PointSide {
  file_id: string | null;
  filename: string | null;
  band: number | null;
  available: boolean;
  reason: string | null;
  crs: string | null;
  native_x: number | null;
  native_y: number | null;
  row: number | null;
  col: number | null;
  row_float: number | null;
  col_float: number | null;
  inside_raster: boolean;
  raw_value: number | string | null;
  scaled_value: number | null;
  is_nodata: boolean | null;
  nodata: number | string | null;
  scale: number | null;
  offset: number | null;
  units: string | null;
}

export interface PointResponse {
  lon: number;
  lat: number;
  band_a: number;
  band_b: number;
  a: PointSide;
  b: PointSide;
  both_valid: boolean;
  difference: number | null;
  difference_units: string | null;
  warning: string | null;
}
