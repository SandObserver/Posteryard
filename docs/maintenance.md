# Upgrade, back up, uninstall

[Back to the README](../README.md)

## Upgrading

```sh
docker compose pull && docker compose up -d
```

After an update, the first full pass checks every title. Only images whose design changed are rendered and uploaded again. Going back to an older release is not supported: it refuses to start with a database from a newer one.

## Backups

Back up `./data`. `state.db` records the art chosen for each title and what was uploaded, and `custom/` holds the art you set with `art set` or `posteryard-custom`. `previews/` can be deleted at any time.

## Uninstalling

1. Stop the service, so it uploads nothing new: `docker compose stop posteryard`.
2. Give every uploaded image back to the server's own and unlock it: `docker compose run --rm posteryard posteryard restore --all`. Images you changed by hand stay as they are.
3. With `SERVICE_COLLECTIONS`, delete the collections that carry the `posteryard-collection` label (a tag in Jellyfin and Emby).
4. Remove the container and its data: `docker compose down`, then delete `./data`.

