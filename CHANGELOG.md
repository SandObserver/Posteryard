# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Use Apple TV's own key art as poster art with `APPLE_ART`; per-title overrides still win.
- Add service marks for Crunchyroll, Tubi, Pluto TV, Starz, MUBI, Viaplay, Sky, NOW, RTL+, Movistar Plus+ and Channel 4.
- Give other streaming services a mark cut from their TMDB icon when the icon is clean, such as BBC iPlayer, Stan, Canal+ and CBC Gem.
- Try fanart.tv art, title logos and backdrops when TMDB has none usable, with `FANART_API_KEY`.
- Find a title's TMDB id from its IMDb or TVDB id, including titles matched by Plex's legacy agents.
- Refuse to start with a `state.db` from a newer release, so a downgrade cannot damage it.
- Add `restore --all` to give every uploaded image back to the server's own before removing Posteryard.
- Read secrets from files with `TMDB_API_KEY_FILE`, `PLEX_TOKEN_FILE` and the other `_FILE` settings.
- Accept TMDB's API Read Access Token as `TMDB_API_KEY`.
- Set the log detail with `LOG_LEVEL`.
- Attach an SBOM and build provenance to the published image.

### Changed

- On light art, draw a dark title logo, captions, badges, season number and service mark without the black fade, when the art leaves near-black at 4.5:1 contrast and TMDB has a dark logo.
- Deepen the fade under a white logo until it reaches 4.5:1 contrast.
- Count The Roku Channel, Criterion Channel and Channel 4 as streaming services; only add-on channels sold through Amazon, Apple TV or Roku are skipped.
- Make fewer TMDB requests: one per title and one per season, instead of up to five per title and one per episode.
- Keep connections to Plex, Jellyfin and TMDB open between requests, and wait as long as a server asks with `Retry-After`.

### Fixed

- Delete custom art files once they are replaced or reset, so `./data/custom` no longer grows.
- Handle titles from the webhook and labels before the rest of a running full pass, instead of after it.

## [0.5.1] - 2026-10-03

### Removed

- Remove the status page at `/`; `/healthz` stays for monitoring.

## [0.5.0] - 2026-10-03

### Added

- Support Jellyfin with `JELLYFIN_URL` and `JELLYFIN_API_KEY`, including its Webhook plugin, tags as labels and collections.
- Accept `LIBRARIES` as the library list for either server; `PLEX_LIBRARIES` still works.
- Add collection posters with `COLLECTION_POSTERS`, with Apple TV's channel tile for streaming service collections.
- Keep one collection per streaming service in TV libraries with `SERVICE_COLLECTIONS`.
- Show coloured labels above the logo for just added titles, new episodes and seasons, and a season's start date; turn them off with `STATUS_LABELS=false`.
- Draw the season number large in the top left corner of season posters.
- Add SDH, CC and AD badges on their own line with `QUALITY_ACCESSIBILITY`.
- Choose episode thumbnails with `EPISODE_THUMBNAILS`: `plain`, `titled` or `off`.
- Choose title logo languages with `LOGO_LANGUAGES`.
- Prefer wide title logos over emblems, white ones first, then coloured ones such as red or yellow; turn it off with `PREFER_WORDMARK=false`.
- Set the title in white when TMDB has no title logo, instead of skipping the poster.
- Add a status page at `/` with health, schedule, failures and the latest images.
- Publish the image for 64-bit ARM hosts, such as a Raspberry Pi 4 or 5, as well as x86.

### Changed

- Place the title logo and the lines under it as on Apple TV tiles: the logo sits lower and moves up only for the lines present.
- Make episode thumbnails plain stills by default; set `EPISODE_THUMBNAILS=titled` for the previous design.
- Leave the streaming service mark off season posters.
- Look up streaming services in the US by default; set `STREAMING_REGIONS` for another country.
- Give every streaming service mark the same visual size, so stacked marks such as HBO Max are no longer small, and darken the corner behind it until the white mark reaches 4.5:1 contrast.
- Use the `latest` image tag in the README and the compose example.
- Move the streaming service mark to the top left corner, where Plex's unwatched count and watched checkmark do not cover it.

### Fixed

- Draw captions translucent as designed; they were fully opaque.
- Render titles whose TMDB original is too large to load, such as Fallout and Challengers, from TMDB's 1280 px copy.

## [0.4.0] - 2026-10-03

### Added

- Accept title names in commands, such as `art next The Office`, plus `--season N`; a name that matches several titles or none changes nothing.
- Add `find WORDS` to list matching movies and shows with their rating keys.
- Send alerts to Discord, Telegram, Gotify, email and about 100 other services through Apprise with `NOTIFY_URLS`.
- Add `test-alert` to check the notification setup.
- Call `HEARTBEAT_URL`, such as an Uptime Kuma push monitor, every minute while healthy.
- Report each health check, the last sweep and the last full pass on `/healthz`, and answer `HEAD /healthz`.
- Exit when an internal thread stops, so the container restart policy starts a fresh one.

### Changed

- Update onnxruntime to 1.30 in the image.
- Explain each setup value, rating keys, Plex labels and common errors in the README.
- Lock down the example compose file with a read-only filesystem, no capabilities and `no-new-privileges`.
- Make 2 fewer TMDB requests per title.
- Render a title right away after `forget`, which now takes one title instead of several rating keys.
- Take one title in `preview`, with `--season N` for seasons of a Plex show.

### Fixed

- Keep the schedule running after an unexpected error, alert about it, and report unhealthy if a service thread stops.
- Recheck known titles missing from a full pass one by one instead of forgetting them, so manual changes survive a Plex or settings hiccup.
- Stop a full pass and alert when no Plex library matches `PLEX_LIBRARIES`, and reject an empty `PLEX_LIBRARIES`.
- Ignore malformed Maintainerr answers instead of failing every title.
- Answer malformed webhook requests with an error instead of dropping the connection.
- Refuse images over 50 megapixels and downloads over 64 MB before they use up the container's memory.
- Reject settings and rating keys written with non-ASCII digits.

## [0.3.1] - 2026-10-02

### Fixed

- Render an item fresh when its `posteryard-ignore` label is removed, instead of treating a poster chosen meanwhile as a manual change.

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

[Unreleased]: https://github.com/SandObserver/Posteryard/compare/v0.5.1...HEAD
[0.5.1]: https://github.com/SandObserver/Posteryard/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/SandObserver/Posteryard/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/SandObserver/Posteryard/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/SandObserver/Posteryard/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/SandObserver/Posteryard/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/SandObserver/Posteryard/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/SandObserver/Posteryard/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/SandObserver/Posteryard/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/SandObserver/Posteryard/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/SandObserver/Posteryard/releases/tag/v0.1.0
