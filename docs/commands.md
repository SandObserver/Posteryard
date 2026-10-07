# Commands and labels

[Back to the README](../README.md)

## Commands

Run a command inside the container:

```sh
docker exec posteryard posteryard art next The Office
```

On Unraid, use the container's **Console** and leave out `docker exec posteryard`, such as `posteryard find office`.

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
| `restore --all` | Give every image Posteryard uploaded back to the server's own. See [Uninstalling](maintenance.md#uninstalling). |
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

## Plex labels

Add these labels to a title in Plex instead of running a command. In Plex Web, open the title, choose **Edit** (the pencil), then **Tags**, and type the label under **Labels**. Posteryard picks it up at the next sweep, within 15 minutes.

| Label | What it does |
| --- | --- |
| `posteryard-next` | Switch to the next best art. The label is removed when done. With `DRY_RUN=true`, the label stays and nothing changes. |
| `posteryard-custom` | Keep the poster you uploaded in Plex as the art, with the title and badges drawn on top. Remove the label to go back to automatic art. |
| `posteryard-ignore` | Leave this title alone. On a show it covers the show only; label seasons separately. Remove the label and it is rendered fresh. |

On collections, only `posteryard-ignore` applies.

