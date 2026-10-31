# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Draw status labels such as `LEAVING IN 5 DAYS` and `JUST ADDED` in bold at full opacity, so they read clearly. Posters whose only reason for the fade was the label now have no fade.

### Fixed

- Read every title in a Maintainerr collection. Newer Maintainerr versions list only a few titles per collection, so the other titles had no `LEAVING` label.
- Print a message, not a traceback, when `why` or `preview` cannot read an image file, such as custom art removed from the data folder.

### Security

- Update the Python base image to 3.13.18, which fixes CVE-2026-115557.

## [0.10.1] - 2026-10-24

### Changed

- Give the channel tile of Crunchyroll, Tubi, MUBI, Viaplay, Movistar Plus+, ADN, Starz, Channel 4, Hayu, discovery+, Stan, BBC iPlayer, JioHotstar and CBC Gem a band in the service's own colour, darkened for a white mark, in place of grey.
- Accept any http or https address for `art set --url`, a server on the same machine included.
- Ask servers for compressed answers. A daily check of a 2,500-title Plex library downloads about 45% less.
- Use the jemalloc memory allocator in the image. Peak memory while new titles are processed is 12 to 25% lower.

### Fixed

- Leave an HTML error page out of a failed request's log line and alert, so the line shows the status and the address.
- Stop reading a compressed server answer at the 64 MB and time limits even when it unpacks to nothing, and refuse a compression Posteryard did not ask for.
- Pick the version of a movie that its quality badges describe only by the badge kinds that are on, so `QUALITY_MIN_VIDEO=off` no longer hides the Dolby Vision and Atmos badges of a 1080p version next to a 4K one.
- Read every version of a movie on Jellyfin and Emby for its badges, as Plex does, in place of only the first.
- Check art picked earlier again when the show or movie gains a title, such as a Live TV guide name, and pick new art when it prints that title.
- Check the art against a Live TV guide name also when the library looked the title up in the 10 minutes before.
- Pass over art more than four times as tall as it is wide, or as wide as it is tall, as reading its text could run out of memory.
- Refuse an `art set --file` over 40 MB before reading it.
- Pass over art that TMDB or fanart.tv lists but that is gone or not an image, in place of failing the title on every retry.
- Keep Apple TV art already picked while Apple TV is paused after a failed download, in place of switching those posters to a backdrop for an hour and back.
- Pause Apple TV art only for the title whose Apple TV image is gone, not for every title.
- Give a season its own clean art when the show has none, in place of failing the season.
- Keep `art next` on a show off the art its seasons already use.
- Keep a season that arrives after `art next` on its show off the show's new art.
- Step `art next` through Apple TV and fanart.tv art too, in the same order as the automatic choice.
- Show in `why` when a season's own art is skipped as the same picture as the show or another season, and list the show art a season falls back to.
- Keep the daily pass and the sweep running when a streaming service's TMDB icon or logo is gone or not an image, or the marks folder cannot be written. Such a service gets no mark.
- Send a collection create once, and take over a collection the server made when its answer was lost, in place of leaving an unlabelled copy.
- Make a streaming service's automatic mark again, and the posters that carry it, when TMDB changes the service's icon or network logo.
- Make an automatic mark again when its file is broken, and write mark files so a stop part way through leaves none.
- Keep a service collection for every streaming service with `SERVICE_COLLECTIONS`, not only the ones with a built-in mark, and give its collection the channel tile with the service's automatic mark.
- Keep one set of service collections for all Jellyfin TV libraries in `LIBRARIES`, in place of the first library the server lists.
- Add shows to a Jellyfin or Emby collection 100 at a time, as Jellyfin refuses a request for about 234 shows or more.
- Keep a show TMDB no longer has in the collections it is in, in place of keeping every service collection in that library from changing.
- Update the Maintainerr countdown on a leaving show's seasons at the same sweep as the show, in place of at the next daily pass.
- Show a season the sooner of its own and its show's Maintainerr date.
- Skip only the Maintainerr collection or title whose delete days or add date cannot be read, in place of the whole answer and a false outage alert.
- Label an upcoming special no longer as a `NEW SEASON` premiere, and a newly added special `NEW EPISODE` in place of `NEW SEASON`.
- Keep a background changed by hand when `art next`, `art set` or `art reset` changes the poster.
- Give back an episode thumbnail whose upload stopped halfway when `EPISODE_THUMBNAILS=off`, as `restore --all` does.
- Remove `posteryard-next` from an episode, in place of failing the episode at every sweep.
- Render a season or episode again at the next sweep when it loses `posteryard-ignore` and `ONLY_RATING_KEYS` names its show, in place of at the daily pass.
- Read a library one page at a time in the daily pass, so a large TV library no longer holds every episode in memory at once.
- Leave a season without a season number, such as Jellyfin's Season Unknown, and its episodes alone, in place of drawing it as Specials with Specials art and stills.
- Give a new season its own art when the show was read in the 10 minutes before it arrived, in place of the show's art.
- Step `art next` on a season through the season's other clean posters first.
- Keep the show image a season uses when a Specials season appears or another season is removed, in place of moving later seasons to other images.
- Lay out a Live TV title of hundreds of words in about a second, in place of tens of minutes.

