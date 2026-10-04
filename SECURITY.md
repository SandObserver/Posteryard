# Security

## Reporting a vulnerability

Use **Report a vulnerability** on the repository's Security tab. Do not open a public issue.

## Supported versions

Only the latest release gets fixes.

## Running it safely

- Posteryard holds your Plex token. Keep `PLEX_TOKEN`, `TMDB_API_KEY`, `FANART_API_KEY`, `NTFY_TOKEN` and `WEBHOOK_SECRET` in the environment or in secret files (`PLEX_TOKEN_FILE` and so on), never in a committed file.
- The webhook URL contains `WEBHOOK_SECRET`. Use a long random value.
- Keep port 8000 on your home network. Do not forward it to the internet.
