import ipaddress
import os
import re
import socket
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from posteryard import config, http
from posteryard.render import layers
from posteryard.store import Store

# Dates use the local zone. The tests expect UTC.
os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()

SETTINGS = sorted(
    set(re.findall(r'"([A-Z][A-Z0-9_]+)"', Path(config.__file__).read_text(encoding="utf-8")))
    | {f"{name}_FILE" for name in config.SECRETS}
)


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in SETTINGS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def close_stores(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    opened: list[Store] = []
    original = Store.__init__

    def tracked(self: Store, path: Path) -> None:
        original(self, path)
        opened.append(self)

    monkeypatch.setattr(Store, "__init__", tracked)
    yield
    for store in opened:
        store.close()


@pytest.fixture(autouse=True)
def fresh_module_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(http, "_holds", {})
    monkeypatch.setattr(layers, "MARK_DIRS", [])
    layers._mark.cache_clear()
    yield
    layers._mark.cache_clear()


RESERVED = (".example", ".test", ".invalid")


def _address(host: object) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(str(host).split("%")[0])
    except ValueError:
        return None


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reached: list[str] = []
    lookup, connect = socket.getaddrinfo, socket.socket.connect

    def guarded_lookup(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in (None, "localhost") and _address(host) is None:
            if not str(host).rstrip(".").endswith(RESERVED):
                reached.append(f"lookup {host}")
            raise socket.gaierror(socket.EAI_NONAME, "tests have no network")
        return lookup(host, *args, **kwargs)

    def guarded_connect(self: socket.socket, address: Any) -> None:
        ip = _address(address[0])
        if self.family in (socket.AF_INET, socket.AF_INET6) and not (ip and ip.is_loopback):
            reached.append(f"connect {address[0]}")
            raise ConnectionRefusedError("tests have no network")
        connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_lookup)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    yield
    assert not reached, f"the test tried to reach the network: {', '.join(reached)}"
