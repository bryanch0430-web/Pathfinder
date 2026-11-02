"""Provider registry: the one place where a data source is registered.

Each capability is a priority-ordered list. An agent walks the list and the first
non-empty result wins; a failure moves to the next source. Adding a source is one
import and one append, which is the whole point of the registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings
from app.infrastructure.providers.flights import SerpApiFlightSource
from app.infrastructure.providers.fx import FxService
from app.infrastructure.providers.geocoding import (
    GeocoderChain,
    GoogleGeocoder,
    NominatimGeocoder,
    OpenMeteoGeocoder,
)
from app.infrastructure.providers.elevation import GoogleElevation
from app.infrastructure.providers.hotels import SerpApiHotelSource, WikivoyageHotelSource
from app.infrastructure.providers.notes import BilibiliNotesSource, YouTubeNotesSource
from app.infrastructure.providers.places import (
    GooglePlacesAutocomplete,
    GooglePlacesSource,
    NominatimPlacesSource,
    WikivoyagePlacesSource,
)
from app.infrastructure.providers.routes import GoogleRoutesSource
from app.infrastructure.providers.timezone import GoogleTimeZone
from app.infrastructure.providers.weather import OpenMeteoArchiveWeather, OpenMeteoWeather
from app.infrastructure.providers.websearch import DeepSeekSearchSource, GrokSearchSource


@dataclass
class ProviderRegistry:
    settings: Settings
    geocoder: object
    weather: list = field(default_factory=list)
    places: list = field(default_factory=list)
    hotels: list = field(default_factory=list)
    flights: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    web: list = field(default_factory=list)
    web_deep: list = field(default_factory=list)
    routes: list = field(default_factory=list)
    timezone: object | None = None
    elevation: object | None = None
    autocomplete: object | None = None
    fx: FxService = field(default_factory=FxService)

    @classmethod
    def build(cls, settings: Settings) -> "ProviderRegistry":
        geocoders: list = []
        if settings.google_places_api_key or settings.google_maps_api_key:
            geocoders.append(GoogleGeocoder(settings))
        geocoders.extend([OpenMeteoGeocoder(settings), NominatimGeocoder(settings)])
        geocoder = GeocoderChain(geocoders)
        weather = [OpenMeteoWeather(settings), OpenMeteoArchiveWeather(settings)]
        places = [
            GooglePlacesSource(settings),
            NominatimPlacesSource(settings),
            WikivoyagePlacesSource(settings),
        ]
        hotels = [SerpApiHotelSource(settings), WikivoyageHotelSource(settings)]
        flights = [SerpApiFlightSource(settings)]
        notes: list = []
        if settings.enable_notes:
            notes.append(YouTubeNotesSource(settings))
            notes.append(BilibiliNotesSource(settings))
        routes = [GoogleRoutesSource(settings)]
        web = [DeepSeekSearchSource(settings)] if settings.web_search_enabled else []
        web_deep = [GrokSearchSource(settings)] if settings.grok_search_enabled else []
        timezone = GoogleTimeZone(settings) if settings.enable_timezone else None
        elevation = GoogleElevation(settings) if settings.enable_elevation else None
        autocomplete = GooglePlacesAutocomplete(settings)
        return cls(
            settings=settings,
            geocoder=geocoder,
            weather=weather,
            places=places,
            hotels=hotels,
            flights=flights,
            notes=notes,
            routes=routes,
            web=web,
            web_deep=web_deep,
            timezone=timezone,
            elevation=elevation,
            autocomplete=autocomplete,
            fx=FxService(),
        )

    def describe(self) -> dict[str, object]:
        def names(items: list) -> list[str]:
            return [getattr(item, "name", item.__class__.__name__) for item in items]

        return {
            "geocoder": names([self.geocoder]) if not isinstance(self.geocoder, GeocoderChain) else names(self.geocoder.sources),
            "weather": names(self.weather),
            "places": names(self.places),
            "hotels": names(self.hotels),
            "flights": names(self.flights),
            "notes": names(self.notes),
            "web": names(self.web),
            "web_deep": names(self.web_deep),
            "routes": names(self.routes),
            "timezone": names([self.timezone]) if self.timezone is not None else [],
            "elevation": names([self.elevation]) if self.elevation is not None else [],
            "autocomplete": names([self.autocomplete]) if self.autocomplete is not None else [],
            "fx": self.fx.source,
        }