### Security

- Update certifi to 2026.10.19 for GHSA-6q48-6c7w-9gw2.
- Update the Python base image to 3.13.17 with ca-certificates 20250419+deb13u1, which stops trusting the compromised emSign ECC Root CA - G3 (DSA-6572-1).
- Refuse a Live TV guide picture or channel logo on the Posteryard machine, a link-local address or a cloud metadata service, also after a redirect. Reported by @VIMS-Coder in #190.
- Read a Jellyfin or Emby XMLTV guide without keeping it in memory, and skip a guide with more than about 4 million entries, so a hostile guide cannot crash the container.
- Skip a Jellyfin or Emby XMLTV guide that declares its own entities, which could expand it to 100 times its size.
- Check where a Jellyfin or Emby XMLTV guide redirects to, as for a guide picture.
- Stop a request that takes more than 4 times its timeout, with its connection, retries and redirects, so a slow server cannot stall the worker. Read only the start of an error answer, and never the body of a redirect. Refuse a redirect to an address that can be read as two different hosts.
- Restart when one title has been processing for 30 minutes.
- Handle at most 16 webhook connections at once and close each after 15 seconds.
- Hide passwords and keys in every address in the log, also in messages from libraries.
- Limit memory to 1536 MB and processes to 512 in the compose example and the `docker run` command, and processes in the Unraid template.

## [0.10.0] - 2026-10-17

### Added

- Publish the image to Docker Hub as `sandobserver/posteryard` too, with the same tags as ghcr.io.

### Changed

- Give a Live TV title without art its channel's logo large over the logo's blurred colours, in place of the title on dark grey.
- Give a Live TV block named after its channel the same card, in place of art from the channel's show with the most airtime.
- List what Posteryard needs and its Live TV posters in the Unraid template.
- Install Posteryard on Unraid from the Apps tab, listed in Community Applications.
- Default the Unraid webhook port to 8098, as many Unraid apps already use 8000.
- Restart the Unraid container after a crash.
- Show the latest release notes in the Unraid template, and take its settings help from the README.
- Show the settings panel's lines for the install method picked on the landing page.
- Name Posteryard's version and project page in every web request, as Wikimedia asks of programs that download its logos.

### Fixed

- Show the Posteryard icon in the Unraid Docker tab, which cannot show SVG icons.
- Upload Live TV posters again at the next sweep when Plex deletes them while cleaning bundles.
- Give a Plex guide title a poster when Plex returns another entry for it, such as "News" for "News at Ten".

### Security

- Update Pillow to 13.0.0 for its JPEG decoder buffer overflow and other image decoding fixes.

## [0.9.0] - 2026-10-12

### Added

- Add posters for the Live TV guide on Plex and Jellyfin with `LIVE_TV`.

