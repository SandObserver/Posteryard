"""What to check for a failure the user can fix. Each `kind` is listed on the troubleshooting page."""

from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urlsplit

from posteryard import http

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

    def fields(self) -> dict[str, str]:
        return {"error_type": self.kind, "hint": self.text, "docs": DOCS}


def explain(exc: BaseException, server_name: str, server_url: str) -> Hint | None:
    if not isinstance(exc, http.RequestError):
        return None
    host = (urlsplit(exc.url).hostname or "").lower()
    if host and host == (urlsplit(server_url).hostname or "").lower():
        return _server(exc, server_name)
    if host.endswith(("themoviedb.org", "tmdb.org")):
        return _tmdb(exc)
    return None


def _server(exc: http.RequestError, name: str) -> Hint | None:
    address, key = SETTINGS.get(name, ("the server address", "the server key"))
    status = exc.status if isinstance(exc, http.HttpError) else 0
    if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return Hint("server_auth", f"{name} refused the key. Check {key}", shared=True)
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR and name != "Plex" and "/Images/" in urlsplit(exc.url).path:
        return Hint(
            "server_image_change",
            f"{name} could not change this image. The {name} log names the cause."
            f" Often {name} cannot write to the media folder",
            shared=False,
        )
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return Hint("server_error", f"{name} failed this request. The {name} log names the cause", shared=False)
    if exc.reason.startswith("NameResolutionError"):
        return Hint(
            "server_unreachable",
            f"The host name in {address} is not known inside the container."
            " Use the server's IP address, or a container name on the same Docker network",
            shared=True,
        )
    if exc.reason.startswith("SSLError WRONG_VERSION_NUMBER"):
        return Hint(
            "server_tls",
            f"{address} starts with https://, but {name} answers without TLS on that port. Use http://",
            shared=True,
        )
    if exc.reason.startswith(NO_ANSWER):
        return Hint(
            "server_unreachable",
            f"Nothing answers at {address}. Check the address and port from inside the container",
            shared=True,
        )
    return None


def _tmdb(exc: http.RequestError) -> Hint | None:
    if isinstance(exc, http.HttpError) and exc.status == HTTPStatus.UNAUTHORIZED:
        return Hint(
            "tmdb_auth",
            "TMDB refused TMDB_API_KEY. Copy the API key or the API Read Access Token again"
            " from https://www.themoviedb.org/settings/api",
            shared=True,
        )
    if exc.reason.startswith(("NameResolutionError", *NO_ANSWER)):
        return Hint(
            "tmdb_unreachable",
            "TMDB is not reachable from inside the container. Check the container's DNS and internet access",
            shared=True,
        )
    return None
