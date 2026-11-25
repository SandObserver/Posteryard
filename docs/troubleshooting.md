# Troubleshooting

[Back to the README](../README.md)


Read the log first: `docker logs --tail 100 posteryard`. After a start, Posteryard prints the settings it runs with. After that, each line has the time in UTC, a level (`INF`, `WRN` or `ERR`), the message, and details such as the title and the reason:

```text
2026-10-04T19:50:02.001Z WRN msg="item failed" title="The Invite" key=20106 attempt=1 reason="TMDB has no textless art for The Invite"
```

When Posteryard knows what to check, the line also has an `error_type`, a `hint` and a link to this page:

```text
2026-11-24T20:07:26.808Z WRN msg="item failed" title="2 Guns" key=73e8ff41162b9426e33d51a5a1223b54 attempt=1 reason="HTTP 500: Error processing request. for http://192.168.1.10:8096/Items/73e8ff41162b9426e33d51a5a1223b54/Images/Backdrop?imageIndex=0" error_type=server_image_change hint="Jellyfin could not change this image. The Jellyfin log names the cause. Often Jellyfin cannot write to the media folder" docs=https://github.com/SandObserver/Posteryard/blob/main/docs/troubleshooting.md
```

When many titles fail for the same setup problem, only the first line has the hint. One `more titles failed the same way` line then gives the count.

Set `LOG_LEVEL=debug` to see every item Posteryard checks.

| Message or reason | What to do |
| --- | --- |
| `library not found` | One name in `LIBRARIES` is not on the server. Use the names exactly as the sidebar shows them. |
| `Plex has no movie or TV library named ...` | Set `LIBRARIES` to the library names exactly as the Plex sidebar shows them. |
| `not matched to TMDB, match it in ... to get a poster` | The title is unmatched, or matched by an agent without TMDB, IMDb or TVDB ids, such as HAMA. Posteryard skips it until it is matched. In Plex, choose **Fix Match** or **Refresh Metadata**. In Jellyfin and Emby, choose **Identify**. |
| `TMDB has no textless art for ...` | TMDB has no usable art yet. Set `FANART_API_KEY` to try fanart.tv too, use `art set` with your own image, or add the `posteryard-ignore` label. |
| `error_type=server_unreachable` | Posteryard cannot reach the server. Check `PLEX_URL`, `JELLYFIN_URL` or `EMBY_URL` from inside the container: `docker exec posteryard python -c "import urllib.request; urllib.request.urlopen('http://192.168.1.10:32400/identity')"`. A host name such as `jellyfin` works only when both containers share a Docker network. Otherwise use the server's IP address. |
| `error_type=server_auth` | The server refused the key. `PLEX_TOKEN`, `JELLYFIN_API_KEY` or `EMBY_API_KEY` is wrong or was removed. |
| `error_type=server_tls` | The address starts with `https://`, but the server answers plain HTTP on that port. Use `http://`, or the port that serves HTTPS. |
| `error_type=server_image_change` | Jellyfin or Emby could not change an image. Jellyfin sends no reason, so read the server's own log, such as `docker logs jellyfin`. `Read-only file system` there means the media folder is mounted read-only (`:ro`) or is not writable for the server. Posteryard replaces images in the media folder when the server keeps artwork there. |
| `kept the new poster in its metadata folder` | Jellyfin could not replace the poster file in the media folder, so the old file comes back at the next library scan. Turn on **Save artwork into media folders** for the library and make the media folder writable for Jellyfin. If the old poster still comes back, delete `poster.jpg` from the title's folder and run `forget TITLE`. |
| `error_type=server_error` | The server failed the request. Its own log names the cause. |
| `error_type=tmdb_auth` | TMDB refused `TMDB_API_KEY`. Copy the API key or the API Read Access Token again from [TMDB](https://www.themoviedb.org/settings/api). |
| `error_type=tmdb_unreachable` | Posteryard cannot reach TMDB. Check the container's DNS and internet access. |
| `refused a redirect (HTTP 301)` from Plex | `PLEX_URL` points at an address that redirects, often `http://` where Plex or a proxy wants `https://`. Set `PLEX_URL` to the address the redirect goes to. Posteryard does not follow redirects, so the token never goes to another host. |
| `saved choices skipped: the state database could not be read` | `preview` could not read `state.db`, often because it comes from a newer release. The preview renders without art set by hand and the last known Maintainerr dates. `preview` never changes `state.db`. |
| `stopped before the current title finished` | Normal after `docker stop` while a poster was being drawn. Posteryard stops within 9 seconds so Docker does not kill it. The next sweep or daily check draws that title again. |
| A poster has art you do not expect | Run `why TITLE`. It lists the art checked in order and why each was used or not. `art next` switches to the next one. |
| A poster you set by hand is not replaced | Expected: Posteryard leaves hand-made changes alone. Run `forget TITLE` to hand it back. |

