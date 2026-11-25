"""What to check for a failure the user can fix. Each `kind` is listed on the troubleshooting page."""

from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urlsplit

from posteryard import http
from posteryard.jellyfin import KeptOutsideError

DOCS = "https://github.com/SandObserver/Posteryard/blob/main/docs/troubleshooting.md"
SETTINGS = {
    "Plex": ("PLEX_URL", "PLEX_TOKEN"),
    "Jellyfin": ("JELLYFIN_URL", "JELLYFIN_API_KEY"),
    "Emby": ("EMBY_URL", "EMBY_API_KEY"),
}
NO_ANSWER = ("NewConnectionError", "ConnectTimeoutError")


@dataclass(frozen=True)
class Hint:
    kind: str
    text: str
    # Every title fails the same way until the setup changes.
    shared: bool


def explain(exc: BaseException, server_name: str, server_url: str) -> Hint | None:
    if isinstance(exc, KeptOutsideError):
        return Hint(
            "media_folder_file",
            f'Turn on "Save artwork into media folders" and make the media folder writable for {exc.server}',
            shared=False,
        )
    if not isinstance(exc, http.RequestError):
        return None
    host = (urlsplit(exc.url).hostname or "").lower()
    if host and host == (urlsplit(server_url).hostname or "").lower():
        return _server(exc, server_name)
    if host.endswith(("themoviedb.org", "tmdb.org")):
        return _tmdb(exc)
    return None


def short_reason(exc: BaseException, found: Hint | None) -> str:
    """The reason without the request address. The error type already names the service."""
    return exc.reason if found is not None and isinstance(exc, http.RequestError) else str(exc)


def _server(exc: http.RequestError, name: str) -> Hint | None:
    address, key = SETTINGS.get(name, ("the server address", "the server key"))
    status = exc.status if isinstance(exc, http.HttpError) else 0
    if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return Hint("server_auth", f"Check {key}", shared=True)
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR and "Read-only file system" in exc.reason:
        return Hint("server_image_change", f"Make the media folder writable for {name}", shared=False)
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR and name != "Plex" and "/Images/" in urlsplit(exc.url).path:
        return Hint(
            "server_image_change", f"Read the {name} log. A read-only media folder is the usual cause", shared=False
        )
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return Hint("server_error", f"Read the {name} log", shared=False)
    if exc.reason.startswith("NameResolutionError"):
        return Hint(
            "server_unreachable",
            f"Use an IP address in {address}, or a container name on the same Docker network",
            shared=True,
        )
    if exc.reason.startswith("SSLError WRONG_VERSION_NUMBER"):
        return Hint("server_tls", f"Use http:// in {address}", shared=True)
    if exc.reason.startswith("TLS certificate refused"):
        return Hint("server_tls", f"Use http:// in {address}, or an address the certificate is valid for", shared=True)
    if exc.reason.startswith(NO_ANSWER):
        return Hint("server_unreachable", f"Check the address and port in {address}", shared=True)
    return None


def _tmdb(exc: http.RequestError) -> Hint | None:
    if isinstance(exc, http.HttpError) and exc.status == HTTPStatus.UNAUTHORIZED:
        return Hint("tmdb_auth", "Copy the key again from themoviedb.org/settings/api into TMDB_API_KEY", shared=True)
    if exc.reason.startswith(("NameResolutionError", *NO_ANSWER)):
        return Hint("tmdb_unreachable", "Check the container's DNS and internet access", shared=True)
    return None
