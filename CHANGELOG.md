# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Hold at most 16 downloaded images in memory, so the service no longer runs out of memory on large passes.
- Resume an unfinished full pass after a restart.

## [0.1.0] - 2026-10-01

### Added

- Render movie and show posters from the official English TMDB poster, confirmed by OCR.
- Render season posters with the season number unless the poster already prints it.
- Render episode thumbnails with a blurred, colour-faded bottom, episode number and title.
- Render textless backgrounds.
- Fall back to textless art with the title logo when no English poster passes.
- Show quality badges with separate minimums for video, HDR and audio.
- Show the Maintainerr leaving label.
- Show the streaming service mark on show and season posters.
- Add the `posteryard preview` command for Plex rating keys and TMDB ids.
- Add the `posteryard serve` service: Plex webhook, sweep, daily full pass, retries and ntfy alerts.
- Upload, select and lock images in Plex, or write previews only with `DRY_RUN=true`.
- Skip unchanged images by fingerprint and leave images changed by hand in Plex alone.
- Add the `posteryard forget` command.
- Add the Docker image.

[Unreleased]: https://github.com/SandObserver/Posteryard/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/SandObserver/Posteryard/releases/tag/v0.1.0
