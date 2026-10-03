import ctypes
import ctypes.util
import gc
import sys
from functools import cache
from typing import Any


@cache
def _libc() -> Any:
    if not sys.platform.startswith("linux"):
        return None
    name = ctypes.util.find_library("c")
    return ctypes.CDLL(name) if name else None


def release() -> None:
    """glibc keeps freed heap pages for reuse. Without a trim, a long-running process only grows."""
    gc.collect()
    libc = _libc()
    if libc is not None and hasattr(libc, "malloc_trim"):
        libc.malloc_trim(0)
