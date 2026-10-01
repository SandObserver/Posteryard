# Posteryard

Apple TV style artwork for the Plex `Movies` and `TV Shows` libraries.

| Plex image | Design |
| --- | --- |
| Movie poster | The official English poster from TMDB. Quality badges and the Maintainerr label top left. |
| Show poster | The official English poster. Streaming service mark top right, Maintainerr label top left. |
| Season poster | The season's official poster with `SEASON N` at the bottom, unless the poster already prints it. |
| Episode thumbnail | The TMDB still. The bottom quarter is blurred and faded into the still's colour, with `EPISODE N` and the title. |
| Background | Textless TMDB art. No title. |

OCR confirms that a poster prints the English title. TMDB language tags are often wrong. When no poster passes, the poster falls back to textless art with the title logo in Apple's tile layout.

## Status

Rendering only. `posteryard preview` writes images to a folder. Nothing is uploaded to Plex yet.

## Settings

Set in the environment.

| Variable | Purpose | Default |
| --- | --- | --- |
| `TMDB_API_KEY` | TMDB v3 API key. Required. | |
| `PLEX_URL` | Plex server URL. Required for Plex rating keys. | |
| `PLEX_TOKEN` | Plex token. Required for Plex rating keys. | |
| `MAINTAINERR_URL` | Maintainerr URL. Empty turns the label off. | |
| `STREAMING_REGIONS` | Regions to look up streaming services in, in order. | `CA,US` |
| `QUALITY_MIN_VIDEO` | Lowest resolution that gets a badge: `off`, `720`, `1080`, `2160`. | `2160` |
| `QUALITY_MIN_HDR` | Lowest HDR format that gets a badge: `off`, `hdr10`, `hdr10plus`, `dolbyvision`. | `hdr10` |
| `QUALITY_MIN_AUDIO` | Lowest audio that gets a badge: `off`, `5.1`, `7.1`, `atmos`. DTS:X counts as `atmos`. | `atmos` |
| `DATA_DIR` | Working folder. Previews go to `DATA_DIR/previews`. | `data` |

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
