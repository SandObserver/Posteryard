# Posteryard

Apple TV style artwork for the Plex `Movies` and `TV Shows` libraries.

| Plex image | Design |
| --- | --- |
| Movie and show poster | Apple's tile: textless TMDB art, the title logo in Apple's fixed box, Apple's black bottom gradient. Quality badges (movies) and the Maintainerr label in a row under the logo. Streaming service mark top right on shows. |
| Season poster | The same tile with `Season N` or `Specials` as a caption. The art is the season's own textless art; otherwise a series image no other season or the show poster uses; otherwise the show's art. |
| Episode thumbnail | The TMDB still. The bottom quarter is blurred and faded into the still's colour, with `EPISODE N` and the title. |
| Background | Textless TMDB art. No title. |

OCR rejects any art that prints the title or other large text. TMDB language tags are often wrong.

## How it runs

`posteryard serve` runs as a Docker service. See [compose.example.yml](compose.example.yml).

- **Plex webhook** (`library.new`): a new item is queued at once. A new episode also queues its season and show.
- **Sweep**, every `SWEEP_MINUTES`: items added or changed since the last sweep, every item Maintainerr lists, and failed items whose retry is due. A replaced file sends no webhook, so the sweep catches it. The first sweep after a start looks back 6 hours.
- **Full pass**, daily at `DAILY_AT` and at once after a version or setting change: every item. Items Plex no longer has are forgotten.

Each image is fingerprinted from what decides it: the chosen TMDB art, badges, label, service and version. An unchanged image is not rendered or uploaded again. OCR runs again only when TMDB's list of candidates changes.

With `DRY_RUN=true` images go to `DATA_DIR/previews` and nothing is written to Plex. Otherwise each image is uploaded, selected and locked, so a metadata refresh keeps it. If an uploaded image is later changed in Plex, Posteryard leaves that image alone. `posteryard forget RATING_KEY` hands it back.

Failed items are retried after 15 minutes, doubling to 12 hours. After three failures an ntfy alert goes out, one per upstream (Plex, TMDB, Maintainerr, missing artwork), at most once per 6 hours. While Maintainerr is down its last known schedule is used.

Posteryard must be the only thing writing posters, backgrounds and episode thumbnails. Turn off poster overlays in other tools.

Plex webhook URL (Plex Web > Settings > Webhooks): `http://HOST:8000/webhook/WEBHOOK_SECRET`. `GET /healthz` reports health, queue length and image counts.

## Settings

Set in the environment.

| Variable | Purpose | Default |
| --- | --- | --- |
| `TMDB_API_KEY` | TMDB v3 API key. Required. | |
| `PLEX_URL` | Plex server URL. Required for `serve` and Plex rating keys. | |
| `PLEX_TOKEN` | Plex token. Required for `serve` and Plex rating keys. | |
| `WEBHOOK_SECRET` | Random string, part of the webhook URL. Required for `serve`. | |
| `PLEX_LIBRARIES` | Libraries to manage, by name. | `Movies,TV Shows` |
| `DRY_RUN` | `true` writes previews only. `false` uploads to Plex. | `true` |
| `ONLY_RATING_KEYS` | Comma-separated rating keys to limit a rollout. A show key includes its seasons and episodes. Empty means all. | |
| `SWEEP_MINUTES` | Minutes between sweeps. | `15` |
| `DAILY_AT` | Local time of the daily full pass. | `04:15` |
| `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` | ntfy server, topic and access token for alerts. Empty turns alerts off. | |
| `LISTEN_PORT` | HTTP port inside the container. | `8000` |
| `MAINTAINERR_URL` | Maintainerr URL. Empty turns the label off. | |
| `STREAMING_REGIONS` | Regions to look up streaming services in, in order. | `CA,US` |
| `QUALITY_MIN_VIDEO` | Lowest resolution that gets a badge: `off`, `720`, `1080`, `2160`. | `2160` |
| `QUALITY_MIN_HDR` | Lowest HDR format that gets a badge: `off`, `hdr10`, `hdr10plus`, `dolbyvision`. | `hdr10` |
| `QUALITY_MIN_AUDIO` | Lowest audio that gets a badge: `off`, `5.1`, `7.1`, `atmos`. DTS:X counts as `atmos`. | `atmos` |
| `DATA_DIR` | Working folder: `state.db` and `previews/`. The image sets `/data`. | `data` |

Each axis shows at most one badge: the best format the file has, if it reaches the minimum.

## Preview

```sh
uv run posteryard preview 12345 12346        # Plex rating keys: movie, show, season or episode
uv run posteryard preview 12345 --episodes 2 # also the first 2 episodes of each season
uv run posteryard preview --tmdb movie:693134 --tmdb tv:95396 --season 2   # TMDB only, no Plex
```

## Development

```sh
uv sync
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
```

Python 3.13. `onnxruntime` has no Python 3.14 wheels for Intel Macs.

Service and Dolby marks: see [assets/marks-src/SOURCES.md](assets/marks-src/SOURCES.md). Font: Inter, SIL Open Font License, in `src/posteryard/assets/fonts/OFL.txt`.

## Releases

Semantic versioning. To release, move the `Unreleased` entries in [CHANGELOG.md](CHANGELOG.md) under the new version with its date, set `version` in `pyproject.toml`, commit, and push a `vX.Y.Z` tag. The tag builds the image and pushes `ghcr.io/sandobserver/posteryard:X.Y.Z` and `:X.Y`.
