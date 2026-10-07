# Jellyfin and Emby

[Back to the README](../README.md)

Follow [Getting started](../README.md#getting-started), with the changes below.

## Jellyfin

Posteryard works with Jellyfin instead of Plex. It is tested on Jellyfin 12.1.

1. In the Jellyfin dashboard, open **API Keys**, add a key named `Posteryard`, and copy it.
2. In the compose file, replace `PLEX_URL` and `PLEX_TOKEN` with `JELLYFIN_URL=http://192.168.1.10:8096` and `JELLYFIN_API_KEY=your-key`. Set only one server.
3. Set `LIBRARIES` to your Jellyfin library names, such as `LIBRARIES=Movies,Shows`.

For new titles right away, install the **Webhook** plugin from the Jellyfin catalog. Add a **Generic** destination with the URL `http://SERVER-IP:8000/webhook/YOUR-WEBHOOK-SECRET`, the notification type **Item Added**, the item types you want, and **Send All Properties** on. Restart Jellyfin once after saving the destination; until then the plugin sends nothing. It sends new items about every 30 seconds. Without the plugin, new titles get posters at the next sweep.

Differences from Plex:

- The [labels](commands.md#plex-labels) are Jellyfin **tags**. Add them in **Edit metadata > Tags**.
- Jellyfin has no image lock. It keeps an uploaded image unless you refresh with **Replace existing images**.
- Jellyfin collections belong to no library. `COLLECTION_POSTERS` covers all of them, and `SERVICE_COLLECTIONS` keeps them for the first TV library in `LIBRARIES` only.

## Emby

Posteryard works with Emby instead of Plex. It is tested on Emby 4.10.1. Emby Premiere is not needed.

1. In the Emby dashboard, open **API Keys**, add a key named `Posteryard`, and copy it.
2. In the compose file, replace `PLEX_URL` and `PLEX_TOKEN` with `EMBY_URL=http://192.168.1.10:8096` and `EMBY_API_KEY=your-key`. Set only one server.
3. Set `LIBRARIES` to your Emby library names, such as `LIBRARIES=Movies,Shows`.

For new titles right away, open **Notifications** in the Emby dashboard and add a **Webhooks** notification. Use the URL `http://SERVER-IP:8000/webhook/YOUR-WEBHOOK-SECRET` and the event **New Media Added**. Both request types work. Without the webhook, new titles get posters at the next sweep.

Differences from Plex:

- The [labels](commands.md#plex-labels) are Emby **tags**. Add them in **Edit metadata > Tags**.
- Emby keeps an uploaded image unless you refresh with **Replace existing images**. Posteryard does not lock items, because a locked item gets no metadata updates.
- A refresh with **Replace all metadata** removes tags, including Posteryard's labels.

To move from Plex or Jellyfin to Emby, start with a new data folder. The data folder belongs to one server, and Posteryard refuses to start with a data folder from another server.

