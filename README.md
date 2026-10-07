<p align="center"><img src="docs/logo.svg" width="96" alt=""></p>

<h1 align="center">Posteryard</h1>

<p align="center"><b>Clean, consistent posters for Plex, Jellyfin and Emby, picked, rendered and kept current automatically.</b></p>

<p align="center">
  <a href="https://github.com/SandObserver/Posteryard/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/SandObserver/Posteryard/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/SandObserver/Posteryard/pkgs/container/posteryard"><img alt="ghcr.io" src="https://img.shields.io/badge/ghcr.io-posteryard-2496ED?logo=github&logoColor=white"></a>
</p>

<p align="center"><img src="docs/before-after.jpg" alt="Deadpool & Wolverine, Thunderbolts* and Lilo & Stitch, each as the official poster and as rendered by Posteryard with quality badges and a Maintainerr label"></p>

Posteryard runs next to Plex, [Jellyfin](#jellyfin) or [Emby](#emby), and gives every movie, show, season and episode the same calm look: clean art, the title logo in one fixed spot, and a soft fade. It finds the art, checks it, renders it, uploads it and keeps it up to date. You only step in when you want a different picture.

## Getting started

You need [Docker](https://docs.docker.com/get-started/get-docker/) on an x86-64 or ARM64 host, a free [TMDB API key](https://www.themoviedb.org/settings/api) and your [Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/). For Jellyfin or Emby, see [Jellyfin](#jellyfin) or [Emby](#emby).

### Create the compose file

```yaml
services:
  posteryard:
    image: ghcr.io/sandobserver/posteryard:latest
    container_name: posteryard
    restart: unless-stopped
    init: true
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    environment:
      # TMDB API Key or API Read Access Token
      - TMDB_API_KEY=your-tmdb-api-key
      # Plex's network address. Not localhost.
      - PLEX_URL=http://192.168.1.10:32400
      - PLEX_TOKEN=your-plex-token
      # Any long random text: openssl rand -hex 16
      - WEBHOOK_SECRET=any-long-random-text
      # The daily pass runs at DAILY_AT in this time zone
      - TZ=America/New_York
      - DRY_RUN=true
    volumes:
      - ./data:/data
    ports:
      - "8000:8000"
```

Plex in Docker on the same network? Use its container name, such as `http://plex:32400`.

### Start it

```sh
mkdir -p ./data && sudo chown 1000:1000 ./data
docker compose up -d
```

Posteryard runs as user 1000 and writes only to `./data` and `/tmp`.

Without Compose, the same container in one command:

```sh
mkdir -p ./data && sudo chown 1000:1000 ./data
docker run -d --name posteryard --restart unless-stopped \
  --init --read-only --tmpfs /tmp --cap-drop ALL \
  --security-opt no-new-privileges:true \
  -e TMDB_API_KEY=your-tmdb-api-key \
  -e PLEX_URL=http://192.168.1.10:32400 \
  -e PLEX_TOKEN=your-plex-token \
  -e WEBHOOK_SECRET=any-long-random-text \
  -e TZ=America/New_York -e DRY_RUN=true \
  -v ./data:/data -p 8000:8000 \
  ghcr.io/sandobserver/posteryard:latest
```

To go live, run `docker rm -f posteryard` and the same command with `DRY_RUN=false`.

### Preview, then go live

With `DRY_RUN=true`, Plex is not touched: images go to `./data/previews`, such as `5646-poster.jpg`. `docker logs -f posteryard` shows progress through the first full run.

When you like them, set `DRY_RUN=false` and run `docker compose up -d` again. Images are uploaded and locked, so a Plex refresh keeps them. `ONLY_RATING_KEYS` limits a first run to a few titles.

After that:

- With Plex Pass, add `http://SERVER-IP:8000/webhook/YOUR-WEBHOOK-SECRET` under **Settings > Webhooks** so new titles get posters right away. Without it, they follow within 15 minutes.
- Turn off poster overlays in other tools, such as Kometa, or the two overwrite each other.

## Settings

Add any of these under `environment:`.

### Start

| Setting | What it does | Default |
| --- | --- | --- |
| `DRY_RUN` | **Preview only, or upload.** `true` renders every image into `/data/previews` and changes nothing on your server. `false` uploads the images and locks them, so a metadata refresh keeps them. | `true` |
| `LIBRARIES` | **The libraries to manage.** Names exactly as your server shows them, separated by commas. Movie and TV libraries only. `PLEX_LIBRARIES` works too. | `Movies,TV Shows` |
| `ONLY_RATING_KEYS` | **Limit Posteryard to a few titles.** [Rating keys](#rating-keys) separated by commas; `posteryard find NAME` lists them. A show includes its seasons and episodes. Empty means every title. | |
| `JELLYFIN_URL`, `JELLYFIN_API_KEY` | **Use Jellyfin instead of Plex.** Set both and leave out `PLEX_URL` and `PLEX_TOKEN`. See [Jellyfin](#jellyfin). | |
| `EMBY_URL`, `EMBY_API_KEY` | **Use Emby instead of Plex.** Set both and leave out `PLEX_URL` and `PLEX_TOKEN`. See [Emby](#emby). | |

### Art

| Setting | What it does | Default |
| --- | --- | --- |
| `APPLE_ART` | **Fill gaps with Apple TV's key art.** `true` uses the art from a title's Apple TV page when TMDB has no textless poster for it, before TMDB backdrops. The art is found through the title's Apple TV id on Wikidata, in the store of your first `STREAMING_REGIONS` country. `art next`, `art set` and the labels still override it. Apple can change its pages without notice. | `false` |
| `FANART_API_KEY` | **A second source when TMDB has no clean art.** Your personal API key from your [fanart.tv](https://fanart.tv) profile. When TMDB has no usable art, title logo or backdrop for a title, Posteryard tries fanart.tv's, with the same check for printed text. | |
| `LOGO_LANGUAGES` | **Title logo languages, in order.** Two-letter codes separated by commas, such as `fr,en`. Posteryard uses the first language TMDB has a logo in. | `en` |
| `PREFER_WORDMARK` | **Prefer the title written out over an emblem.** When TMDB has both a wide logo with the name and a square emblem, `true` picks the wide one. | `true` |

### Badges

| Setting | What it does | Default |
| --- | --- | --- |
| `QUALITY_MIN_VIDEO` | **Lowest resolution that gets a badge.** `off`, `720`, `1080` or `2160`. With `2160`, only 4K files get a resolution badge. | `2160` |
| `QUALITY_MIN_HDR` | **Lowest HDR format that gets a badge.** `off`, `hdr10`, `hdr10plus` or `dolbyvision`. With `dolbyvision`, only Dolby Vision files get an HDR badge. | `hdr10` |
| `QUALITY_MIN_AUDIO` | **Lowest audio that gets a badge.** `off`, `5.1`, `7.1` or `atmos`. DTS:X counts as `atmos`. | `atmos` |
| `QUALITY_ACCESSIBILITY` | **Subtitle and audio description badges on movies.** Any of `sdh`, `cc` and `ad`, separated by commas, on their own line. Read from the track flags, or from track names such as "English (SDH)". Empty means none. | |

### Labels and marks

| Setting | What it does | Default |
| --- | --- | --- |
| `STATUS_LABELS` | **Labels for new titles, episodes and seasons.** A coloured label above the title: `JUST ADDED` for 14 days after a title arrives, `NEW EPISODE` or `NEW SEASON` for 7 days after one arrives, and `NEW SEASON OCT 21` from 30 days before a premiere. `false` turns these off; the Maintainerr label stays. | `true` |
| `MAINTAINERR_URL` | **Count down to Maintainerr deletions.** Your [Maintainerr](https://github.com/Maintainerr/Maintainerr) address. A title in a Maintainerr collection that deletes after a number of days gets a red label counting down to that day: `LEAVING IN 5 DAYS`, then `LEAVING TOMORROW`, then `LEAVING TODAY`. | |
| `STREAMING_REGIONS` | **Countries to find a show's streaming service in.** Two-letter country codes, in order. Posteryard goes through the countries in this order and uses the first streaming service it has a mark for. Outside the US, put your country first and keep `US` as a fallback, such as `GB,US`. | `US` |

### Seasons and collections

| Setting | What it does | Default |
| --- | --- | --- |
| `EPISODE_THUMBNAILS` | **How episode thumbnails look.** `plain` keeps the still with a light shade at the bottom. `titled` adds `EPISODE N` and the episode title. `off` leaves episodes alone and restores your server's own thumbnails. | `plain` |
| `COLLECTION_POSTERS` | **Posters for collections too.** `true` gives every collection a poster. A collection named after a streaming service, such as `Netflix`, gets Apple TV's channel tile: its newest show's art over a band with the service mark. Other collections get Apple TV's category tile: the newest title's art in one colour palette, with the collection's name. Collections named like an Apple TV genre, such as `Comedy` or `Sci-Fi`, use that genre's colours. | `false` |
| `SERVICE_COLLECTIONS` | **One collection per streaming service.** `true` keeps a collection for each service with at least 3 shows in each TV library. Posteryard only changes collections it made; they carry the `posteryard-collection` label. Nothing changes while `DRY_RUN` is on. Leave it off if another tool already makes these, and use `COLLECTION_POSTERS`. | `false` |

### Alerts and schedule

| Setting | What it does | Default |
| --- | --- | --- |
| `NOTIFY_URLS` | **Where alerts go.** [Apprise addresses](https://github.com/caronc/apprise/wiki#notification-services) separated by spaces or commas: Discord, Telegram, Gotify, Pushover, Slack, email, ntfy and about 100 more. See [Alerts](#alerts). | |
| `NOTIFY_EVENTS` | **Which alerts are sent.** `problems`, `new` and `summary`, separated by commas. See [Alerts](#alerts). | `problems` |
| `HEARTBEAT_URL` | **An address to call while healthy.** Posteryard calls it every minute while it works, such as an Uptime Kuma push URL. When the calls stop, your monitor alerts you. See [Monitoring](#monitoring). | |
| `SWEEP_MINUTES` | **Minutes between checks for new titles.** Each check handles titles added or changed since the last one, titles Maintainerr lists, and failed titles due for a retry. | `15` |
| `DAILY_AT` | **Time of the daily full pass.** A pass over every title, as `HH:MM` in your `TZ`. | `04:15` |

### Advanced

| Setting | What it does | Default |
| --- | --- | --- |
| `LOG_LEVEL` | **How much the log shows.** `debug`, `info`, `warning` or `error`. | `info` |
| `LISTEN_PORT` | **Port inside the container.** If you change it, change the right side of `ports:` to match. | `8000` |
| `DATA_DIR` | **Folder for the database, previews and custom art.** Inside the container. Keep the default and mount a volume there. | `/data` |
| `..._FILE` | **Read a secret from a file.** Add `_FILE` to a secret's name and give a path, such as a [Docker secret](https://docs.docker.com/compose/how-tos/use-secrets/): `PLEX_TOKEN_FILE=/run/secrets/plex_token`. Works for `TMDB_API_KEY`, `FANART_API_KEY`, `PLEX_TOKEN`, `JELLYFIN_API_KEY`, `EMBY_API_KEY`, `WEBHOOK_SECRET`, `NOTIFY_URLS` and `HEARTBEAT_URL`. | |

## Commands

Run a command inside the container:

```sh
docker exec posteryard posteryard art next The Office
```

Name a title the way Plex shows it. Case and punctuation do not matter, and quotes are optional. Add `--season N` to change one season of a show.

| Command | What it does |
| --- | --- |
| `art next TITLE` | Switch to the next best art. Run it again for the one after. |
| `art set TITLE --url URL` | Use your own image as the art. The title logo and badges are drawn on top. |
| `art set TITLE --file /data/my-art.jpg` | The same, with an image you put in `./data`. |
| `art reset TITLE` | Go back to automatic art. |
| `why TITLE` | Show the art in use, and every candidate checked with the reason it was used or not, such as text found on it. Changes nothing. |
| `forget TITLE` | You changed the poster in Plex and want Posteryard to manage it again. |
| `test-alert` | Send a test alert to every service in `NOTIFY_URLS`. |
| `health` | Exit with `0` when the running service is healthy, `1` when not. The Docker health check runs it. |
| `restore --all` | Give every image Posteryard uploaded back to the server's own. See [Uninstalling](#uninstalling). |
| `find WORDS` | List the movies and shows whose name contains `WORDS`, with their rating keys. |
| `preview TITLE` | Save the images to `./data/previews` without touching Plex. `--episodes 2` adds the first 2 episodes of each season. |
| `preview --tmdb movie:ID` | Preview any TMDB movie or show (`tv:ID`), even one not in Plex. |

Examples:

```sh
docker exec posteryard posteryard art next "Fly Me to the Moon"
docker exec posteryard posteryard art reset The Office --season 2
docker exec posteryard posteryard art set Dune 2021 --url https://example.org/dune.jpg
docker exec posteryard posteryard find office
```

With `DRY_RUN=true`, these commands save a preview instead of changing Plex.

### When a name matches more than one title

A command never guesses. If two titles share a name, nothing changes and Posteryard lists them:

```text
"Dune" matches 2 titles. Run again with one of these:
  Dune (1984, movie)   posteryard art next "Dune 1984"   or 4411
  Dune (2021, movie)   posteryard art next "Dune 2021"   or 8120
Nothing changed.
```

Add the year, or use the number shown. A misspelled name lists the closest titles instead.

### Rating keys

A rating key is the number Plex uses for a title. In Jellyfin and Emby it is the item ID. Every command accepts one in place of a name. `find` shows them. You only need them for `ONLY_RATING_KEYS`, or for a title whose name is a number, such as `1917`: write `1917 2019` or use its key.

## Jellyfin

Posteryard works with Jellyfin instead of Plex. It is tested on Jellyfin 12.1.

1. In the Jellyfin dashboard, open **API Keys**, add a key named `Posteryard`, and copy it.
2. In the compose file, replace `PLEX_URL` and `PLEX_TOKEN` with `JELLYFIN_URL=http://192.168.1.10:8096` and `JELLYFIN_API_KEY=your-key`. Set only one server.
3. Set `LIBRARIES` to your Jellyfin library names, such as `LIBRARIES=Movies,Shows`.

For new titles right away, install the **Webhook** plugin from the Jellyfin catalog. Add a **Generic** destination with the URL `http://SERVER-IP:8000/webhook/YOUR-WEBHOOK-SECRET`, the notification type **Item Added**, the item types you want, and **Send All Properties** on. Restart Jellyfin once after saving the destination; until then the plugin sends nothing. It sends new items about every 30 seconds. Without the plugin, new titles get posters at the next sweep.

Differences from Plex:

- The labels below are Jellyfin **tags**. Add them in **Edit metadata > Tags**.
- Jellyfin has no image lock. It keeps an uploaded image unless you refresh with **Replace existing images**.
- Jellyfin collections belong to no library. `COLLECTION_POSTERS` covers all of them, and `SERVICE_COLLECTIONS` keeps them for the first TV library in `LIBRARIES` only.

## Emby

Posteryard works with Emby instead of Plex. It is tested on Emby 4.10.1. Emby Premiere is not needed.

1. In the Emby dashboard, open **API Keys**, add a key named `Posteryard`, and copy it.
2. In the compose file, replace `PLEX_URL` and `PLEX_TOKEN` with `EMBY_URL=http://192.168.1.10:8096` and `EMBY_API_KEY=your-key`. Set only one server.
3. Set `LIBRARIES` to your Emby library names, such as `LIBRARIES=Movies,Shows`.

For new titles right away, open **Notifications** in the Emby dashboard and add a **Webhooks** notification. Use the URL `http://SERVER-IP:8000/webhook/YOUR-WEBHOOK-SECRET` and the event **New Media Added**. Both request types work. Without the webhook, new titles get posters at the next sweep.

Differences from Plex:

- The labels below are Emby **tags**. Add them in **Edit metadata > Tags**.
- Emby keeps an uploaded image unless you refresh with **Replace existing images**. Posteryard does not lock items, because a locked item gets no metadata updates.
- A refresh with **Replace all metadata** removes tags, including Posteryard's labels.

To move from Plex or Jellyfin to Emby, start with a new data folder. The data folder belongs to one server, and Posteryard refuses to start with a data folder from another server.

## Unraid

Save the template to your flash drive from the Unraid terminal:

```sh
curl -fsSL -o /boot/config/plugins/dockerMan/templates-user/my-Posteryard.xml \
  https://raw.githubusercontent.com/SandObserver/Posteryard/main/templates/posteryard.xml
```

Then pick **Posteryard** under **Docker**, **Add Container**, **Template**. Fill in the TMDB API key, the Plex URL and token, and a webhook secret. Previews land in `/mnt/user/appdata/posteryard/previews`.

The template runs Posteryard as Unraid's `nobody` user (`--user=99:100`) with the same lockdown as the compose file. Run commands from the container's **Console**, such as `posteryard find office`.

## Plex labels

Add these labels to a title in Plex instead of running a command. In Plex Web, open the title, choose **Edit** (the pencil), then **Tags**, and type the label under **Labels**. Posteryard picks it up at the next sweep, within 15 minutes.

| Label | What it does |
| --- | --- |
| `posteryard-next` | Switch to the next best art. The label is removed when done. With `DRY_RUN=true`, the label stays and nothing changes. |
| `posteryard-custom` | Keep the poster you uploaded in Plex as the art, with the title and badges drawn on top. Remove the label to go back to automatic art. |
| `posteryard-ignore` | Leave this title alone. On a show it covers the show only; label seasons separately. Remove the label and it is rendered fresh. |

On collections, only `posteryard-ignore` applies.

## Alerts

Posteryard sends an alert when a title fails three times in a row, when Maintainerr is unreachable, and when a scheduled run fails. Each cause sends at most one alert every 6 hours, also across restarts. When TMDB, the media server, Maintainerr, scheduled runs or service collections work again, a second alert says so. An alert that could not be delivered is tried again after 10 minutes.

`NOTIFY_EVENTS` sets which alerts are sent, such as `NOTIFY_EVENTS=problems,new`:

| Event | What is sent |
| --- | --- |
| `problems` | The problem alerts above. On by default. |
| `new` | New posters: one alert with up to 10 titles, at most every 15 minutes. A single new poster comes with the image if the service shows images. A new poster is the first one Posteryard uploads for a title the webhook or a sweep found. The full pass sends none. |
| `summary` | One alert after the daily full pass, only when something was updated or failed. |

Set `NOTIFY_URLS` to one or more Apprise addresses:

```yaml
      - NOTIFY_URLS=discord://WEBHOOK_ID/WEBHOOK_TOKEN tgram://BOT_TOKEN/CHAT_ID
```

Common addresses:

| Service | Address |
| --- | --- |
| ntfy | `ntfy://ntfy.sh/your-topic`, `ntfys://tk_ACCESS_TOKEN@your-server/topic` or `ntfys://user:password@your-server/topic` |
| Discord | `discord://WEBHOOK_ID/WEBHOOK_TOKEN` (the two parts of the webhook URL) |
| Telegram | `tgram://BOT_TOKEN/CHAT_ID` |
| Gotify | `gotifys://your-server/APP_TOKEN` |
| Pushover | `pover://USER_KEY@APP_TOKEN` |
| Email | `mailtos://user:password@gmail.com` |

Check the setup with `docker exec posteryard posteryard test-alert`. Posteryard refuses to start when an address is not one Apprise understands. The error names its position, never the address, because addresses hold secrets.

## Monitoring

The image has a Docker health check that calls `/healthz` on `LISTEN_PORT`. `docker ps` shows `healthy` or `unhealthy` within a few minutes of start. If one of Posteryard's internal threads stops, it sends an alert and exits, and `restart: unless-stopped` starts it again.

`GET /healthz` answers `200` when healthy and `503` when not:

```json
{"ok": true, "checks": {"threads_running": true, "worker_responsive": true, "sweep_recent": true},
 "last_sweep_seconds_ago": 312, "last_full_pass": "2026-10-03", "full_pass_running": false,
 "queue": 0, "images": {"uploaded": 2410}, "dry_run": false, "version": "0.7.0"}
```

| Check | Fails when |
| --- | --- |
| `threads_running` | The worker or the schedule has stopped. |
| `worker_responsive` | One title has been processing for more than 10 minutes. |
| `sweep_recent` | No sweep has succeeded for 3 sweep intervals (at least 15 minutes), for example while Plex is down. |

### Uptime Kuma

Use either monitor, or both:

- **HTTP**: add an **HTTP(s) - Keyword** monitor for `http://YOUR-SERVER-IP:8000/healthz` with the keyword `"ok": true`. It alerts when Posteryard is unhealthy or unreachable.
- **Push**: add a **Push** monitor with a heartbeat interval of 300 seconds, copy its push URL, and set it as `HEARTBEAT_URL`. Posteryard calls it every minute while healthy, so this works even when Uptime Kuma cannot reach port 8000. [healthchecks.io](https://healthchecks.io) ping URLs work the same way.

## What it makes

| Plex image | Design |
| --- | --- |
| Movie and show poster | Textless TMDB art, the title logo and a soft black fade, laid out like an Apple TV tile. One status or Maintainerr label sits above the logo. Under it: quality badges, then accessibility badges (movies); the logo moves up only as far as those lines need. Shows get their streaming service mark top left, clear of the unwatched count Plex draws top right. A title without a TMDB logo gets its name set in white. |
| Season poster | The same, with the season number large in the top left corner, and no service mark. Specials say `Specials` under the logo. Uses the season's own art, or a show image no other season uses. Crops and other versions of a picture count as the same picture. |
| Episode thumbnail | The episode still with a light bottom shade. With `EPISODE_THUMBNAILS=titled`, the bottom is blurred and faded, with `EPISODE N` and the title. |
| Background | Textless TMDB art, no title. |
| Collection poster | With `COLLECTION_POSTERS` or `SERVICE_COLLECTIONS`. Service collections: the newest show's art and logo over a band in the service's colour with its mark. Other collections: the newest title's art recoloured in a palette from Apple TV's genre tiles, two colours across the poster, with the collection's name bottom left (bottom right for right-to-left scripts). The name is always kept at 4.5:1 contrast. |

The streaming service is the first subscription, free or ad-supported offer in `STREAMING_REGIONS`, from TMDB's watch provider data by [JustWatch](https://www.justwatch.com). Stores, live TV, cable on-demand services such as Spectrum On Demand, and add-on channels sold through Amazon, Apple TV or Roku are skipped. Built-in marks: Netflix, Prime Video, Apple TV, Disney+, HBO Max, Hulu, Paramount+, Peacock, YouTube, Crave, Crunchyroll, Tubi, Pluto TV, Starz, MUBI, Viaplay, Sky, NOW, RTL+, Movistar Plus+, Channel 4 and ADN. Any other service gets its TMDB network logo in one colour, such as Exxen, SkyShowtime, U-NEXT, JioHotstar, BINGE, discovery+ or Shudder. When TMDB has no such logo, the mark is cut from the service's TMDB icon. Logos that would turn into a block or loose specks in one colour are left out, so the poster has no mark.

The service mark and season number are black or white, whichever reads better where they sit, with no shadow behind the mark. When a dark logo reads on the art, like Apple TV's New Releases row, the logo and badges are dark and the fade is left off. A one-colour logo is drawn dark itself, so every poster of a show keeps one logo design. Everywhere else the logo is white over the fade, deepened where needed so it always reads.

Art that prints the title or other large text is rejected, even when TMDB marks it as textless. Status labels such as `JUST ADDED` sit above the logo, so they never move it.

## How it works

- **Webhook**: a new title is handled right away. A new episode also refreshes its season and show.
- **Sweep**, every 15 minutes: titles added or changed since the last sweep, titles Maintainerr lists, and failed titles due for a retry.
- **Full pass**, daily and after an update or settings change: the whole library. Titles Plex no longer has are forgotten.

Webhook and label changes go ahead of a running full pass.

Images that would come out the same are not rendered or uploaded again. If you change an image in Plex by hand, Posteryard leaves it alone until you run `forget TITLE`. Failed titles are retried from 15 minutes up to every 12 hours. See [Alerts](#alerts) for what is sent when. See [Monitoring](#monitoring) for health checks.

## What it changes

Posteryard changes images, removes the `posteryard-next` label, and keeps the collections it made. It never edits titles, descriptions or other metadata, and it has no access to your media files. With `DRY_RUN=true` it changes nothing.

| What | When | How to undo |
| --- | --- | --- |
| Posters, backgrounds and episode thumbnails | For every title in `LIBRARIES`. Plex images are locked, so a refresh keeps them. | `restore --all` gives every image back and unlocks it. `EPISODE_THUMBNAILS=off` gives back episode thumbnails only. `posteryard-ignore` stops changes to one title. |
| Collection posters | With `COLLECTION_POSTERS` or `SERVICE_COLLECTIONS`. | `restore --all`. |
| The `posteryard-next` label | Removed after the art is switched. No other label on a title is added or removed. | Nothing to undo. |
| Collections | With `SERVICE_COLLECTIONS`: created per service, shows added and removed, and deleted below 3 shows. Only collections with the `posteryard-collection` label. | Delete the collections with the `posteryard-collection` label. |

On Jellyfin and Emby, a new background replaces all of a title's backgrounds, and `restore --all` deletes the uploaded image and refreshes the item, so the server downloads its own art again.

## Troubleshooting

Read the log first: `docker logs --tail 100 posteryard`. After a start, Posteryard prints the settings it runs with. After that, each line has the time in UTC, a level (`INF`, `WRN` or `ERR`), the message, and details such as the title and the reason:

```text
2026-10-04T19:50:02.001Z WRN msg="item failed" title="The Invite" key=20106 attempt=1 reason="TMDB has no textless art for The Invite"
```

Set `LOG_LEVEL=debug` to see every item Posteryard checks.

| Message or reason | What to do |
| --- | --- |
| `library not found` | One name in `LIBRARIES` is not on the server. Use the names exactly as the sidebar shows them. |
| `Plex has no movie or TV library named ...` | Set `LIBRARIES` to the library names exactly as the Plex sidebar shows them. |
| `... has no TMDB id, and TMDB knows no IMDb or TVDB id of it` | The title is unmatched, or matched by an agent without TMDB, IMDb or TVDB ids, such as HAMA. In Plex, choose **Fix Match** or **Refresh Metadata**. |
| `TMDB has no textless art for ...` | TMDB has no usable art yet. Set `FANART_API_KEY` to try fanart.tv too, use `art set` with your own image, or add the `posteryard-ignore` label. |
| `NewConnectionError for http://.../library/sections`, or `ConnectTimeoutError` or `NameResolutionError` |  Posteryard cannot reach Plex. Check `PLEX_URL` from inside the container: `docker exec posteryard python -c "import urllib.request; urllib.request.urlopen('http://192.168.1.10:32400/identity')"`. |
| `HTTP 401` from Plex | `PLEX_TOKEN` is wrong or expired. |
| A poster has art you do not expect | Run `why TITLE`. It lists the art checked in order and why each was used or not. `art next` switches to the next one. |
| A poster you set by hand is not replaced | Expected: Posteryard leaves hand-made changes alone. Run `forget TITLE` to hand it back. |

## Upgrading

```sh
docker compose pull && docker compose up -d
```

After an update, the first full pass checks every title. Only images whose design changed are rendered and uploaded again. Going back to an older release is not supported: it refuses to start with a database from a newer one.

## Backups

Back up `./data`. `state.db` records the art chosen for each title and what was uploaded, and `custom/` holds the art you set with `art set` or `posteryard-custom`. `previews/` can be deleted at any time.

## Uninstalling

1. Stop the service, so it uploads nothing new: `docker compose stop posteryard`.
2. Give every uploaded image back to the server's own and unlock it: `docker compose run --rm posteryard posteryard restore --all`. Images you changed by hand stay as they are.
3. With `SERVICE_COLLECTIONS`, delete the collections that carry the `posteryard-collection` label (a tag in Jellyfin and Emby).
4. Remove the container and its data: `docker compose down`, then delete `./data`.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and releases. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Credits

<a href="https://www.themoviedb.org"><img src="docs/tmdb.svg" alt="TMDB" height="12"></a>

Artwork and metadata come from [TMDB](https://www.themoviedb.org), and with `FANART_API_KEY` also from [fanart.tv](https://fanart.tv). Posteryard uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB. Streaming availability comes from [JustWatch](https://www.justwatch.com) through TMDB. Service and Dolby marks: see [assets/marks-src/SOURCES.md](assets/marks-src/SOURCES.md). Fonts, all under the SIL Open Font License: Inter, and for other scripts Vazirmatn (Persian, Arabic), Pretendard (Korean, Japanese) and Noto Sans (Chinese, Hebrew, Thai, Devanagari); licences in `src/posteryard/assets/fonts/`.

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for the name, the logo and third-party assets.