### Changed

- Try a request again at once, without a warning, when a server has dropped an idle connection.

### Security

- Update the Python base image to 3.13.16 for CVE-2026-19445.

## [0.8.1] - 2026-10-08

### Fixed

- Treat zoomed, cropped and widened versions of the same art as one picture, so `art next` and season posters move to different art.

### Security

- Send the Plex token in a header and refuse redirects from Plex, so a redirect cannot pass the token to another host.
- Refuse redirects for TMDB requests that carry an API key.
- Hide tokens and API keys in error messages when a server repeats them in its error page.

## [0.8.0] - 2026-10-07

### Added

- Add `TEXT_CHECK=false` to skip reading text on art, for machines with little memory such as a Raspberry Pi.
- Support Emby 4.10 as the media server, with `EMBY_URL` and `EMBY_API_KEY` and its built-in webhooks.
- Refuse to start with a data folder that belongs to another media server.
- Give collections that are not a streaming service Apple TV's category tile: the art in a genre palette with the name.
- Draw Persian, Arabic, Hebrew, Hindi, Thai, Chinese, Japanese and Korean names with matching fonts and shaping.
- Add a service mark for ADN (Anime Digital Network).
- Use TMDB network logos as streaming marks for services without a built-in mark, such as Exxen, SkyShowtime, U-NEXT, JioHotstar, BINGE, discovery+ and Shudder.
- Add `NOTIFY_EVENTS` to also send alerts for new posters, with the image, and a daily summary.
- Send an alert when TMDB, the media server, Maintainerr, scheduled runs or service collections work again.
- Print the settings in use at start, and log each name in `LIBRARIES` the server does not have.
- Document what Posteryard changes on the server and how to undo each change.
- Add the `why TITLE` command to list a poster's art candidates and why each was used or not.
- Add the `health` command, which the Docker health check now runs.
- Document starting Posteryard with `docker run` instead of Compose.

### Changed

- Leave the black fade off posters whose white logo, labels and badges already read on the art.
- Shorten the README to setup and settings, and move commands, server notes, alerts, troubleshooting and design details into pages in `docs/`.
- Read series art for season posters only when a season needs it, and stop at the first usable image. The first season poster of a show no longer reads up to 12 images it does not use.
- Stop the text reader after 10 idle minutes and drop downloaded images while idle, to use less memory. A read that hangs stops the text reader at once instead of holding up the worker.
- Write the log as logfmt lines with the title and the art source, one line per image, and end each full pass with a summary.
- Keep the 6-hour alert limit across restarts, and try an undelivered alert again after 10 minutes.
- Use TMDB's best-voted textless poster before Apple TV art; with `APPLE_ART`, Apple TV art now fills in before backdrops.
- Leave the unused sympy library out of the image, about 31 MB smaller.
- Process leaving titles again only when their Maintainerr date changes, and all of them once a day, instead of every sweep.
- Skip titles the server has not matched to TMDB, IMDb or TVDB with one log line, instead of failing, retrying and counting them in the daily summary.

### Fixed

