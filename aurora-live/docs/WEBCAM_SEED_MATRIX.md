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

| Region | Registered | ONLINE (probe) | Gate |
| --- | ---: | ---: | --- |
| Europe | 10 | 10 | **PASS** (Digitraffic road weather JPEG) |
| North America | 10 | 10 | **PASS** (WSDOT traffic JPEG) |
| Asia | 10 | pending | YouTube live candidates seeded |
| Oceania | 10 | pending | YouTube live candidates seeded |
| South America | 10 | pending | YouTube live candidates seeded |
| Middle East | 10 | pending | YouTube live candidates seeded |
| Africa | 10 | pending | YouTube live candidates seeded |

**Total ONLINE after JPEG probe: 20 / 70.** Two of seven regional gates pass.

Provider pages (met agencies only) stay `DEGRADED` by design. Direct JPEG/MJPEG/HLS/YouTube-live evidence is required for `ONLINE`.

YouTube probes are rate-limited under aggressive concurrent fetch; re-probe remaining regions with low concurrency after cooldown:

```bash
python scripts/verify_webcam_matrix.py --database var/webcams.sqlite3 --probe --region Asia --limit 10
```

## Integrity

- Private/local targets are rejected by the probe path.
- ONLINE requires stream-specific verification (HLS playlist markers, MJPEG/JPEG frames, or YouTube live markers).
- Capability `webcams` remains non-LIVE until all seven regional gates pass.
- Digitraffic: open data, CC BY 4.0. WSDOT: public agency traffic images. YouTube: platform + streamer terms.
