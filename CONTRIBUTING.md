# Contributing

## Setup

```sh
uv sync
```

Python 3.13. `onnxruntime` has no Python 3.14 wheels for Intel Macs, and none at all after 1.23, so Intel Macs stay on `onnxruntime` 1.23 while Linux and the image get the latest.

## Run it locally

Put your settings in `.env` (ignored by git), then:

```sh
set -a && . ./.env && set +a
DATA_DIR=./data uv run posteryard preview "The Office"
DATA_DIR=./data uv run posteryard serve
```

`preview` never writes to the server. Keep `DRY_RUN=true` for `serve` unless you test uploads on a server you own.

To run the checks before each commit and the tests before each push:

```sh
uvx pre-commit install --hook-type pre-commit --hook-type pre-push
```

## How it fits together

A title goes through these modules in order:

| Module | Job |
| --- | --- |
| `service.py` | Webhook server, sweep, daily full pass and the work queue. |
| `worker.py` | Handles one item: labels, overrides, plans, upload or preview, failures and alerts. |
| `pipeline.py` | Plans the images for one item and fingerprints each plan before anything is drawn. |
| `artwork.py`, `ocr.py` | Choose TMDB art and logos; reject art with printed text. |
| `render/` | Draw the designs: `designs.py` composes, `category.py` draws collection tiles, `lines.py` places the lines under the logo, `badges.py` and `layers.py` draw parts. `layers.font_for` picks a fallback font for scripts Inter lacks; rebuild those fonts with `uv run python tools/build_fonts.py`. |
| `store.py` | SQLite state: what was uploaded, overrides, cached art choices. |
| `plex.py`, `jellyfin.py` | The media servers, behind the `MediaServer` protocol in `server.py`. Jellyfin and Emby (`jellyfin.py`) answer with Plex-shaped items. |
| `tmdb.py`, `maintainerr.py`, `notify.py`, `http.py` | Outside services. Every request goes through `http.request`, which redacts credentials. |
| `services.py`, `automarks.py` | Streaming marks: built-in marks first, then TMDB network logos matched through `assets/networks.json`, then a cut of the provider icon. Rebuild the table with `TMDB_API_KEY=... uv run python tools/build_networks.py`. |
| `cli.py`, `config.py` | Commands and settings. |
| `why.py` | The `why` command: checks a poster's art candidates again and names the reason for each. |
| `logfmt.py` | The log format and the startup block. |

Log a short fixed message and put the values in `extra`: `log.info("poster uploaded", extra={"title": name})`. Keys must not be `LogRecord` attribute names, such as `name`, `module` or `thread`: logging raises `KeyError`.

## Before opening a PR

CI runs the same checks:

```sh
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
```

`pytest` measures coverage and fails below 85% or on any warning. CI also lints the workflows with actionlint and zizmor, and scans the image with Grype; a fixable critical vulnerability fails the build.

Render a few titles with `uv run posteryard preview` and look at them. A change that alters rendered images must bump `DESIGN_VERSION` in `src/posteryard/pipeline.py`. Without it, unchanged fingerprints keep old images in Plex.

`tests/test_golden.py` compares every design with the reference images in `tests/golden`. It fails when the output changes and `DESIGN_VERSION` does not. After bumping it, write the references again and commit them:

```sh
UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py
```

A change to the database schema adds a new entry at the end of `MIGRATIONS` in `src/posteryard/store.py`. Never edit an entry that was released.

A new setting also goes into the Unraid template, `templates/posteryard.xml`, with its default as the value. `tests/test_unraid.py` fails when a template default does not load or a template setting is missing from `README.md`.

## Website

`site/` builds the landing page at https://posteryard.sandobserver.com. Its Getting started and Settings sections and the version come from `README.md` and `CHANGELOG.md` at build time, so edit those files, not the page. Each setting's description starts with a bold one-line summary; the build fails without one. The settings recipes above the list are page copy in `site/template.html`. Cloudflare Pages rebuilds the site on every push to `main`.

```sh
cd site && npm ci && npm run build
```

The build fails if a section it reads is missing from `README.md`.

The Plex preview shows either 12 random movies or 12 random shows from `site/posters.json` on each visit, as one Plex library. Each entry needs an image at `site/public/img/posters/<id>.webp`, 400 x 600, rendered with `posteryard preview --tmdb` and `STREAMING_REGIONS=US`.

## Branches and PRs

- Branch: `type/short-description`, where type is `feat`, `fix`, `perf`, `security`, `refactor`, `docs`, `chore` or `release`.
- PR title: `Type: short description`, where Type is `Fix`, `Feature`, `Add`, `Improve`, `Refactor`, `Security` or `Docs`.
- User-visible changes go under `Unreleased` in [CHANGELOG.md](CHANGELOG.md), one line each.

## Releases

Semantic versioning. Move the `Unreleased` entries in [CHANGELOG.md](CHANGELOG.md) under the new version with its date, set `version` in `pyproject.toml`, merge, and push a `vX.Y.Z` tag. The tag runs the CI checks, then pushes `ghcr.io/sandobserver/posteryard:X.Y.Z`, `:X.Y` and `:latest`, and publishes a GitHub release with the version's CHANGELOG section. The release stops if the tag, `pyproject.toml` and a dated CHANGELOG section do not agree. The image carries an SBOM and build provenance. A tag with a suffix, such as `v1.0.0-rc.1`, publishes a prerelease: only the `1.0.0-rc.1` image tag, and a GitHub release marked as prerelease.
