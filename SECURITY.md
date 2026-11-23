# Security

## Reporting a vulnerability

Use **Report a vulnerability** on the repository's Security tab. Do not open a public issue.

## Supported versions

Only the latest release gets fixes.

## Running it safely

- Posteryard holds your Plex token. Keep `PLEX_TOKEN`, `TMDB_API_KEY`, `FANART_API_KEY`, `NOTIFY_URLS` and `WEBHOOK_SECRET` in the environment or in secret files (`PLEX_TOKEN_FILE` and so on), never in a committed file.
- The webhook URL contains `WEBHOOK_SECRET`. Use a long random value.
- Keep port 8000 on your home network. Do not forward it to the internet.
- To listen on one network only, put that address before the port, such as `"192.168.1.10:8000:8000"`. Docker otherwise publishes the port on every network the host is on.
- Images released after 0.10.5 carry a GitHub attestation. Check one with `gh attestation verify oci://ghcr.io/sandobserver/posteryard:latest --owner SandObserver`. The Docker Hub image has the same digest and checks the same way.
