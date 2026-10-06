import logging
import re
import sys

from posteryard import logfmt


def record(msg: str, level: int = logging.INFO, **extra: object) -> logging.LogRecord:
    rec = logging.makeLogRecord({"msg": msg, "levelno": level, "levelname": logging.getLevelName(level)})
    rec.__dict__.update(extra)
    return rec


def test_a_record_is_one_logfmt_line() -> None:
    line = logfmt.Formatter().format(record("poster uploaded", title="Crave", attempt=2, dry_run=False))
    assert re.fullmatch(
        r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z INF msg="poster uploaded" title=Crave attempt=2 dry_run=false', line
    )


def test_levels_use_three_letters() -> None:
    fmt = logfmt.Formatter()
    levels = [fmt.format(record("x", level)).split()[1] for level in (10, 20, 30, 40, 50)]
    assert levels == ["DBG", "INF", "WRN", "ERR", "ERR"]


def test_values_are_quoted_and_escaped() -> None:
    line = logfmt.Formatter().format(record("item failed", reason='TMDB said "no"\nINF msg=forged', empty=""))
    assert line.endswith(r'reason="TMDB said \"no\"\nINF msg=forged" empty=""')
    assert "\n" not in line
    assert logfmt.value("a=b") == '"a=b"'
    assert logfmt.value("\x07") == '"\\u0007"'
    assert logfmt.value(None) == ""


def test_an_exception_stays_on_one_line() -> None:
    try:
        raise ValueError("bad\nvalue")
    except ValueError:
        rec = logging.getLogger("t").makeRecord("t", logging.ERROR, "f", 1, "unexpected error", (), sys.exc_info())
    line = logfmt.Formatter().format(rec)
    assert "\n" not in line
    assert 'error="ValueError: bad\\nvalue"' in line
    assert 'stack="Traceback' in line


def test_durations_read_like_clock_time() -> None:
    assert logfmt.took(1.14) == "1.1s"
    assert logfmt.took(510) == "8m30s"
    assert logfmt.took(7500) == "2h05m"


def test_the_banner_aligns_labels() -> None:
    assert logfmt.banner("Posteryard 1.0", [("Server", "Plex"), ("Schedule", "daily")]).splitlines() == [
        "",
        "  Posteryard 1.0",
        "  Server     Plex",
        "  Schedule   daily",
    ]
