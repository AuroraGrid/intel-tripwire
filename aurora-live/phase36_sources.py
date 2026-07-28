from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from phase35_sources import ImageCandidate, NasaEpicAdapter, NoaaGoesAdapter, SourceAdapter


@dataclass(frozen=True)
class AdapterPolicy:
    name: str
    interval_seconds: int
    failure_threshold: int = 3
    cooldown_seconds: int = 900


class StaticLatestImageAdapter(SourceAdapter):
    name = "static-latest"
    region = ""
    country = ""
    title = ""
    geographic_scope = ""
    source_url = ""
    image_url = ""
    provider = ""
    attribution = ""
    license_note = ""
    latitude: float | None = None
    longitude: float | None = None
    refresh_interval_seconds = 600
    max_age_seconds = 3600
    allowed_hosts: tuple[str, ...] = ()

    def discover(self, transport) -> list[ImageCandidate]:
        del transport
        return [
            ImageCandidate(
                adapter=self.name,
                external_id=self.name,
                source_payload={
                    "region": self.region,
                    "country": self.country,
                    "title": self.title,
                    "category": "satellite",
                    "geographic_scope": self.geographic_scope,
                    "source_url": self.source_url,
                    "image_url": self.image_url,
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "provider": self.provider,
                    "attribution": self.attribution,
                    "license_note": self.license_note,
                    "refresh_interval_seconds": self.refresh_interval_seconds,
                    "max_age_seconds": self.max_age_seconds,
                },
                captured_at=None,
                image_url=self.image_url,
                allowed_hosts=self.allowed_hosts,
                metadata={
                    "latest_endpoint": True,
                    "region": self.region,
                    "capture_timestamp_basis": "HTTP Last-Modified when supplied; observation time otherwise",
                },
            )
        ]


class NoaaSouthAmericaAdapter(StaticLatestImageAdapter):
    name = "noaa-goes-south-america"
    region = "South America"
    title = "NOAA GOES-19 South America Southern GeoColor"
    geographic_scope = "Southern South America sector"
    source_url = "https://www.star.nesdis.noaa.gov/goes/sector.php?sat=G19&sector=ssa"
    image_url = "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/ssa/GEOCOLOR/900x540.jpg"
    provider = "NOAA NESDIS STAR"
    attribution = "Credit CIRA/NOAA for GeoColor imagery"
    license_note = "U.S. government imagery; informational and non-operational use per NOAA STAR disclaimer"
    latitude = -25.0
    longitude = -60.0
    allowed_hosts = ("cdn.star.nesdis.noaa.gov", "www.star.nesdis.noaa.gov")


class EumetsatEuropeAdapter(StaticLatestImageAdapter):
    name = "eumetsat-europe"
    region = "Europe"
    title = "EUMETSAT MTG Europe IR10.5"
    geographic_scope = "Europe domain"
    source_url = "https://user.eumetsat.int/resources/user-guides/eumetview-image-download-by-using-fixed-urls-guide"
    image_url = (
        "https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetMap"
        "&layers=mtg_fd:ir105_hrfi,backgrounds:ne_10m_coastline,backgrounds:ne_boundary_lines_land"
        "&bbox=-2500000,3050000,3750000,5450000&width=1800&height=950"
        "&srs=AUTO:97004,9001,0,0&styles=&format=image/jpeg&bgcolor=0xCCCCCC"
    )
    provider = "EUMETSAT EUMETView"
    attribution = "Credit EUMETSAT"
    license_note = "Latest-image WMS request under EUMETSAT data policy and EUMETView terms"
    latitude = 50.0
    longitude = 15.0
    allowed_hosts = ("view.eumetsat.int", "user.eumetsat.int")


class EumetsatAfricaAdapter(StaticLatestImageAdapter):
    name = "eumetsat-africa"
    region = "Africa"
    title = "EUMETSAT MTG Africa IR10.5"
    geographic_scope = "Africa domain"
    source_url = "https://user.eumetsat.int/resources/user-guides/eumetview-image-download-by-using-fixed-urls-guide"
    image_url = (
        "https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetMap"
        "&layers=mtg_fd:ir105_hrfi,backgrounds:ne_10m_coastline,backgrounds:ne_boundary_lines_land"
        "&bbox=-2200000,-4000000,4900000,4000000&width=1200&height=1400"
        "&srs=AUTO:97004,9001,0,0&styles=&format=image/jpeg&bgcolor=0xCCCCCC"
    )
    provider = "EUMETSAT EUMETView"
    attribution = "Credit EUMETSAT"
    license_note = "Latest-image WMS request under EUMETSAT data policy and EUMETView terms"
    latitude = 0.0
    longitude = 20.0
    allowed_hosts = ("view.eumetsat.int", "user.eumetsat.int")


