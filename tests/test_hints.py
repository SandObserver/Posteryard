import pytest

from posteryard import http
from posteryard.hints import explain
from posteryard.jellyfin import KeptOutsideError

JELLYFIN = "http://jellyfin.example:8096"
ITEM = f"{JELLYFIN}/Items/{'a' * 32}"
TMDB = "https://api.themoviedb.org/3/movie/42"


@pytest.mark.parametrize(
    ("exc", "server", "kind", "says"),
    [
        (http.HttpError(401, f"{JELLYFIN}/Library/VirtualFolders"), "Jellyfin", "server_auth", "JELLYFIN_API_KEY"),
        (http.HttpError(403, "http://plex.example:32400/library/sections"), "Plex", "server_auth", "PLEX_TOKEN"),
        (http.HttpError(500, f"{ITEM}/Images/Backdrop?imageIndex=0"), "Jellyfin", "server_image_change", "read-only"),
        (http.HttpError(500, f"{ITEM}/Images/Primary"), "Emby", "server_image_change", "the Emby log"),
        (http.HttpError(500, "http://plex.example:32400/library/metadata/1/posters"), "Plex", "server_error", "log"),
        (
            http.TemporaryError("NameResolutionError", f"{JELLYFIN}/System"),
            "Jellyfin",
            "server_unreachable",
            "IP address",
        ),
        (http.TemporaryError("NewConnectionError", f"{JELLYFIN}/System"), "Jellyfin", "server_unreachable", "port"),
        (http.TemporaryError("ConnectTimeoutError", f"{JELLYFIN}/System"), "Jellyfin", "server_unreachable", "port"),
        (
            http.TemporaryError("SSLError WRONG_VERSION_NUMBER", "https://jellyfin.example:8096/System"),
            "Jellyfin",
            "server_tls",
            "http://",
        ),
        (KeptOutsideError("Jellyfin", "poster", f"{ITEM}/Images/Primary"), "Jellyfin", "media_folder_file", "writ"),
        (http.HttpError(401, TMDB), "Jellyfin", "tmdb_auth", "TMDB_API_KEY"),
        (http.TemporaryError("NameResolutionError", TMDB), "Jellyfin", "tmdb_unreachable", "DNS"),
    ],
)
def test_known_failures_say_what_to_check(exc: http.RequestError, server: str, kind: str, says: str) -> None:
    url = "http://plex.example:32400" if server == "Plex" else JELLYFIN
    found = explain(exc, server, url)
    assert found is not None
    assert found.kind == kind
    assert says in found.text


@pytest.mark.parametrize(
    "exc",
    [
        http.HttpError(404, TMDB),
        http.HttpError(502, "https://assets.fanart.tv/x.jpg"),
        http.TemporaryError("response too slow", f"{JELLYFIN}/Items"),
        ValueError("broken image"),
    ],
)
def test_other_failures_have_no_hint(exc: Exception) -> None:
    assert explain(exc, "Jellyfin", JELLYFIN) is None


def test_a_server_error_from_another_host_is_not_the_server() -> None:
    assert explain(http.HttpError(401, "http://other.example:8096/Items"), "Jellyfin", JELLYFIN) is None


def test_a_refused_certificate_says_what_to_use() -> None:
    exc = http.RequestError("TLS certificate refused: IP address mismatch", "https://192.0.2.5:32400/identity")
    found = explain(exc, "Plex", "https://192.0.2.5:32400")
    assert found is not None
    assert (found.kind, found.text) == (
        "server_tls",
        "Use http:// in PLEX_URL, or an address the certificate is valid for",
    )


def test_a_server_that_names_a_read_only_folder_gets_a_direct_hint() -> None:
    exc = http.HttpError(
        500, "http://emby.example:8096/Items/7/Images/Primary", "Read-only file system : '/media/x.jpg'"
    )
    found = explain(exc, "Emby", "http://emby.example:8096")
    assert found is not None
    assert (found.kind, found.text) == ("server_image_change", "Make the media folder writable for Emby")
