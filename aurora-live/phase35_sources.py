from __future__ import annotations

import json
import struct
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlparse


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    status: int
    url: str
    headers: dict[str, str]
    body: bytes


class HttpTransport:
    """Bounded HTTPS transport with allowlisted hosts and retry/backoff."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        retries: int = 3,
        backoff_seconds: float = 0.25,
        user_agent: str = "AURORA-LIVE/35 (+https://github.com/hr185882-creator/intel-tripwire)",
        opener: Callable[..., Any] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if retries < 1 or retries > 8:
            raise ValueError("retries must be between 1 and 8")
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.backoff_seconds = max(0.0, backoff_seconds)
        self.user_agent = user_agent
        self._opener = opener or urllib.request.urlopen
        self._sleeper = sleeper

    @staticmethod
    def _validate_url(url: str, allowed_hosts: set[str]) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise SourceError("only HTTPS source URLs are allowed")
        if parsed.username or parsed.password:
            raise SourceError("credential-bearing URLs are not allowed")
        hostname = parsed.hostname.lower().rstrip(".")
        if hostname not in {host.lower().rstrip(".") for host in allowed_hosts}:
            raise SourceError(f"host is not allowlisted: {hostname}")

    def get(self, url: str, *, allowed_hosts: set[str], max_bytes: int) -> HttpResponse:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self._validate_url(url, allowed_hosts)
        last_error: Exception | None = None
        for attempt in range(self.retries):
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json,image/png,image/jpeg,image/gif,image/webp,*/*;q=0.1",
                    "Cache-Control": "no-cache",
                },
                method="GET",
            )
            try:
                with self._opener(request, timeout=self.timeout_seconds) as response:
                    status = int(getattr(response, "status", 200))
                    final_url = str(getattr(response, "url", url))
                    self._validate_url(final_url, allowed_hosts)
                    headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
                    declared = headers.get("content-length")
                    if declared:
                        try:
                            if int(declared) > max_bytes:
                                raise SourceError("response exceeds configured byte limit")
                        except ValueError:
                            pass
                    body = response.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise SourceError("response exceeds configured byte limit")
                    if status < 200 or status >= 300:
                        raise SourceError(f"unexpected HTTP status {status}")
                    return HttpResponse(status=status, url=final_url, headers=headers, body=body)
            except urllib.error.HTTPError as exc:
                last_error = exc
                retryable = exc.code == 429 or 500 <= exc.code <= 599
                if not retryable or attempt + 1 >= self.retries:
                    raise SourceError(f"HTTP {exc.code} from source") from exc
            except (urllib.error.URLError, TimeoutError, OSError, SourceError) as exc:
                last_error = exc
                if isinstance(exc, SourceError) and "byte limit" in str(exc):
                    raise
                if attempt + 1 >= self.retries:
                    raise SourceError(f"source request failed: {exc}") from exc
            self._sleeper(self.backoff_seconds * (2**attempt))
        raise SourceError(f"source request failed: {last_error}")

    def get_json(self, url: str, *, allowed_hosts: set[str], max_bytes: int = 2_000_000) -> Any:
        response = self.get(url, allowed_hosts=allowed_hosts, max_bytes=max_bytes)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type and content_type not in {"application/json", "text/json", "text/plain"}:
            raise SourceError(f"unexpected JSON content type: {content_type}")
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceError("source did not return valid UTF-8 JSON") from exc


@dataclass(frozen=True)
class ImageCandidate:
    adapter: str
    external_id: str
    source_payload: dict[str, Any]
    captured_at: str | None
    image_url: str
    allowed_hosts: tuple[str, ...]
    metadata: dict[str, Any]

    def value(self) -> dict[str, Any]:
        return asdict(self)


class SourceAdapter:
    name = "base"

    def discover(self, transport: HttpTransport) -> list[ImageCandidate]:
        raise NotImplementedError


class NasaEpicAdapter(SourceAdapter):
    name = "nasa-epic"
    api_url = "https://epic.gsfc.nasa.gov/api/natural"
    allowed_hosts = ("epic.gsfc.nasa.gov",)

    @staticmethod
    def _timestamp(value: Any) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise SourceError("NASA EPIC record is missing date")
        try:
            parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise SourceError("NASA EPIC date has an unexpected format") from exc
        return parsed.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _region(latitude: float, longitude: float) -> str:
        if longitude < -30:
            return "North America" if latitude >= 0 else "South America"
        if longitude < 35:
            return "Europe" if latitude >= 30 else "Africa"
        if longitude < 75:
            return "Middle East" if latitude >= 10 else "Africa"
        if longitude < 150:
            return "Asia"
        return "Oceania"

    def discover(self, transport: HttpTransport) -> list[ImageCandidate]:
        payload = transport.get_json(self.api_url, allowed_hosts=set(self.allowed_hosts))
        if not isinstance(payload, list) or not payload:
            raise SourceError("NASA EPIC returned no natural-color images")
        records = [item for item in payload if isinstance(item, dict) and item.get("image") and item.get("date")]
        if not records:
            raise SourceError("NASA EPIC returned no usable natural-color records")
        latest = max(records, key=lambda item: str(item.get("date")))
        captured_at = self._timestamp(latest.get("date"))
        captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        image_name = str(latest["image"]).strip()
        image_url = (
            "https://epic.gsfc.nasa.gov/archive/natural/"
            f"{captured.year:04d}/{captured.month:02d}/{captured.day:02d}/png/{image_name}.png"
        )
        centroid = latest.get("centroid_coordinates") or {}
        try:
            latitude = float(centroid.get("lat", 0.0))
            longitude = float(centroid.get("lon", 0.0))
        except (TypeError, ValueError) as exc:
            raise SourceError("NASA EPIC centroid coordinates are invalid") from exc
        region = self._region(latitude, longitude)
        source_payload = {
            "region": region,
            "country": "",
            "title": "NASA DSCOVR EPIC natural-color Earth disk",
            "category": "satellite",
            "geographic_scope": "Global sunlit Earth disk; canonical region follows current disk centroid",
            "source_url": self.api_url,
            "image_url": image_url,
            "latitude": latitude,
            "longitude": longitude,
            "provider": "NASA EPIC / NOAA DSCOVR",
            "attribution": "Credit NASA EPIC Team and NOAA DSCOVR",
            "license_note": "NASA EPIC imagery is available for reuse with credit under NASA media usage guidance",
            "refresh_interval_seconds": 3600,
            "max_age_seconds": 1_209_600,
        }
        return [
            ImageCandidate(
                adapter=self.name,
                external_id=str(latest.get("identifier") or image_name),
                source_payload=source_payload,
                captured_at=captured_at,
                image_url=image_url,
                allowed_hosts=self.allowed_hosts,
                metadata={
                    "identifier": latest.get("identifier"),
                    "caption": latest.get("caption"),
                    "version": latest.get("version"),
                    "centroid_coordinates": {"lat": latitude, "lon": longitude},
                },
            )
        ]


class NoaaGoesAdapter(SourceAdapter):
    name = "noaa-goes"
    image_url = "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/CONUS/GEOCOLOR/1250x750.jpg"
    source_url = "https://www.star.nesdis.noaa.gov/goes/conus.php?sat=G19"
    allowed_hosts = ("cdn.star.nesdis.noaa.gov", "www.star.nesdis.noaa.gov")

    def discover(self, transport: HttpTransport) -> list[ImageCandidate]:
        del transport
        source_payload = {
            "region": "North America",
            "country": "United States",
            "title": "NOAA GOES-19 CONUS GeoColor",
            "category": "satellite",
            "geographic_scope": "Continental United States and adjacent waters",
            "source_url": self.source_url,
            "image_url": self.image_url,
            "latitude": 39.5,
            "longitude": -98.35,
            "provider": "NOAA NESDIS STAR",
            "attribution": "Credit CIRA/NOAA for GeoColor imagery",
            "license_note": "U.S. government imagery; informational and non-operational use per NOAA STAR disclaimer",
            "refresh_interval_seconds": 300,
            "max_age_seconds": 1800,
        }
        return [
            ImageCandidate(
                adapter=self.name,
                external_id="GOES19-ABI-CONUS-GEOCOLOR-1250x750",
                source_payload=source_payload,
                captured_at=None,
                image_url=self.image_url,
                allowed_hosts=self.allowed_hosts,
                metadata={"satellite": "GOES-19", "sector": "CONUS", "product": "GeoColor"},
            )
        ]


ADAPTERS: dict[str, type[SourceAdapter]] = {
    NasaEpicAdapter.name: NasaEpicAdapter,
    NoaaGoesAdapter.name: NoaaGoesAdapter,
}


def adapter_names() -> tuple[str, ...]:
    return tuple(sorted(ADAPTERS))


def build_adapter(name: str) -> SourceAdapter:
    try:
        return ADAPTERS[name]()
    except KeyError as exc:
        raise ValueError(f"unknown adapter: {name}") from exc


def parse_http_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized_content_type(headers: dict[str, str], body: bytes) -> str:
    declared = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if declared.startswith("image/"):
        return declared
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if body.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    raise SourceError("response is not a recognized image")


def image_dimensions(body: bytes, content_type: str) -> tuple[int, int]:
    if content_type == "image/png":
        if len(body) < 24 or not body.startswith(b"\x89PNG\r\n\x1a\n"):
            raise SourceError("invalid PNG image")
        width, height = struct.unpack(">II", body[16:24])
        return _valid_dimensions(width, height)
    if content_type == "image/gif":
        if len(body) < 10 or not body.startswith((b"GIF87a", b"GIF89a")):
            raise SourceError("invalid GIF image")
        width, height = struct.unpack("<HH", body[6:10])
        return _valid_dimensions(width, height)
    if content_type == "image/jpeg":
        return _jpeg_dimensions(body)
    if content_type == "image/webp":
        return _webp_dimensions(body)
    raise SourceError(f"unsupported image format: {content_type}")


def _valid_dimensions(width: int, height: int) -> tuple[int, int]:
    if width < 1 or height < 1 or width > 100_000 or height > 100_000:
        raise SourceError("image dimensions are outside the allowed range")
    return width, height


def _jpeg_dimensions(body: bytes) -> tuple[int, int]:
    if len(body) < 4 or not body.startswith(b"\xff\xd8"):
        raise SourceError("invalid JPEG image")
    index = 2
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while index + 4 <= len(body):
        if body[index] != 0xFF:
            index += 1
            continue
        while index < len(body) and body[index] == 0xFF:
            index += 1
        if index >= len(body):
            break
        marker = body[index]
        index += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            break
        if index + 2 > len(body):
            break
        length = struct.unpack(">H", body[index : index + 2])[0]
        if length < 2 or index + length > len(body):
            raise SourceError("invalid JPEG segment")
        if marker in sof_markers:
            if length < 7:
                raise SourceError("invalid JPEG frame")
            height, width = struct.unpack(">HH", body[index + 3 : index + 7])
            return _valid_dimensions(width, height)
        index += length
    raise SourceError("JPEG dimensions were not found")


def _webp_dimensions(body: bytes) -> tuple[int, int]:
    if len(body) < 30 or not (body.startswith(b"RIFF") and body[8:12] == b"WEBP"):
        raise SourceError("invalid WebP image")
    chunk = body[12:16]
    if chunk == b"VP8X":
        width = 1 + int.from_bytes(body[24:27], "little")
        height = 1 + int.from_bytes(body[27:30], "little")
        return _valid_dimensions(width, height)
    if chunk == b"VP8 ":
        marker = body.find(b"\x9d\x01\x2a", 20)
        if marker < 0 or marker + 7 > len(body):
            raise SourceError("invalid lossy WebP image")
        width, height = struct.unpack("<HH", body[marker + 3 : marker + 7])
        return _valid_dimensions(width & 0x3FFF, height & 0x3FFF)
    if chunk == b"VP8L":
        if body[20] != 0x2F:
            raise SourceError("invalid lossless WebP image")
        bits = int.from_bytes(body[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return _valid_dimensions(width, height)
    raise SourceError("unsupported WebP encoding")