class EumetsatMiddleEastAdapter(StaticLatestImageAdapter):
    name = "eumetsat-middle-east"
    region = "Middle East"
    title = "EUMETSAT MTG Middle East IR10.5"
    geographic_scope = "Middle East custom WMS view"
    source_url = "https://user.eumetsat.int/resources/user-guides/eumet-view-user-guide"
    image_url = (
        "https://view.eumetsat.int/geoserver/wms?service=WMS&version=1.3.0&request=GetMap"
        "&layers=mtg_fd:ir105_hrfi&styles=&format=image/jpeg&crs=EPSG:4326"
        "&bbox=10,25,45,65&width=1200&height=900"
    )
    provider = "EUMETSAT EUMETView"
    attribution = "Credit EUMETSAT"
    license_note = "Latest-image WMS request under EUMETSAT data policy and EUMETView terms"
    latitude = 29.0
    longitude = 45.0
    allowed_hosts = ("view.eumetsat.int", "user.eumetsat.int")


class JmaHimawariAdapter(SourceAdapter):
    region = ""
    area = ""
    country = ""
    title = ""
    geographic_scope = ""
    latitude: float | None = None
    longitude: float | None = None
    name = "jma-himawari"
    allowed_hosts = ("www.data.jma.go.jp",)

    @staticmethod
    def latest_slot(now: datetime | None = None, publication_lag_minutes: int = 30) -> datetime:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) - timedelta(minutes=publication_lag_minutes)
        return current.replace(minute=(current.minute // 10) * 10, second=0, microsecond=0)

    def discover(self, transport) -> list[ImageCandidate]:
        del transport
        slot = self.latest_slot()
        hhmm = slot.strftime("%H%M")
        image_url = f"https://www.data.jma.go.jp/mscweb/data/himawari/img/{self.area}/{self.area}_b13_{hhmm}.jpg"
        source_url = f"https://www.data.jma.go.jp/mscweb/data/himawari/list_{self.area}.html"
        return [
            ImageCandidate(
                adapter=self.name,
                external_id=f"{self.area}-b13-{slot.strftime('%Y%m%d%H%M')}",
                source_payload={
                    "region": self.region,
                    "country": self.country,
                    "title": self.title,
                    "category": "satellite",
                    "geographic_scope": self.geographic_scope,
                    "source_url": source_url,
                    "image_url": image_url,
                    "latitude": self.latitude,
                    "longitude": self.longitude,
                    "provider": "Japan Meteorological Agency Meteorological Satellite Center",
                    "attribution": "Credit JMA, NOAA/NESDIS and CSU/CIRA where required by the JMA imagery terms",
                    "license_note": "Use subject to the JMA Meteorological Satellite Center website terms and imagery acknowledgements",
                    "refresh_interval_seconds": 600,
                    "max_age_seconds": 3600,
                },
                captured_at=slot.isoformat().replace("+00:00", "Z"),
                image_url=image_url,
                allowed_hosts=self.allowed_hosts,
                metadata={"satellite": "Himawari-9", "region": self.region, "area": self.area, "band": "B13", "slot_utc": hhmm},
            )
        ]


class JmaAsiaAdapter(JmaHimawariAdapter):
    name = "jma-himawari-asia"
    region = "Asia"
    area = "se1"
    title = "JMA Himawari-9 Southeast Asia infrared"
    geographic_scope = "Southeast Asia 1: 80E to 115E, 30N to equator"
    latitude = 15.0
    longitude = 97.5


class JmaOceaniaAdapter(JmaHimawariAdapter):
    name = "jma-himawari-oceania"
    region = "Oceania"
    area = "aus"
    country = "Australia"
    title = "JMA Himawari-9 Australia infrared"
    geographic_scope = "Australia: 110E to 155E, 10S to 45S"
    latitude = -27.5
    longitude = 132.5


OPERATIONAL_ADAPTERS: dict[str, type[SourceAdapter]] = {
    NasaEpicAdapter.name: NasaEpicAdapter,
    NoaaGoesAdapter.name: NoaaGoesAdapter,
    NoaaSouthAmericaAdapter.name: NoaaSouthAmericaAdapter,
    EumetsatEuropeAdapter.name: EumetsatEuropeAdapter,
    EumetsatAfricaAdapter.name: EumetsatAfricaAdapter,
    EumetsatMiddleEastAdapter.name: EumetsatMiddleEastAdapter,
    JmaAsiaAdapter.name: JmaAsiaAdapter,
    JmaOceaniaAdapter.name: JmaOceaniaAdapter,
}

POLICIES: dict[str, AdapterPolicy] = {
    name: AdapterPolicy(name=name, interval_seconds=3600 if name == "nasa-epic" else 600)
    for name in OPERATIONAL_ADAPTERS
}

BASELINE_REGION_ADAPTERS: dict[str, str] = {
    "Oceania": "jma-himawari-oceania",
    "Africa": "eumetsat-africa",
    "Asia": "jma-himawari-asia",
    "Middle East": "eumetsat-middle-east",
    "Europe": "eumetsat-europe",
    "North America": "noaa-goes",
    "South America": "noaa-goes-south-america",
}


def operational_adapter_names() -> tuple[str, ...]:
    return tuple(sorted(OPERATIONAL_ADAPTERS))


def build_operational_adapter(name: str) -> SourceAdapter:
    try:
        return OPERATIONAL_ADAPTERS[name]()
    except KeyError as exc:
        raise ValueError(f"unknown operational adapter: {name}") from exc


def policy_for(name: str) -> AdapterPolicy:
    try:
        return POLICIES[name]
    except KeyError as exc:
        raise ValueError(f"unknown operational adapter: {name}") from exc
