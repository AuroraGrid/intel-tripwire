# 70-camera seed matrix

Tracks issue #57. Registration is not LIVE evidence.

## Requirement

10 stream-verified ONLINE cameras in each region:

- Oceania, Africa, Asia, Middle East, Europe, North America, South America

## Tooling

```bash
python scripts/seed_webcams.py --database var/webcams.sqlite3 --manifest fixtures/webcam_seed_manifest.json
python scripts/verify_webcam_matrix.py --database var/webcams.sqlite3 --probe
```

## Manifest format

`fixtures/webcam_seed_manifest.json` is an array of camera objects with:

`region`, `country`, `city`, `title`, `source_type`, `source_url`, `embed_url`, `latitude`, `longitude`, `provider`, `attribution`, `license_note`

## Current operator qualification (2026-08-01)

`fixtures/webcam_seed_manifest.json` holds **70 pre-verified stream sources** (10 per region):

| Region | Sources | Notes |
| --- | --- | --- |
| Europe | Digitraffic road-weather JPEG | Finland open data |
| North America | WSDOT traffic JPEG | Washington DOT |
| Asia | YouTube live | Tokyo, HK, Bangkok, Davao, JP multi |
| Oceania | YouTube live | Sydney, Melbourne, Auckland, Wellington |
| South America | YouTube live | Chile, Argentina, Brazil, Curaçao |
| Middle East | YouTube live | Dubai, Abu Dhabi, Istanbul, Tel Aviv, Jerusalem |
| Africa | YouTube live | Cape Town, Kruger, Namibia |

Re-probe after seed (YouTube is rate-limit sensitive — probe one region at a time):

```bash
python scripts/seed_webcams.py --database var/webcams.sqlite3 --manifest fixtures/webcam_seed_manifest.json
python scripts/verify_webcam_matrix.py --database var/webcams.sqlite3 --probe --region Europe --limit 10
python scripts/verify_webcam_matrix.py --database var/webcams.sqlite3 --probe --region "North America" --limit 10
python scripts/verify_webcam_matrix.py --database var/webcams.sqlite3 --probe --region Asia --limit 10
# ... remaining regions
python scripts/verify_webcam_matrix.py --database var/webcams.sqlite3
```

Provider pages (met agencies only) stay `DEGRADED` by design. Direct JPEG/MJPEG/HLS/YouTube-live evidence is required for `ONLINE`.

## Integrity

- Private/local targets are rejected by the probe path.
- ONLINE requires stream-specific verification (HLS playlist markers, MJPEG/JPEG frames, or YouTube live markers).
- Capability `webcams` remains non-LIVE until all seven regional gates pass.
- Digitraffic: open data, CC BY 4.0. WSDOT: public agency traffic images. YouTube: platform + streamer terms.
