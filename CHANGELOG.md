# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] - 2026-10-02

### Added

- Use your own image as the poster art with the `posteryard-custom` Plex label or `posteryard art set`.
- Switch to the next best image with the `posteryard-next` Plex label or `posteryard art next`.
- Go back to automatic art with `posteryard art reset`.
- Leave an item completely alone with the `posteryard-ignore` Plex label.

## [0.2.2] - 2026-10-02

### Changed

- Run OCR in a separate process that is replaced after 100 reads, so its memory is returned in full.
- Return freed memory to the operating system after each item.
- Base fingerprints on a design version instead of the package version, so a release that renders the same images uploads nothing.

## [0.2.1] - 2026-10-02

### Fixed

- Shrink downloaded images to 2160 px and hold at most 8, so large TMDB originals no longer run the service out of memory.
- Run OCR on 2 threads and limit allocator arenas to keep memory under the container limit.

## [0.2.0] - 2026-10-02

### Changed

- Render every movie, show and season poster as Apple's tile: textless art, the title logo in a fixed box and a black bottom gradient.
- Move quality badges and the Maintainerr label to a row under the title logo.
- Give each season its own art, or a series image no other season uses, with a `Season N` caption.

### Removed

- Remove the studio poster design and the season number check.

### Fixed

- Queue one full pass, not two, when a restart and a settings change happen together.

## [0.1.1] - 2026-10-01

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

[Unreleased]: https://github.com/SandObserver/Posteryard/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/SandObserver/Posteryard/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/SandObserver/Posteryard/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/SandObserver/Posteryard/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/SandObserver/Posteryard/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/SandObserver/Posteryard/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/SandObserver/Posteryard/releases/tag/v0.1.0
