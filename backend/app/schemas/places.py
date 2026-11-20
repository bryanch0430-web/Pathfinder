from pydantic import BaseModel, Field


class PlaceMediaItem(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=160)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)


class PlaceMediaRequest(BaseModel):
    places: list[PlaceMediaItem] = Field(min_length=1, max_length=24)


class SeasonPlace(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    category: str | None = Field(default=None, max_length=80)


class SeasonStop(BaseModel):
    id: str = Field(min_length=1, max_length=120)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    month: int = Field(ge=1, le=12)
    places: list[SeasonPlace] = Field(default_factory=list, max_length=40)


class SeasonRequest(BaseModel):
    stops: list[SeasonStop] = Field(min_length=1, max_length=24)
