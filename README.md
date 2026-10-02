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
    environment:
      - TMDB_API_KEY=your-tmdb-api-key
      - PLEX_URL=http://192.168.1.10:32400
      - PLEX_TOKEN=your-plex-token
      - WEBHOOK_SECRET=any-long-random-text
      - DRY_RUN=true
    volumes:
      - ./data:/data
    ports:
      - "8000:8000"
```

Change these:

| Setting | Set it to |
| --- | --- |
| `TMDB_API_KEY` | Your TMDB API key. |
| `PLEX_URL` | Your Plex address, as the container sees it. |
| `PLEX_TOKEN` | Your Plex token. |
| `WEBHOOK_SECRET` | Any long random text. `openssl rand -hex 16` makes one. |

```sh
mkdir -p data && sudo chown 1000:1000 data
docker compose up -d
```

Posteryard runs as user 1000, so it needs to own `./data`.

It starts with `DRY_RUN=true`: posters are saved to `./data/previews` and Plex is not touched. Happy with them? Set `DRY_RUN=false` and run `docker compose up -d` again. Posters are now uploaded and locked, so Plex keeps them.

With Plex Pass, add a webhook in Plex Web under **Settings > Webhooks** so new titles get posters right away: `http://YOUR-SERVER-IP:8000/webhook/YOUR-WEBHOOK-SECRET`. Without Plex Pass, they get them within 15 minutes.

Turn off poster overlays in other tools, such as Kometa. Two tools writing posters keep overwriting each other.

## Settings

Add any of these under `environment:`.

| Setting | What it does | Default |
| --- | --- | --- |
| `DRY_RUN` | `true` saves previews only. `false` uploads to Plex. | `true` |
| `PLEX_LIBRARIES` | The Plex libraries to manage, by name. | `Movies,TV Shows` |
| `ONLY_RATING_KEYS` | Only handle these titles, for a first test. A show includes its seasons and episodes. Empty means everything. | |
| `MAINTAINERR_URL` | Your [Maintainerr](https://github.com/Maintainerr/Maintainerr) address. Shows "Leaving in 3 days" on titles about to be removed. | |
| `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` | Your [ntfy](https://ntfy.sh) server, topic and token. Sends an alert when something keeps failing. | |
| `QUALITY_MIN_VIDEO` | Lowest resolution that gets a badge: `off`, `720`, `1080`, `2160`. | `2160` |
| `QUALITY_MIN_HDR` | Lowest HDR format that gets a badge: `off`, `hdr10`, `hdr10plus`, `dolbyvision`. | `hdr10` |
| `QUALITY_MIN_AUDIO` | Lowest audio that gets a badge: `off`, `5.1`, `7.1`, `atmos`. DTS:X counts as `atmos`. | `atmos` |
| `STREAMING_REGIONS` | Countries to look up the streaming service in, in order. | `CA,US` |
| `SWEEP_MINUTES` | How often to check Plex for new and changed titles. | `15` |
| `DAILY_AT` | Time of the daily full pass over the whole library. | `04:15` |
| `LISTEN_PORT` | Port inside the container. | `8000` |
| `DATA_DIR` | Where the database and previews are kept. | `/data` |

Each badge row shows at most one video, one HDR and one audio badge: the best the file has, if it reaches the minimum.

## Commands

Run them in the container: `docker exec posteryard posteryard COMMAND`.

| Command | What it does |
| --- | --- |
| `art next RATING_KEY` | Switch a movie, show or season to the next best art. |
| `art set RATING_KEY --url URL` | Use your own image as the art. `--file /data/my-art.jpg` takes a file from `./data`. |
| `art reset RATING_KEY` | Go back to automatic art. |
| `forget RATING_KEY` | Hand an image you changed in Plex back to Posteryard. |
| `preview RATING_KEY` | Save a preview to `./data/previews` without touching Plex. `--episodes 2` adds the first 2 episodes of each season. |
| `preview --tmdb movie:ID` | Preview any TMDB movie or show (`tv:ID`), even one not in Plex. `--season 2` adds a season. |

To find a rating key, open the title in Plex Web, choose **Get Info > View XML**, and read the number after `/library/metadata/` in the address bar.

## Plex labels

Add these labels to a title in Plex instead of running a command. Posteryard picks them up within 15 minutes.

| Label | What it does |
| --- | --- |
| `posteryard-next` | Switch to the next best art. The label is removed when done. |
| `posteryard-custom` | Keep the poster you uploaded in Plex as the art, with the title and badges drawn on top. Remove the label to go back to automatic art. |
| `posteryard-ignore` | Leave this title alone. On a show it covers the show only; label seasons separately. Remove the label and it is rendered fresh. |

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

Images that would come out the same are not rendered or uploaded again. If you change an image in Plex by hand, Posteryard leaves it alone until you run `forget`. Failed titles are retried from 15 minutes up to every 12 hours; after three failures ntfy gets one alert per cause, at most every 6 hours. `GET /healthz` reports health, queue length and image counts.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and releases. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Credits

Artwork and metadata come from [TMDB](https://www.themoviedb.org). Posteryard uses the TMDB API but is not endorsed or certified by TMDB. Service and Dolby marks: see [assets/marks-src/SOURCES.md](assets/marks-src/SOURCES.md). Font: Inter, SIL Open Font License, in `src/posteryard/assets/fonts/OFL.txt`.
