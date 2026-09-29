from typing import Any, Literal

from pydantic import BaseModel, Field

SlotName = Literal["a", "b"]


class BandInfo(BaseModel):
    index: int
    description: str | None = None
    dtype: str
    units: str
    scale: float
    offset: float
    nodata: float | int | str | None = None
    block_width: int
    block_height: int


class RasterMetadata(BaseModel):
    file_id: str
    slot: SlotName
    filename: str
    size_bytes: int
    opened_at: float
    driver: str | None = None
    width: int
    height: int
    band_count: int
    crs: str | None
    crs_name: str | None
    projected: bool
    geographic: bool
    bounds_native: tuple[float, float, float, float]
    bounds_wgs84: tuple[float, float, float, float]
    transform: list[float]
    bands: list[BandInfo]
    tags: dict[str, str] = Field(default_factory=dict)


class SessionState(BaseModel):
    session_id: str
    a: RasterMetadata | None = None
    b: RasterMetadata | None = None
    comparison: dict[str, Any]


class BoundsModel(BaseModel):
    west: float
    south: float
    east: float
    north: float


class ViewRequest(BaseModel):
    band_a: int
    band_b: int
    bounds: BoundsModel
    width: int = Field(ge=64, le=2048)
    height: int = Field(ge=64, le=2048)
    request_id: str | None = None


class PointRequest(BaseModel):
    lon: float
    lat: float
    band_a: int
    band_b: int


class PointSide(BaseModel):
    file_id: str | None = None
    filename: str | None = None
    band: int | None = None
    available: bool
    reason: str | None = None
    crs: str | None = None
    native_x: float | None = None
    native_y: float | None = None
    row: int | None = None
    col: int | None = None
    row_float: float | None = None
    col_float: float | None = None
    inside_raster: bool
    raw_value: float | int | str | None = None
    scaled_value: float | None = None
    is_nodata: bool | None = None
    nodata: float | int | str | None = None
    scale: float | None = None
    offset: float | None = None
    units: str | None = None


class PointResponse(BaseModel):
    lon: float
    lat: float
    request_id: str | None = None
    band_a: int
    band_b: int
    a: PointSide
    b: PointSide
    both_valid: bool
    difference: float | None = None
    difference_units: str | None = None
    warning: str | None = None