- Queue only the titles in `ONLY_RATING_KEYS` in full passes and sweeps, instead of fetching every title to skip it.
- Measure the fade and logo ink again when the poster design changes, instead of reusing values from the old design. Every poster is measured once after the update.
- Delete cached art choices and measurements from older versions at start.
- Stop rejecting clean art when OCR reads a large shape, such as a window or an emblem, as one character. Art is checked again once after the update.
- Turn off onnxruntime's telemetry, which looked up Microsoft's telemetry server from the container, and its warning in the log.
- Hide image decoder messages from the debug log.
- Find Jellyfin and Emby titles by name and year, such as `why "Dune 2021"`.
- Stop within a second on `docker stop`, instead of after up to 30 seconds, which Docker ended with a kill after 10.
- Upload an image again when a stop or crash cut its upload off, instead of treating the poster as changed by hand, and give it back with `restore --all`.
- Ignore Plex webhooks with an invalid rating key, including a Jellyfin-style ID, instead of retrying them forever.
- Wait 2 minutes after a failed scheduled run, as the log says, instead of 30 seconds.
- Name the media server in the `--season` error, instead of always Plex.
- Keep a mistyped `NOTIFY_URLS` address, and the secret in it, out of the log.
- Match Jellyfin library folders on Windows servers, so their titles are no longer skipped.
- Give seasons of a Jellyfin or Emby show without season folders their show's library, so they are no longer skipped.
- Draw in dark ink only when the logo, the badges and the label each read where they sit.
- Leave out automatic streaming marks that turn into a solid shape or specks in one colour, such as Sony LIV, without showing another service's mark instead.
- Keep white letters of a coloured logo, such as the "HI" of HIDIVE, in its one-colour mark.
- Use the Sky mark for Sky X.
- Draw the streaming mark black or white without a shadow, by contrast where the mark sits.
- Keep one logo design per show: draw a one-colour logo dark on light art instead of switching to another logo.
- Choose a dark logo only when it reads where the logo sits, not across the whole bottom of the art.
- Treat crops and redrawn versions of a picture as the same picture when giving seasons their art.
- Keep `art next` working when art it skipped earlier has since been deleted from TMDB or Apple TV.
- Give collections that only start with a service's name, such as Max Payne Collection, a category tile instead of that service's channel tile.
- Select the right image when a render is identical to one uploaded before, so an expired label no longer stays on the poster.
- Give Sky service collections the channel tile instead of the category tile.
- Keep a show the server has not matched in the service collections it is in, instead of removing it or deleting a collection left with fewer than 3 shows.
- Count Maintainerr's leaving days in the container's time zone, so the label is no longer one day off.
- Skip only one image when removing the `posteryard-next` label fails and is retried.
- Remove a `posteryard-next` label typed with capital letters.
- Leave the `posteryard-next` label and the art unchanged while `DRY_RUN` is on.
- Run the Docker health check on `LISTEN_PORT` instead of port 8000.
- Retry and alert on unexpected errors like other failures.
- Keep the record of an uploaded episode thumbnail while `DRY_RUN` is on, so `restore --all` can still give it back.
- Keep the record of every uploaded image while `DRY_RUN` is on, so going live again leaves posters changed by hand alone and does not upload unchanged images again.

## [0.7.0] - 2026-10-04

### Added

- License Posteryard under the Apache License 2.0.
- Add an Unraid Community Applications template that runs Posteryard as Unraid's `nobody` user.
- Stop at startup with the path and user named when the data folder or its database is not writable.

### Removed

- Remove `NTFY_URL`, `NTFY_TOPIC` and `NTFY_TOKEN`. Send ntfy alerts through `NOTIFY_URLS`, such as `ntfys://tk_ACCESS_TOKEN@your-server/topic`. Posteryard refuses to start while the old settings are set.

### Fixed

- Use custom art from `art set` or `posteryard-custom` for titles that have no clean art on TMDB, Apple TV or fanart.tv, also while fanart.tv is down.
- Keep the last Apple TV art found, or use other art, when Apple TV or Wikidata cannot be reached or an Apple TV image cannot be loaded, instead of failing the title.
- Skip only a title's background when its backdrop cannot be looked up, instead of failing the whole title.
- Skip cable and live TV services such as Spectrum On Demand, Philo and Sling TV when choosing a show's streaming mark.

## [0.6.0] - 2026-10-04

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

[Unreleased]: https://github.com/SandObserver/Posteryard/compare/v0.10.1...HEAD
[0.10.1]: https://github.com/SandObserver/Posteryard/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/SandObserver/Posteryard/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/SandObserver/Posteryard/compare/v0.8.1...v0.9.0
[0.8.1]: https://github.com/SandObserver/Posteryard/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/SandObserver/Posteryard/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/SandObserver/Posteryard/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/SandObserver/Posteryard/compare/v0.5.1...v0.6.0
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
