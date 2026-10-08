# Troubleshooting

[Back to the README](../README.md)


Read the log first: `docker logs --tail 100 posteryard`. After a start, Posteryard prints the settings it runs with. After that, each line has the time in UTC, a level (`INF`, `WRN` or `ERR`), the message, and details such as the title and the reason:

```text
2026-10-04T19:50:02.001Z WRN msg="item failed" title="The Invite" key=20106 attempt=1 reason="TMDB has no textless art for The Invite"
```

Set `LOG_LEVEL=debug` to see every item Posteryard checks.

| Message or reason | What to do |
| --- | --- |
| `library not found` | One name in `LIBRARIES` is not on the server. Use the names exactly as the sidebar shows them. |
| `Plex has no movie or TV library named ...` | Set `LIBRARIES` to the library names exactly as the Plex sidebar shows them. |
| `not matched to TMDB, match it in ... to get a poster` | The title is unmatched, or matched by an agent without TMDB, IMDb or TVDB ids, such as HAMA. Posteryard skips it until it is matched. In Plex, choose **Fix Match** or **Refresh Metadata**. In Jellyfin and Emby, choose **Identify**. |
| `TMDB has no textless art for ...` | TMDB has no usable art yet. Set `FANART_API_KEY` to try fanart.tv too, use `art set` with your own image, or add the `posteryard-ignore` label. |
| `NewConnectionError for http://.../library/sections`, or `ConnectTimeoutError` or `NameResolutionError` |  Posteryard cannot reach Plex. Check `PLEX_URL` from inside the container: `docker exec posteryard python -c "import urllib.request; urllib.request.urlopen('http://192.168.1.10:32400/identity')"`. |
| `HTTP 401` from Plex | `PLEX_TOKEN` is wrong or expired. |
| `refused a redirect (HTTP 301)` from Plex | `PLEX_URL` points at an address that redirects, often `http://` where Plex or a proxy wants `https://`. Set `PLEX_URL` to the address the redirect goes to. Posteryard does not follow redirects, so the token never goes to another host. |
| A poster has art you do not expect | Run `why TITLE`. It lists the art checked in order and why each was used or not. `art next` switches to the next one. |
| A poster you set by hand is not replaced | Expected: Posteryard leaves hand-made changes alone. Run `forget TITLE` to hand it back. |

