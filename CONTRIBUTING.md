# Contributing

## Setup

```sh
uv sync
```

Python 3.13. `onnxruntime` has no Python 3.14 wheels for Intel Macs, and none at all after 1.23, so Intel Macs stay on `onnxruntime` 1.23 while Linux and the image get the latest.

## Before opening a PR

CI runs the same checks:

```sh
uv run ruff format . && uv run ruff check . && uv run mypy && uv run pytest
```

`pytest` measures coverage and fails below 85% or on any warning.

Render a few titles with `uv run posteryard preview` and look at them. A change that alters rendered images must bump `DESIGN_VERSION` in `src/posteryard/pipeline.py`. Without it, unchanged fingerprints keep old images in Plex.

A change to the database schema adds a new entry at the end of `MIGRATIONS` in `src/posteryard/store.py`. Never edit an entry that was released.

## Website

`site/` builds the landing page at https://posteryard.sandobserver.com. Its Getting started and Settings sections and the version come from `README.md` and `CHANGELOG.md` at build time, so edit those files, not the page. Cloudflare Pages rebuilds the site on every push to `main`.

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

Semantic versioning. Move the `Unreleased` entries in [CHANGELOG.md](CHANGELOG.md) under the new version with its date, set `version` in `pyproject.toml`, merge, and push a `vX.Y.Z` tag. The tag runs the CI checks, then pushes `ghcr.io/sandobserver/posteryard:X.Y.Z`, `:X.Y` and `:latest`, and publishes a GitHub release with the version's CHANGELOG section. The release stops if the tag, `pyproject.toml` and a dated CHANGELOG section do not agree.
