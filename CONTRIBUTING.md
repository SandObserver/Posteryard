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

Render a few titles with `uv run posteryard preview` and look at them. A change that alters rendered images must bump `DESIGN_VERSION` in `src/posteryard/pipeline.py`. Without it, unchanged fingerprints keep old images in Plex.

## Branches and PRs

- Branch: `type/short-description`, where type is `feat`, `fix`, `perf`, `security`, `refactor`, `docs`, `chore` or `release`.
- PR title: `Type: short description`, where Type is `Fix`, `Feature`, `Add`, `Improve`, `Refactor`, `Security` or `Docs`.
- User-visible changes go under `Unreleased` in [CHANGELOG.md](CHANGELOG.md), one line each.

## Releases

Semantic versioning. Move the `Unreleased` entries in [CHANGELOG.md](CHANGELOG.md) under the new version with its date, set `version` in `pyproject.toml`, merge, and push a `vX.Y.Z` tag. The tag builds the image and pushes `ghcr.io/sandobserver/posteryard:X.Y.Z` and `:X.Y`.
