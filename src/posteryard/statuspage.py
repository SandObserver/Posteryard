"""The read-only status page: health, schedule, failures and the latest images."""

import html
import re
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from posteryard.store import Record, Status

THUMB_WIDTH = 240
KEEP = 60
THUMB_NAME = re.compile(r"^[A-Za-z0-9]+-(poster|art|thumb)\.jpg$")
LABELS = {
    Status.UPLOADED: "uploaded",
    Status.PREVIEW: "preview",
    Status.MANUAL: "changed by hand",
    Status.FAILED: "failed",
}

STYLE = """
:root { --bg: #f5f5f7; --panel: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #d2d2d7;
  --ok: #248a3d; --bad: #d70015; --accent: #b38f00; color-scheme: light dark; }
@media (prefers-color-scheme: dark) { :root { --bg: #000; --panel: #1c1c1e; --fg: #f5f5f7;
  --muted: #98989d; --line: #38383a; --ok: #30d158; --bad: #ff453a; --accent: #ffd60a; } }
body { margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 32px 16px 64px; display: grid; gap: 28px; }
h1 { font-size: 26px; margin: 0; } h2 { font-size: 17px; margin: 0 0 10px; }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; }
.fact { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 12px 14px; }
.fact b { display: block; font-size: 13px; color: var(--muted); font-weight: 500; }
.ok { color: var(--ok); } .bad { color: var(--bad); }
table { width: 100%; border-collapse: collapse; background: var(--panel); border-radius: 10px; overflow: hidden; }
th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font-size: 12px; color: var(--muted); font-weight: 500; text-transform: uppercase; letter-spacing: .05em; }
.wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 14px; }
.wall figure { margin: 0; display: grid; gap: 4px; }
.wall img { width: 100%; border-radius: 8px; background: var(--line); display: block; }
.wall figcaption { font-size: 12px; color: var(--muted); overflow-wrap: anywhere; }
.muted { color: var(--muted); }
"""


def save_thumb(directory: Path, rating_key: str, target: str, image: Image.Image) -> None:
    """Keep a small copy of the latest images for the page, and only the newest KEEP of them."""
    directory.mkdir(parents=True, exist_ok=True)
    small = image.convert("RGB")
    small.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 2), Image.Resampling.LANCZOS)
    small.save(directory / f"{rating_key}-{target}.jpg", quality=80)
    files = sorted(directory.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP:]:
        old.unlink(missing_ok=True)


def thumb_path(directory: Path, name: str) -> Path | None:
    if not THUMB_NAME.match(name):
        return None
    path = directory / name
    return path if path.is_file() else None


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds} s ago"
    if seconds < 5400:
        return f"{seconds // 60} min ago"
    if seconds < 172800:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} days ago"


def _fact(name: str, value: str, good: bool | None = None) -> str:
    cls = "" if good is None else (' class="ok"' if good else ' class="bad"')
    return f'<div class="fact"><b>{html.escape(name)}</b><span{cls}>{html.escape(value)}</span></div>'


def render(status: Mapping[str, Any], recent: Sequence[Record], failures: Sequence[Record], thumbs: Path) -> str:
    now = time.time()
    counts = status.get("images", {})
    facts = [
        _fact("Health", "healthy" if status["ok"] else "unhealthy", bool(status["ok"])),
        _fact("Mode", "dry run, previews only" if status["dry_run"] else "live, uploads to the server"),
        _fact("Last sweep", f"{status['last_sweep_seconds_ago']} s ago"),
        _fact(
            "Last full pass",
            str(status.get("last_full_pass") or "not yet") + (", running" if status.get("full_pass_running") else ""),
        ),
        _fact("Queue", str(status["queue"])),
        _fact("Images", ", ".join(f"{n} {LABELS.get(Status(k), k)}" for k, n in sorted(counts.items())) or "none"),
    ]
    checks = "".join(
        f'<tr><td>{html.escape(name.replace("_", " "))}</td><td class="{"ok" if ok else "bad"}">'
        f"{'yes' if ok else 'no'}</td></tr>"
        for name, ok in status["checks"].items()
    )
    failed = "".join(
        f"<tr><td>{html.escape(r.title or r.rating_key)}</td><td>{r.failures}</td><td>{_ago(now - r.updated_at)}</td>"
        f'<td class="muted">{html.escape(r.last_error)}</td></tr>'
        for r in failures
    )
    failures_html = (
        "<table><thead><tr><th>Title</th><th>Tries</th><th>Last try</th><th>Error</th></tr></thead>"
        f"<tbody>{failed}</tbody></table>"
        if failed
        else '<p class="muted">None.</p>'
    )
    wall = "".join(
        "<figure>"
        + (
            f'<img src="recent/{r.rating_key}-{r.target}.jpg" alt="" loading="lazy">'
            if thumb_path(thumbs, f"{r.rating_key}-{r.target}.jpg")
            else ""
        )
        + f"<figcaption>{html.escape(r.title or r.rating_key)}<br>{LABELS.get(Status(r.status), r.status)}, "
        f"{_ago(now - r.updated_at)}</figcaption></figure>"
        for r in recent
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60"><title>Posteryard status</title><style>{STYLE}</style></head>
<body><main>
<header><h1>Posteryard</h1><span class="muted">Version {html.escape(str(status["version"]))}</span></header>
<section class="facts">{"".join(facts)}</section>
<section><h2>Checks</h2><table><tbody>{checks}</tbody></table></section>
<section><h2>Failures</h2>{failures_html}</section>
<section><h2>Latest images</h2><div class="wall">{wall or '<p class="muted">Nothing rendered yet.</p>'}</div></section>
</main></body></html>
"""
