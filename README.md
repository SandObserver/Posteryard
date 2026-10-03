<p align="center"><img src="docs/logo.svg" width="96" alt=""></p>

<h1 align="center">Posteryard</h1>

<p align="center"><b>Apple TV style posters for Plex, picked, rendered and kept current automatically.</b></p>

<p align="center">
  <a href="https://github.com/SandObserver/Posteryard/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/SandObserver/Posteryard/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/SandObserver/Posteryard/pkgs/container/posteryard"><img alt="ghcr.io" src="https://img.shields.io/badge/ghcr.io-posteryard-2496ED?logo=github&logoColor=white"></a>
</p>

<p align="center"><img src="docs/before-after.jpg" alt="Deadpool & Wolverine, Thunderbolts* and Lilo & Stitch, each as the official poster and as rendered by Posteryard with quality badges and a Maintainerr label"></p>

Posteryard runs next to Plex and gives every movie, show, season and episode the same calm look: clean art, the title logo in one fixed spot, and a soft fade. It finds the art, checks it, renders it, uploads it and keeps it up to date. You only step in when you want a different picture.

## Getting started

You need [Docker](https://docs.docker.com/get-started/get-docker/), a free [TMDB API key](https://www.themoviedb.org/settings/api) (the short **API Key**) and your [Plex token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).

```yaml
services:
  posteryard:
    image: ghcr.io/sandobserver/posteryard:0.3
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
      - TMDB_API_KEY=your-tmdb-api-key
      - PLEX_URL=http://192.168.1.10:32400
      - PLEX_TOKEN=your-plex-token
      - WEBHOOK_SECRET=any-long-random-text
      - TZ=America/New_York
      - DRY_RUN=true
    volumes:
      - ./data:/data
    ports:
      - "8000:8000"
```

Change these:

| Setting | Set it to |
| --- | --- |
| `TMDB_API_KEY` | Your TMDB API key: the short **API Key**, not the long Read Access Token. |
| `PLEX_URL` | Your Plex server's address and port. Use the server's network IP, such as `http://192.168.1.10:32400`. `localhost` does not work: inside the container it means the container itself. If Plex runs in Docker on the same Docker network, use its container name, such as `http://plex:32400`. |
| `PLEX_TOKEN` | Your Plex token. |
| `WEBHOOK_SECRET` | Any long random text. `openssl rand -hex 16` makes one. |
| `TZ` | Your [time zone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones), so the daily pass runs at your local `DAILY_AT`. |

```sh
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d
```

Posteryard runs as user 1000, so it needs to own `./data`. The `read_only`, `cap_drop` and `security_opt` lines lock the container down; Posteryard writes only to `/data` and `/tmp`.

It starts with `DRY_RUN=true`: Plex is not touched. Every image is saved to `./data/previews` instead, named by rating key and image type, such as `5646-poster.jpg`. The first run renders the whole library and takes a while; `docker logs -f posteryard` shows progress.

Happy with the previews? Set `DRY_RUN=false` and run `docker compose up -d` again. Posters, backgrounds and episode thumbnails are now uploaded to Plex and locked, so a Plex metadata refresh keeps them. To try it on a few titles first, also set `ONLY_RATING_KEYS`.

With Plex Pass, add a webhook in Plex Web under **Settings > Webhooks** so new titles get posters right away: `http://SERVER-IP:8000/webhook/YOUR-WEBHOOK-SECRET`, where `SERVER-IP` is the address of the machine running Posteryard. Without Plex Pass, new titles get posters at the next sweep, within 15 minutes.

Turn off poster overlays in other tools, such as Kometa. Two tools writing posters keep overwriting each other.

## Settings

Add any of these under `environment:`.

| Setting | What it does | Default |
| --- | --- | --- |
| `DRY_RUN` | `true` saves previews only. `false` uploads to Plex. | `true` |
| `PLEX_LIBRARIES` | The Plex libraries to manage, by their exact names in the Plex sidebar, separated by commas. Movie and TV libraries only. | `Movies,TV Shows` |
| `ONLY_RATING_KEYS` | Only handle these titles, for a first test: [rating keys](#rating-keys) separated by commas. `find` shows them. A show includes its seasons and episodes. Empty means everything. | |
| `MAINTAINERR_URL` | Your [Maintainerr](https://github.com/Maintainerr/Maintainerr) address. Shows "Leaving in 3 days" on titles about to be removed. | |
| `NOTIFY_URLS` | Where to send alerts, as [Apprise addresses](https://github.com/caronc/apprise/wiki#notification-services) separated by spaces or commas. Discord, Telegram, Gotify, Pushover, Slack, email, ntfy and about 100 more. See [Alerts](#alerts). | |
| `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` | A direct [ntfy](https://ntfy.sh) server, topic and access token. An alternative to an `ntfy://` address in `NOTIFY_URLS`. | |
| `QUALITY_MIN_VIDEO` | Lowest resolution that gets a badge: `off`, `720`, `1080`, `2160`. | `2160` |
| `QUALITY_MIN_HDR` | Lowest HDR format that gets a badge: `off`, `hdr10`, `hdr10plus`, `dolbyvision`. | `hdr10` |
| `QUALITY_MIN_AUDIO` | Lowest audio that gets a badge: `off`, `5.1`, `7.1`, `atmos`. DTS:X counts as `atmos`. | `atmos` |
| `STREAMING_REGIONS` | Two-letter country codes to look up a show's streaming service in, in order. The first country with a known service wins. | `CA,US` |
| `SWEEP_MINUTES` | How often to check Plex for new and changed titles. | `15` |
| `DAILY_AT` | Time of the daily full pass over the whole library, as `HH:MM` in `TZ`. | `04:15` |
| `HEARTBEAT_URL` | An address to call every minute while Posteryard is healthy, such as an Uptime Kuma push URL. See [Monitoring](#monitoring). | |
| `LISTEN_PORT` | Port inside the container. If you change it, change the right side of `ports:` to match. | `8000` |
| `DATA_DIR` | Folder inside the container for the database, previews and custom art. Keep the default and mount a volume there. | `/data` |

Each badge row shows at most one video, one HDR and one audio badge: the best the file has, if it reaches the minimum.

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
| `forget TITLE` | You changed the poster in Plex and want Posteryard to manage it again. |
| `test-alert` | Send a test alert to every service in `NOTIFY_URLS` and ntfy. |
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

A rating key is the number Plex uses for a title. Every command accepts one in place of a name. `find` shows them. You only need them for `ONLY_RATING_KEYS`, or for a title whose name is a number, such as `1917`: write `1917 2019` or use its key.

## Plex labels

Add these labels to a title in Plex instead of running a command. In Plex Web, open the title, choose **Edit** (the pencil), then **Tags**, and type the label under **Labels**. Posteryard picks it up at the next sweep, within 15 minutes.

| Label | What it does |
| --- | --- |
| `posteryard-next` | Switch to the next best art. The label is removed when done. |
| `posteryard-custom` | Keep the poster you uploaded in Plex as the art, with the title and badges drawn on top. Remove the label to go back to automatic art. |
| `posteryard-ignore` | Leave this title alone. On a show it covers the show only; label seasons separately. Remove the label and it is rendered fresh. |

## Alerts

Posteryard sends an alert when a title fails three times in a row, when Maintainerr is unreachable, and when a scheduled run fails. Each cause sends at most one alert every 6 hours.

Set `NOTIFY_URLS` to one or more Apprise addresses:

```yaml
      - NOTIFY_URLS=discord://WEBHOOK_ID/WEBHOOK_TOKEN tgram://BOT_TOKEN/CHAT_ID
```

Common addresses:

| Service | Address |
| --- | --- |
| ntfy | `ntfy://ntfy.sh/your-topic`, or `ntfys://user:password@your-server/topic` |
| Discord | `discord://WEBHOOK_ID/WEBHOOK_TOKEN` (the two parts of the webhook URL) |
| Telegram | `tgram://BOT_TOKEN/CHAT_ID` |
| Gotify | `gotifys://your-server/APP_TOKEN` |
| Pushover | `pover://USER_KEY@APP_TOKEN` |
| Email | `mailtos://user:password@gmail.com` |

Check the setup with `docker exec posteryard posteryard test-alert`. Posteryard refuses to start when an address is not one Apprise understands. The error names its position, never the address, because addresses hold secrets.

## Monitoring

The image has a Docker health check. `docker ps` shows `healthy` or `unhealthy` within a few minutes of start. If one of Posteryard's internal threads stops, it sends an alert and exits, and `restart: unless-stopped` starts it again.

`GET /healthz` answers `200` when healthy and `503` when not:

```json
{"ok": true, "checks": {"threads_running": true, "worker_responsive": true, "sweep_recent": true},
 "last_sweep_seconds_ago": 312, "last_full_pass": "2026-10-03", "full_pass_running": false,
 "queue": 0, "images": {"uploaded": 2410}, "dry_run": false, "version": "0.4.0"}
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
| Movie and show poster | Textless TMDB art, the title logo in a fixed spot and a soft black fade. Quality badges (movies) and the Maintainerr label sit under the logo. Shows get their streaming service mark top right. |
| Season poster | The same, with `Season N` or `Specials` under the logo. Uses the season's own art, or a show image no other season uses. |
| Episode thumbnail | The episode still, with the bottom blurred and faded, `EPISODE N` and the title. |
| Background | Textless TMDB art, no title. |

Art that prints the title or other large text is rejected, even when TMDB marks it as textless.

## How it works

- **Webhook**: a new title is handled right away. A new episode also refreshes its season and show.
- **Sweep**, every 15 minutes: titles added or changed since the last sweep, titles Maintainerr lists, and failed titles due for a retry.
- **Full pass**, daily and after an update or settings change: the whole library. Titles Plex no longer has are forgotten.

Images that would come out the same are not rendered or uploaded again. If you change an image in Plex by hand, Posteryard leaves it alone until you run `forget TITLE`. Failed titles are retried from 15 minutes up to every 12 hours. See [Alerts](#alerts) for what is sent when. See [Monitoring](#monitoring) for health checks.

## Troubleshooting

Read the log first: `docker logs --tail 100 posteryard`.

| Message | What to do |
| --- | --- |
| `Plex has no movie or TV library named ...` | Set `PLEX_LIBRARIES` to the library names exactly as the Plex sidebar shows them. |
| `... has no TMDB id in Plex` | The title is unmatched or uses a legacy agent. In Plex, choose **Fix Match** or **Refresh Metadata**. |
| `TMDB has no textless art or title logo for ...` | TMDB has no usable art yet. Use `art set` with your own image, or add the `posteryard-ignore` label. |
| `URLError for http://.../library/sections` | Posteryard cannot reach Plex. Check `PLEX_URL` from inside the container: `docker exec posteryard python -c "import urllib.request; urllib.request.urlopen('http://192.168.1.10:32400/identity')"`. |
| `HTTP 401` from Plex | `PLEX_TOKEN` is wrong or expired. |
| A poster you set by hand is not replaced | Expected: Posteryard leaves hand-made changes alone. Run `forget TITLE` to hand it back. |

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and releases. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Credits

Artwork and metadata come from [TMDB](https://www.themoviedb.org). Posteryard uses the TMDB API but is not endorsed or certified by TMDB. Service and Dolby marks: see [assets/marks-src/SOURCES.md](assets/marks-src/SOURCES.md). Font: Inter, SIL Open Font License, in `src/posteryard/assets/fonts/OFL.txt`.
