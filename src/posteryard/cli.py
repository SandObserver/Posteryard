"""Command line entry point."""

import argparse
import logging
import re
import sys
from datetime import date
from pathlib import Path

from posteryard import __version__, config, http, memory, overrides, pipeline
from posteryard.maintainerr import Maintainerr
from posteryard.notify import Notifier
from posteryard.plex import Plex, is_rating_key
from posteryard.service import Service
from posteryard.store import Store
from posteryard.tmdb import Kind, Tmdb
from posteryard.worker import Worker

log = logging.getLogger("posteryard")
TMDB_REF = re.compile(r"^(movie|tv):(\d+)$")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="posteryard", description="Apple TV style artwork for Plex.")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("serve", help="run the service: webhook, sweep and daily pass")

    forget = commands.add_parser("forget", help="let the service manage images that were changed by hand again")
    forget.add_argument("rating_keys", nargs="+", metavar="RATING_KEY")

    art = commands.add_parser("art", help="choose the poster art for a movie, show or season")
    art_commands = art.add_subparsers(dest="art_command", required=True)
    art_set = art_commands.add_parser("set", help="use your own image as the poster art; overlays are added on top")
    art_set.add_argument("rating_key", metavar="RATING_KEY")
    source = art_set.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="an http(s) address of the image")
    source.add_argument("--file", type=Path, help="a path to the image inside the container, e.g. under /data")
    for name, text in (("next", "switch to the next best image"), ("reset", "go back to automatic art")):
        art_commands.add_parser(name, help=text).add_argument("rating_key", metavar="RATING_KEY")

    preview = commands.add_parser(
        "preview",
        help="render artwork into a folder; never writes to Plex",
        description="Render artwork into a folder. Nothing is written to Plex.",
    )
    preview.add_argument("rating_keys", nargs="*", metavar="RATING_KEY", help="Plex movie, show, season or episode")
    preview.add_argument(
        "--tmdb", action="append", default=[], metavar="KIND:ID",
        help="render from TMDB alone, e.g. movie:603 or tv:95396; repeatable",
    )  # fmt: skip
    preview.add_argument(
        "--season", action="append", type=int, default=[], metavar="N",
        help="with --tmdb tv:ID, also render season N; repeatable",
    )  # fmt: skip
    preview.add_argument(
        "--episodes", type=int, default=0, metavar="N",
        help="episodes to render per season of a Plex show or season; -1 for all (default 0)",
    )  # fmt: skip
    preview.add_argument("--out", type=Path, help="output folder (default DATA_DIR/previews)")
    return parser


def _keys_ok(keys: list[str]) -> bool:
    bad = [key for key in keys if not is_rating_key(key)]
    if bad:
        log.error("rating keys are numbers, not %s", ", ".join(bad))
    return not bad


def _preview(args: argparse.Namespace, cfg: config.Config) -> int:
    if not args.rating_keys and not args.tmdb:
        log.error("give at least one Plex rating key or --tmdb KIND:ID")
        return 2
    if not _keys_ok(args.rating_keys):
        return 2
    refs: list[tuple[Kind, int]] = []
    for ref in args.tmdb:
        match = TMDB_REF.match(ref)
        if not match:
            log.error("--tmdb takes movie:ID or tv:ID, not %s", ref)
            return 2
        refs.append(("movie" if match.group(1) == "movie" else "tv", int(match.group(2))))
    if args.rating_keys and not (cfg.plex_url and cfg.plex_token):
        log.error("PLEX_URL and PLEX_TOKEN are required to render Plex items")
        return 2

    plex = Plex(cfg.plex_url, cfg.plex_token) if args.rating_keys else None
    try:
        days = Maintainerr(cfg.maintainerr_url).action_days() if args.rating_keys else {}
    except http.RequestError as exc:
        log.warning("Maintainerr unavailable, rendering without leaving labels: %s", exc)
        days = {}
    ctx = pipeline.Context(Tmdb(cfg.tmdb_api_key), cfg.quality, cfg.regions, days, date.today(), plex)
    out_dir: Path = args.out or cfg.preview_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    episodes = None if args.episodes < 0 else args.episodes

    failed = 0
    jobs: list[tuple[str, tuple[Kind, int] | None]] = [(k, None) for k in args.rating_keys]
    jobs += [(f"{kind}:{tid}", (kind, tid)) for kind, tid in refs]
    for label, tmdb_ref in jobs:
        try:
            if tmdb_ref is None:
                plans = pipeline.plan_preview(ctx, label, episodes)
            else:
                plans = pipeline.plan_tmdb(ctx, tmdb_ref[0], tmdb_ref[1], args.season)
            for plan in plans:
                path = out_dir / f"{plan.rating_key}-{plan.target}-{_slug(plan.name)}.jpg"
                plan.draw().convert("RGB").save(path, quality=92)
                log.info("%s  %s  (%s)", path.name, plan.name, "; ".join(plan.notes))
            memory.release()
        except (pipeline.NotFoundError, http.RequestError) as exc:
            log.error("%s: %s", label, exc)
            failed += 1
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parser().parse_args(argv)
    try:
        cfg = config.load()
        if args.command in ("serve", "art"):
            config.require_service(cfg)
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 2
    if args.command == "preview":
        return _preview(args, cfg)
    store = Store(cfg.state_path)
    if args.command == "forget":
        if not _keys_ok(args.rating_keys):
            return 2
        for key in args.rating_keys:
            store.forget(key)
            log.info("forgot %s", key)
        return 0
    plex = Plex(cfg.plex_url, cfg.plex_token)
    worker = Worker(cfg, plex, store, Notifier(cfg.ntfy_url, cfg.ntfy_topic, cfg.ntfy_token))
    if args.command == "art":
        return _art(args, worker)
    Service(cfg, plex, store, worker).run()
    return 0


def _art(args: argparse.Namespace, worker: Worker) -> int:
    key = str(args.rating_key)
    if not _keys_ok([key]):
        return 2
    try:
        if args.art_command == "set":
            image = overrides.from_url(args.url) if args.url else overrides.from_file(args.file)
            outcome = worker.set_custom(key, image)
        elif args.art_command == "next":
            outcome = worker.next_art(key)
        else:
            outcome = worker.reset_art(key)
    except (overrides.ArtError, pipeline.NotFoundError, http.RequestError) as exc:
        log.error("%s", exc)
        return 1
    log.info("%s: %s%s", key, outcome, " (DRY_RUN: preview only)" if worker.cfg.dry_run else "")
    return 1 if outcome == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
