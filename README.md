<p align="center"><img src="docs/logo.svg" width="96" alt=""></p>

<h1 align="center">Posteryard</h1>

<p align="center"><b>Apple TV style posters for Plex, picked, rendered and kept current automatically.</b></p>

<p align="center">
  <a href="https://github.com/SandObserver/Posteryard/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/SandObserver/Posteryard/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/SandObserver/Posteryard/pkgs/container/posteryard"><img alt="ghcr.io" src="https://img.shields.io/badge/ghcr.io-posteryard-2496ED?logo=github&logoColor=white"></a>
</p>

<p align="center"><img src="docs/before-after.jpg" alt="Deadpool & Wolverine, Thunderbolts* and Lilo & Stitch, each as the official poster and as rendered by Posteryard with quality badges and a Maintainerr label"></p>

Posteryard runs next to Plex and gives every movie, show, season and episode the same calm look: clean art, the title logo in one fixed spot, and a soft fade. It finds the art, checks it, renders it, uploads it and keeps it up to date. You only step in when you want a different picture.

## Install

You need:

- Docker with Compose. Portainer, Dockge and Unraid stacks work too.
- A free TMDB API key. Sign up at [themoviedb.org](https://www.themoviedb.org/signup), then open [Settings > API](https://www.themoviedb.org/settings/api) and copy the **API Key** (the short one).
- Your Plex token. Plex shows how to find it: [Finding an authentication token](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/).
- Plex Pass, only for instant updates. Without it, new items still get their posters within 15 minutes.

### 1. Make a folder

```sh
mkdir -p posteryard/data && cd posteryard
sudo chown 1000:1000 data
```

Posteryard runs as user 1000, so it needs to own `data`.

### 2. Add two files

`compose.yaml`: copy [compose.example.yml](compose.example.yml) and change `PLEX_URL` to your Plex address, for example `http://192.168.1.10:32400`.

`.env`, next to it:

```sh
TMDB_API_KEY=your-tmdb-api-key
PLEX_TOKEN=your-plex-token
WEBHOOK_SECRET=any-long-random-text
```

`openssl rand -hex 16` makes a good secret. In Portainer, put these three in the stack's environment variables instead.

### 3. Try it without touching Plex

```sh
docker compose up -d
docker compose logs -f
```

It starts with `DRY_RUN=true`. Posters are saved to `data/previews` and nothing in Plex changes. A big library takes a while, so watch the log.

### 4. Turn it on

Happy with the previews? Set `DRY_RUN=false` in `compose.yaml` and run `docker compose up -d` again. Posteryard now uploads the posters and locks them, so Plex keeps them.

To try it on a few titles first, add `ONLY_RATING_KEYS=12345,67890`. To find a rating key, open the title in Plex Web, choose **Get Info > View XML**, and read the number after `/library/metadata/` in the address bar.

### 5. Instant updates (Plex Pass)

In Plex Web, open **Settings > Webhooks > Add Webhook** and enter:

```text
http://YOUR-SERVER-IP:8000/webhook/YOUR-WEBHOOK-SECRET
```

New movies and episodes now get their posters right after Plex adds them.

### Extras

- [Maintainerr](https://github.com/Maintainerr/Maintainerr): add `MAINTAINERR_URL=http://YOUR-SERVER-IP:6246` to show "Leaving in 3 days" on titles about to be removed.
- [ntfy](https://ntfy.sh) alerts when something keeps failing: add `NTFY_URL`, `NTFY_TOPIC` and, if needed, `NTFY_TOKEN`.
- Every other option is in [Settings](#settings).

Turn off poster overlays in other tools, such as Kometa. Two tools writing posters keep overwriting each other.

## What it makes

| Plex image | Design |
| --- | --- |
| Movie and show poster | Apple's tile: textless TMDB art, the title logo in Apple's fixed box, Apple's black bottom gradient. Quality badges (movies) and the Maintainerr label in a row under the logo. Streaming service mark top right on shows. |
| Season poster | The same tile with `Season N` or `Specials` as a caption. The art is the season's own textless art; otherwise a series image no other season or the show poster uses; otherwise the show's art. |
| Episode thumbnail | The TMDB still. The bottom quarter is blurred and faded into the still's colour, with `EPISODE N` and the title. |
| Background | Textless TMDB art. No title. |

OCR rejects any art that prints the title or other large text. TMDB language tags are often wrong.

## How it runs

Once it runs, Posteryard keeps itself up to date:

- **Plex webhook** (`library.new`): a new item is queued at once. A new episode also queues its season and show.
- **Sweep**, every `SWEEP_MINUTES`: items added or changed since the last sweep, every item Maintainerr lists, and failed items whose retry is due. A replaced file sends no webhook, so the sweep catches it. The first sweep after a start looks back 6 hours.
- **Full pass**, daily at `DAILY_AT` and at once after a version or setting change: every item. Items Plex no longer has are forgotten.

Each image is fingerprinted from what decides it: the chosen TMDB art, badges, label, service and the design version. A release that renders the same images keeps the design version and uploads nothing. An unchanged image is not rendered or uploaded again. OCR runs again only when TMDB's list of candidates changes.

With `DRY_RUN=true` images go to `DATA_DIR/previews` and nothing is written to Plex. Otherwise each image is uploaded, selected and locked, so a metadata refresh keeps it. If an uploaded image is later changed in Plex, Posteryard leaves that image alone. `posteryard forget RATING_KEY` hands it back.

Failed items are retried after 15 minutes, doubling to 12 hours. After three failures an ntfy alert goes out, one per upstream (Plex, TMDB, Maintainerr, missing artwork), at most once per 6 hours. While Maintainerr is down its last known schedule is used.

`GET /healthz` reports health, queue length and image counts.

## Choosing art

Posteryard picks the art itself. To change it for one movie, show or season, use Plex labels or a command.

| What you want | In Plex | Command |
| --- | --- | --- |
| Your own image as the poster art | Upload the image as the poster in Plex and add the label `posteryard-custom`. Remove the label to go back to automatic art. | `posteryard art set RATING_KEY --url https://...` or `--file /data/my-art.jpg` |
| The next best image | Add the label `posteryard-next`. Posteryard switches the art and removes the label. | `posteryard art next RATING_KEY` |
| Automatic art again | Remove `posteryard-custom`. | `posteryard art reset RATING_KEY` |
| No Posteryard changes at all | Add the label `posteryard-ignore`. Posteryard leaves that item's poster, background or thumbnail alone. On a show it covers the show only; label seasons separately. Remove the label to hand the item back: the next sweep renders it fresh, whatever poster was chosen meanwhile. | |

The title logo, gradient, badges, labels, service mark and season caption are drawn on top of the chosen art. Labels are picked up by the next sweep. Commands apply at once; run them in the container, for example `docker exec posteryard posteryard art next 12345`. A choice stays until it is reset.

## Settings

Set in the environment.

| Variable | Purpose | Default |
| --- | --- | --- |
| `TMDB_API_KEY` | TMDB v3 API key. Required. | |
| `PLEX_URL` | Plex server URL. Required for `serve` and Plex rating keys. | |
| `PLEX_TOKEN` | Plex token. Required for `serve` and Plex rating keys. | |
| `WEBHOOK_SECRET` | Random string, part of the webhook URL. Required for `serve`. | |
| `PLEX_LIBRARIES` | Libraries to manage, by name. | `Movies,TV Shows` |
| `DRY_RUN` | `true` writes previews only. `false` uploads to Plex. | `true` |
| `ONLY_RATING_KEYS` | Comma-separated rating keys to limit a rollout. A show key includes its seasons and episodes. Empty means all. | |
| `SWEEP_MINUTES` | Minutes between sweeps. | `15` |
| `DAILY_AT` | Local time of the daily full pass. | `04:15` |
| `NTFY_URL`, `NTFY_TOPIC`, `NTFY_TOKEN` | ntfy server, topic and access token for alerts. Empty turns alerts off. | |
| `LISTEN_PORT` | HTTP port inside the container. | `8000` |
| `MAINTAINERR_URL` | Maintainerr URL. Empty turns the label off. | |
| `STREAMING_REGIONS` | Regions to look up streaming services in, in order. | `CA,US` |
| `QUALITY_MIN_VIDEO` | Lowest resolution that gets a badge: `off`, `720`, `1080`, `2160`. | `2160` |
| `QUALITY_MIN_HDR` | Lowest HDR format that gets a badge: `off`, `hdr10`, `hdr10plus`, `dolbyvision`. | `hdr10` |
| `QUALITY_MIN_AUDIO` | Lowest audio that gets a badge: `off`, `5.1`, `7.1`, `atmos`. DTS:X counts as `atmos`. | `atmos` |
| `DATA_DIR` | Working folder: `state.db` and `previews/`. The image sets `/data`. | `data` |

Each axis shows at most one badge: the best format the file has, if it reaches the minimum.

## Preview

```sh
uv run posteryard preview 12345 12346        # Plex rating keys: movie, show, season or episode
uv run posteryard preview 12345 --episodes 2 # also the first 2 episodes of each season
uv run posteryard preview --tmdb movie:693134 --tmdb tv:95396 --season 2   # TMDB only, no Plex
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks and releases. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## Credits

Artwork and metadata come from [TMDB](https://www.themoviedb.org). Posteryard uses the TMDB API but is not endorsed or certified by TMDB. Service and Dolby marks: see [assets/marks-src/SOURCES.md](assets/marks-src/SOURCES.md). Font: Inter, SIL Open Font License, in `src/posteryard/assets/fonts/OFL.txt`.
