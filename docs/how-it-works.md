# How it works

[Back to the README](../README.md)

## What it makes

| Image | Design |
| --- | --- |
| Movie and show poster | Textless art and the title logo, laid out like an Apple TV tile, with a soft black fade where the logo needs it. One status or Maintainerr label sits above the logo. Under it: quality badges, then accessibility badges (movies); the logo moves up only as far as those lines need. Shows get their streaming service mark top left, clear of the unwatched count Plex draws top right. A title without a TMDB logo gets its name set in white. |
| Season poster | The same, with the season number large in the top left corner, and no service mark. Specials say `Specials` under the logo. Uses the season's own art, or a show image no other season uses. Crops and other versions of a picture count as the same picture. |
| Episode thumbnail | The episode still with a light bottom shade. With `EPISODE_THUMBNAILS=titled`, the bottom is blurred and faded, with `EPISODE N` and the title. |
| Background | Textless art, no title. |
| Collection poster | See [Collections](#collections). |

The service mark and season number are black or white, whichever reads better where they sit, with no shadow behind the mark. When a dark logo reads on the art, like Apple TV's New Releases row, the logo and badges are dark and the fade is left off. A one-colour logo is drawn dark itself, so every poster of a show keeps one logo design. Everywhere else the logo is white. On dark art, where the logo, labels and badges already read, there is no fade. Otherwise the logo sits over the fade, deepened where needed so it always reads.

## Where the art comes from

For a movie or show, Posteryard tries these sources in order and uses the first clean picture:

1. TMDB's textless posters.
2. With `APPLE_ART=true`, the key art from the title's Apple TV page. It is found through the title's Apple TV id on Wikidata, in the store of your first `STREAMING_REGIONS` country. Apple can change its pages without notice.
3. TMDB backdrops.
4. With `FANART_API_KEY`, fanart.tv's posters, then its backdrops. fanart.tv also fills in a missing title logo.

Backgrounds come from TMDB's textless backdrops, then, with `FANART_API_KEY`, from fanart.tv.

Art that prints the title or other large text is rejected, even when TMDB marks it as textless. `TEXT_CHECK=false` skips this check: Posteryard then uses about 350 MB less memory and renders much faster, but trusts TMDB's text-free label. In a test library, 3 to 5 in 100 posters then showed the title twice. Leave `APPLE_ART` off with `TEXT_CHECK=false`: Apple TV art is not labelled text-free, and about 1 in 6 has the title printed on it.

The title logo comes from TMDB, in the first of your `LOGO_LANGUAGES` that has one. When TMDB has both a wide logo with the name and a square emblem, `PREFER_WORDMARK=true` picks the wide one.

`art next`, `art set` and the [labels](commands.md#plex-labels) always win over the automatic choice.

## Labels

With `STATUS_LABELS=true`, a coloured label sits above the title: `JUST ADDED` for 14 days after a title arrives, `NEW EPISODE` or `NEW SEASON` for 7 days after one arrives, and `NEW SEASON OCT 21` from 30 days before a premiere. Labels sit above the logo, so they never move it.

With `MAINTAINERR_URL`, a title in a Maintainerr collection that deletes after a number of days gets a red label counting down to that day: `LEAVING IN 5 DAYS`, then `LEAVING TOMORROW`, then `LEAVING TODAY`. It stays when `STATUS_LABELS=false`.

## Streaming service

The streaming service is the first subscription, free or ad-supported offer in `STREAMING_REGIONS`, from TMDB's watch provider data by [JustWatch](https://www.justwatch.com). Posteryard goes through the countries in order and uses the first service it has a mark for. Stores, live TV, cable on-demand services such as Spectrum On Demand, and add-on channels sold through Amazon, Apple TV or Roku are skipped.

Built-in marks: Netflix, Prime Video, Apple TV, Disney+, HBO Max, Hulu, Paramount+, Peacock, YouTube, Crave, Crunchyroll, Tubi, Pluto TV, Starz, MUBI, Viaplay, Sky, NOW, RTL+, Movistar Plus+, Channel 4 and ADN. Any other service gets its TMDB network logo in one colour, such as Exxen, SkyShowtime, U-NEXT, JioHotstar, BINGE, discovery+ or Shudder. When TMDB has no such logo, the mark is cut from the service's TMDB icon. Logos that would turn into a block or loose specks in one colour are left out, so the poster has no mark.

## Collections

`COLLECTION_POSTERS=true` gives every collection a poster:

- A collection named after a streaming service, such as `Netflix` or `Netflix Movies`, gets Apple TV's channel tile: its newest show's art and logo over a band in the service's colour with its mark.
- Other collections get Apple TV's category tile: the newest title's art recoloured in two colours from Apple TV's genre tiles, with the collection's name bottom left (bottom right for right-to-left scripts). Collections named like an Apple TV genre, such as `Comedy` or `Sci-Fi`, use that genre's colours. The name is always kept at 4.5:1 contrast.

`SERVICE_COLLECTIONS=true` keeps a collection for each service with at least 3 shows in each TV library. Posteryard only changes collections it made; they carry the `posteryard-collection` label. Nothing changes while `DRY_RUN` is on. Leave it off if another tool already makes these, and use `COLLECTION_POSTERS`.

## When it runs

- **Webhook**: a new title is handled right away. A new episode also refreshes its season and show.
- **Sweep**, every `SWEEP_MINUTES` (15): titles added or changed since the last sweep, titles Maintainerr lists, and failed titles due for a retry.
- **Full pass**, daily at `DAILY_AT` and after an update or settings change: the whole library. Titles the server no longer has are forgotten.

Webhook and label changes go ahead of a running full pass.

Images that would come out the same are not rendered or uploaded again. If you change an image in Plex by hand, Posteryard leaves it alone until you run `forget TITLE`. Failed titles are retried from 15 minutes up to every 12 hours. See [Alerts](alerts-and-monitoring.md#alerts) for what is sent when, and [Monitoring](alerts-and-monitoring.md#monitoring) for health checks.

## What it changes

Posteryard changes images, removes the `posteryard-next` label, and keeps the collections it made. It never edits titles, descriptions or other metadata, and it has no access to your media files. With `DRY_RUN=true` it changes nothing.

| What | When | How to undo |
| --- | --- | --- |
| Posters, backgrounds and episode thumbnails | For every title in `LIBRARIES`. Plex images are locked, so a refresh keeps them. | `restore --all` gives every image back and unlocks it. `EPISODE_THUMBNAILS=off` gives back episode thumbnails only. `posteryard-ignore` stops changes to one title. |
| Collection posters | With `COLLECTION_POSTERS` or `SERVICE_COLLECTIONS`. | `restore --all`. |
| The `posteryard-next` label | Removed after the art is switched. No other label on a title is added or removed. | Nothing to undo. |
| Collections | With `SERVICE_COLLECTIONS`: created per service, shows added and removed, and deleted below 3 shows. Only collections with the `posteryard-collection` label. | Delete the collections with the `posteryard-collection` label. |

On Jellyfin and Emby, a new background replaces all of a title's backgrounds, and `restore --all` deletes the uploaded image and refreshes the item, so the server downloads its own art again.
