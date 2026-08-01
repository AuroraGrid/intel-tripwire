# 70-camera seed matrix

Tracks issue #57. Registration is not LIVE evidence.

## Requirement

10 stream-verified ONLINE cameras in each region:

- Oceania, Africa, Asia, Middle East, Europe, North America, South America

## Tooling

```bash
python scripts/seed_webcams.py --database var/webcams.sqlite3 --manifest fixtures/webcam_seed_manifest.json
python scripts/verify_webcam_matrix.py --database var/webcams.sqlite3
```

## Manifest format

`fixtures/webcam_seed_manifest.json` is an array of camera objects with:

`region`, `country`, `city`, `title`, `source_type`, `source_url`, `embed_url`, `latitude`, `longitude`, `provider`, `attribution`, `license_note`

## Integrity

- Private/local targets are rejected by the probe path.
- ONLINE requires stream-specific verification (HLS playlist markers, MJPEG/JPEG frames, or YouTube live markers).
- Capability `webcams` remains non-LIVE until all seven regional gates pass.
