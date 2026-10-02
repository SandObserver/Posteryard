# Security

## Reporting a vulnerability

Use **Report a vulnerability** on the repository's Security tab. Do not open a public issue.

## Supported versions

Only the latest release gets fixes.

## Running it safely

- Posteryard holds your Plex token. Keep `PLEX_TOKEN`, `TMDB_API_KEY`, `NTFY_TOKEN` and `WEBHOOK_SECRET` in the environment, never in a committed file.
- The webhook URL contains `WEBHOOK_SECRET`. Use a long random value.
- Publish the port on `127.0.0.1` or a private network only, as in [compose.example.yml](compose.example.yml).
