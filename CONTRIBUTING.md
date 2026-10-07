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
| `settings.py`, `sources.py`, `measures.py` | What a plan reads: the settings, art and metadata from TMDB, fanart.tv and Apple TV, and measurements of art and logos saved in `state.db`. `pipeline.Context` holds one of each. |
| `artwork.py`, `ocr.py` | Choose TMDB art and logos; reject art with printed text. |
| `render/` | Draw the designs: `designs.py` composes, `category.py` draws collection tiles, `lines.py` places the lines under the logo, `badges.py` and `layers.py` draw parts. `layers.font_for` picks a fallback font for scripts Inter lacks. |
| `store.py` | SQLite state: what was uploaded, overrides, cached art choices. |
| `plex.py`, `jellyfin.py` | The media servers, behind the `MediaServer` protocol in `server.py`. Jellyfin and Emby (`jellyfin.py`) answer with Plex-shaped items. |
| `tmdb.py`, `maintainerr.py`, `notify.py`, `http.py` | Outside services. Every request goes through `http.request`, which redacts credentials. |
| `services.py`, `automarks.py` | Streaming marks: built-in marks first, then TMDB network logos matched through `assets/networks.json` (and `assets/provider_networks.json` for a service with no network in the region), then a cut of the provider icon. |
| `cli.py`, `config.py` | Commands and settings. |
| `why.py` | The `why` command: checks a poster's art candidates again and names the reason for each. |
| `logfmt.py` | The log format and the startup block. |

Log a short fixed message and put the values in `extra`: `log.info("poster uploaded", extra={"title": name})`. Keys must not be `LogRecord` attribute names, such as `name`, `module` or `thread`: logging raises `KeyError`.

## Scripts in `tools/`

| Script | Job | Needs |
| --- | --- | --- |
| `build_marks.py` | Turns `assets/marks-src` into the white marks in `src/posteryard/assets/marks`. | |
| `build_fonts.py` | Fetches the fallback fonts for scripts Inter lacks. | Internet |
| `build_networks.py` | Rebuilds `assets/networks.json` and `assets/provider_networks.json` from TMDB. | `TMDB_API_KEY` |
| `ocr_readings.py` | Adds real OCR readings to `tests/ocr_readings.json`. | `TMDB_API_KEY` |

Run each with `uv run python tools/<script>`.

## Adding a streaming mark

1. Put the logo in `assets/marks-src/<key>.svg`. It must be public domain or CC0. Add its source and license to `assets/marks-src/SOURCES.md`. Use a `.png` only when no vector version exists; its white ink becomes the mark.
2. Run `uv run python tools/build_marks.py` and commit the new file in `src/posteryard/assets/marks`.
3. In `src/posteryard/services.py`, add the provider names TMDB uses to `SERVICE_PATTERNS` and the display name to `NAMES`.
4. Add the provider names to `test_new_services_and_add_on_channels` in `tests/test_services.py`. `test_every_service_has_a_built_in_mark` fails when a key in `NAMES` has no mark file.
5. Add the service to the "Built-in marks" list in `docs/how-it-works.md`.

## Before opening a PR

CI runs the same checks:

```sh
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
```

The tests need no API keys and no internet connection. Ruff limits each function's complexity, branches and arguments. Split a new function that goes over a limit instead of adding a `noqa`. `pytest` measures coverage and fails below 85% or on any warning. CI also lints the workflows with actionlint and zizmor, and scans the image with Grype; a fixable critical vulnerability fails the build.

Shared test doubles live in `tests/fakes.py`. `FakeServer` and `FakeTmdb` subclass the real `MediaServer` and `Tmdb`, so mypy checks every override; a test overrides only the calls it needs. Replace a method in a test with `monkeypatch.setattr`, not assignment.

Render a few titles with `uv run posteryard preview` and look at them. A change that alters rendered images must bump `DESIGN_VERSION` in `src/posteryard/pipeline.py`. Without it, unchanged fingerprints keep old images in Plex. The bump also measures the fade and logo ink of every poster again.

`tests/test_golden.py` compares every design with the reference images in `tests/golden`. It fails when the output changes and `DESIGN_VERSION` does not. After bumping it, write the references again and commit them:

```sh
UPDATE_GOLDEN=1 uv run pytest tests/test_golden.py
```

`tests/test_ocr_readings.py` checks the text rule against what OCR read on real TMDB art, kept as text in `tests/ocr_readings.json`. Add art the rule judges wrongly with `TMDB_API_KEY=... uv run python tools/ocr_readings.py movie:ID:/path.jpg=clean`, or `=text` for art with printed text. A change to the rule that changes past decisions bumps `TEXTLESS_RULE` in `artwork.py`, so cached choices are made again. A change to the logo choice bumps `LOGO_RULE`. `serve` deletes cached rows from older versions at start.

A change to the database schema adds a new entry at the end of `MIGRATIONS` in `src/posteryard/store.py`. Never edit an entry that was released.

A new setting also goes into the Unraid template, `templates/posteryard.xml`, with its default as the value, and into `site/settings.json`. `tests/test_unraid.py` fails when a template default does not load or a template setting is missing from `README.md`.

## Website

`site/` builds the landing page at https://posteryard.sandobserver.com. The version comes from `CHANGELOG.md`. Cloudflare Pages rebuilds the site on every push to `main`.

The install block comes from `site/public/setup.mjs`. The build fails when its compose file, `docker run` command or Unraid command differs from `README.md`, so change both together.

The settings section reads `site/settings.json`: each setting's label, control, default and choices. `tests/test_site_settings.py` fails when that file disagrees with `config.py` or the README, and names each setting that is missing. A setting the page leaves out goes under `elsewhere`, with the reason.

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
