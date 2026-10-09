# Alerts and monitoring

[Back to the README](../README.md)

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
 "queue": 0, "images": {"uploaded": 2410}, "dry_run": false, "version": "0.8.1"}
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

