"""Command line entry point."""

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

from posteryard import __version__, config, http, pipeline
from posteryard.maintainerr import Maintainerr
from posteryard.plex import Plex
from posteryard.tmdb import Tmdb

log = logging.getLogger("posteryard")
TMDB_REF = re.compile(r"^(movie|tv):(\d+)$")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="posteryard", description="Apple TV style artwork for Plex.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser(
        "preview",
        help="render artwork into a folder; never writes to Plex",
        description="Render artwork into a folder. Nothing is written to Plex.",
    )
    preview.add_argument("rating_keys", nargs="*", metavar="RATING_KEY", help="Plex movie, show, season or episode")
    preview.add_argument(
        "--tmdb",
        action="append",
        default=[],
        metavar="KIND:ID",
        help="render from TMDB alone, e.g. movie:603 or tv:95396; repeatable",
    )
    preview.add_argument(
        "--season",
        action="append",
        type=int,
        default=[],
        metavar="N",
        help="with --tmdb tv:ID, also render season N; repeatable",
    )
    preview.add_argument(
        "--episodes",
        type=int,
        default=0,
        metavar="N",
        help="episodes to render per season for Plex shows; -1 for all (default 0)",
    )
    preview.add_argument("--out", type=Path, help="output folder (default DATA_DIR/previews)")
    return parser


def _preview(args: argparse.Namespace, cfg: config.Config) -> int:
    if not args.rating_keys and not args.tmdb:
        log.error("give at least one Plex rating key or --tmdb KIND:ID")
        return 2
    refs = []
    for ref in args.tmdb:
        match = TMDB_REF.match(ref)
        if not match:
            log.error("--tmdb takes movie:ID or tv:ID, not %s", ref)
            return 2
        refs.append((match.group(1), int(match.group(2))))
    if args.rating_keys and not (cfg.plex_url and cfg.plex_token):
        log.error("PLEX_URL and PLEX_TOKEN are required to render Plex items")
        return 2

    plex = Plex(cfg.plex_url, cfg.plex_token) if args.rating_keys else None
    days = Maintainerr(cfg.maintainerr_url).action_days() if args.rating_keys else {}
    ctx = pipeline.Context(Tmdb(cfg.tmdb_api_key), cfg.quality, cfg.regions, days, date.today(), plex)
    out_dir: Path = args.out or cfg.preview_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    episodes = None if args.episodes < 0 else args.episodes

    failed = 0
    jobs = [("plex", key) for key in args.rating_keys] + [("tmdb", ref) for ref in refs]
    for source, ref in jobs:
        try:
            if source == "plex":
                outputs = pipeline.render_plex(ctx, str(ref), episodes)
            else:
                kind, tid = ref
                outputs = pipeline.render_tmdb(ctx, kind, tid, args.season)
        except (pipeline.NotFoundError, http.HttpError) as exc:
            log.error("%s: %s", ref, exc)
            failed += 1
            continue
        for output in outputs:
            path = out_dir / f"{output.rating_key}-{output.target}-{_slug(output.name)}.jpg"
            output.image.save(path, quality=92)
            log.info("%s  %s  (%s)", path.name, output.name, "; ".join(output.notes))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parser().parse_args(argv)
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 2
    if args.command == "preview":
        return _preview(args, cfg)
    return 2


if __name__ == "__main__":
    sys.exit(main())
