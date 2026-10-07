import argparse
import logging
import re
import shlex
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from posteryard import __version__, config, http, logfmt, lookup, memory, overrides, pipeline, why
from posteryard.artwork import MemoryChoices
from posteryard.automarks import AutoMarks
from posteryard.fanart import Fanart
from posteryard.jellyfin import Emby, Jellyfin
from posteryard.maintainerr import Maintainerr
from posteryard.notify import Notifier
from posteryard.plex import Plex
from posteryard.server import MediaServer
from posteryard.service import Service, other_server
from posteryard.settings import Settings
from posteryard.sources import Sources
from posteryard.store import Store, StoreError
from posteryard.tmdb import Kind, Tmdb
from posteryard.worker import Outcome, Worker

log = logging.getLogger("posteryard")
# These libraries log full URLs, with tokens, or every image chunk at debug level.
QUIET_LOGGERS = ("urllib3", "apprise", "requests", "PIL")
TMDB_REF = re.compile(r"^(movie|tv):(\d+)$")
TITLE_HELP = 'a movie or show name such as "The Office" or "Dune 2021", or a rating key'
SEASON_HELP = "season N of the show instead of the show itself"
DRY_RUN_NOTE = " (DRY_RUN is on: saved to the previews folder, the server was not changed)"


def _set_log_level(level: config.LogLevel) -> None:
    logging.getLogger().setLevel(level.upper())
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _title_arguments(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument("title", nargs="+" if required else "*", metavar="TITLE", help=TITLE_HELP)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="posteryard", description="Clean, consistent artwork for Plex, Jellyfin and Emby."
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("serve", help="run the service: webhook, sweep and daily pass")

    commands.add_parser("test-alert", help="send a test alert to every notification service")

    commands.add_parser("health", help="check the running service; exit 0 when it is healthy")

    restore = commands.add_parser(
        "restore",
        help="give every uploaded image back to the server's own, before you remove Posteryard",
        description="Give every uploaded image back to the server's own and unlock it. Stop the service first.",
    )
    restore.add_argument("--all", action="store_true", required=True, help="every movie, show, season and episode")

    find = commands.add_parser("find", help="list the movies and shows whose name contains WORDS")
    find.add_argument("words", nargs="+", metavar="WORDS")

    forget = commands.add_parser("forget", help="hand an image you changed on the server back to Posteryard")
    _title_arguments(forget)
    forget.add_argument("--season", type=int, metavar="N", help=SEASON_HELP)

    explain = commands.add_parser(
        "why",
        help="show which art a poster uses and why the other candidates were not used",
        description="List the art candidates of a poster with the reason each was used or not. Nothing is changed.",
    )
    _title_arguments(explain)
    explain.add_argument("--season", type=int, metavar="N", help=SEASON_HELP)

    art = commands.add_parser("art", help="choose the poster art for a movie, show or season")
    art_commands = art.add_subparsers(dest="art_command", required=True)
    art_set = art_commands.add_parser("set", help="use your own image as the poster art; overlays are added on top")
    _title_arguments(art_set)
    art_set.add_argument("--season", type=int, metavar="N", help=SEASON_HELP)
    source = art_set.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="an http(s) address of the image")
    source.add_argument("--file", type=Path, help="a path to the image inside the container, e.g. under /data")
    for name, text in (("next", "switch to the next best image"), ("reset", "go back to automatic art")):
        command = art_commands.add_parser(name, help=text)
        _title_arguments(command)
        command.add_argument("--season", type=int, metavar="N", help=SEASON_HELP)

    preview = commands.add_parser(
        "preview",
        help="render artwork into a folder; never writes to the server",
        description="Render artwork into a folder. Nothing is written to the server.",
    )
    _title_arguments(preview, required=False)
    preview.add_argument(
        "--tmdb", action="append", default=[], metavar="KIND:ID",
        help="render from TMDB alone, e.g. movie:603 or tv:95396; repeatable",
    )  # fmt: skip
    preview.add_argument(
        "--season", action="append", type=int, default=[], metavar="N",
        help="render season N of the show; repeatable",
    )  # fmt: skip
    preview.add_argument(
        "--episodes", type=int, default=0, metavar="N",
        help="episodes to render per season of a show or season on the server; -1 for all (default 0)",
    )  # fmt: skip
    preview.add_argument("--out", type=Path, help="output folder (default DATA_DIR/previews)")
    return parser


def _options(args: argparse.Namespace) -> str:
    parts: list[str] = []
    seasons = args.season if isinstance(args.season, list) else [args.season] if args.season is not None else []
    for number in seasons:
        parts += ["--season", str(number)]
    if getattr(args, "url", None):
        parts += ["--url", args.url]
    if getattr(args, "file", None):
        parts += ["--file", str(args.file)]
    return "".join(f" {shlex.quote(part)}" for part in parts)


def _command(args: argparse.Namespace) -> str:
    return f"art {args.art_command}" if args.command == "art" else str(args.command)


def _resolve(plex: MediaServer, cfg: config.Config, args: argparse.Namespace) -> lookup.Match:
    match = lookup.resolve(plex, cfg.libraries, " ".join(args.title))
    return lookup.season(plex, match, args.season) if args.season is not None else match


def _preview(args: argparse.Namespace, cfg: config.Config) -> int:  # noqa: C901, PLR0912
    if not args.title and not args.tmdb:
        print("Give a title or --tmdb KIND:ID.")
        return 2
    refs: list[tuple[Kind, int]] = []
    for ref in args.tmdb:
        match = TMDB_REF.match(ref)
        if not match:
            print(f"--tmdb takes movie:ID or tv:ID, not {ref}")
            return 2
        refs.append(("movie" if match.group(1) == "movie" else "tv", int(match.group(2))))
    if args.title:
        try:
            config.require_server(cfg)
        except config.ConfigError as exc:
            print(exc)
            return 2

    plex = _server(cfg) if args.title else None
    keys: list[str] = []
    if plex is not None:
        try:
            found = lookup.resolve(plex, cfg.libraries, " ".join(args.title))
            keys = [lookup.season(plex, found, n).rating_key for n in args.season] or [found.rating_key]
        except lookup.TitleError as exc:
            print(exc.explain("preview", _options(args)))
            return 1
        except (ValueError, http.RequestError) as exc:
            print(exc)
            return 1
    try:
        days = Maintainerr(cfg.maintainerr_url).action_days() if plex is not None else {}
    except (http.RequestError, ValueError) as exc:
        log.warning("Maintainerr unavailable, rendering without leaving labels", extra={"reason": str(exc)})
        days = {}
    settings = Settings.from_config(cfg, days, date.today())
    sources = Sources(
        Tmdb(cfg.tmdb_api_key, cfg.logo_languages),
        settings,
        fanart=Fanart(cfg.fanart_api_key) if cfg.fanart_api_key else None,
        marks=AutoMarks(cfg.data_dir / "marks", MemoryChoices()),
    )
    ctx = pipeline.Context(settings, sources, plex)
    out_dir: Path = args.out or cfg.preview_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    episodes = None if args.episodes < 0 else args.episodes

    failed = 0
    jobs: list[tuple[str, tuple[Kind, int] | None]] = [(k, None) for k in keys]
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
                print(f"{path}  {plan.name}  ({'; '.join(plan.notes)})")
            memory.release()
        except (pipeline.NotFoundError, http.RequestError, ValueError) as exc:
            print(f"{label}: {exc}")
            failed += 1
    return 1 if failed else 0


def _find(args: argparse.Namespace, cfg: config.Config) -> int:
    plex = _server(cfg)
    query = " ".join(args.words)
    matches = lookup.search(lookup.library_titles(plex, cfg.libraries), query)
    if not matches:
        print(f'Nothing in {", ".join(cfg.libraries)} matches "{query}".')
        return 1
    width = max(len(m.rating_key) for m in matches)
    for m in matches:
        print(f"  {m.rating_key.rjust(width)}  {m.label}")
    return 0


def _server(cfg: config.Config) -> MediaServer:
    if cfg.jellyfin_url:
        return Jellyfin(cfg.jellyfin_url, cfg.jellyfin_api_key)
    if cfg.emby_url:
        return Emby(cfg.emby_url, cfg.emby_api_key)
    return Plex(cfg.plex_url, cfg.plex_token)


def _health(cfg: config.Config) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{cfg.listen_port}/healthz", timeout=4):
            return 0
    except urllib.error.HTTPError as exc:
        print(f"unhealthy: HTTP {exc.code}")
    except OSError as exc:
        print(f"not reachable on port {cfg.listen_port}: {exc}")
    return 1


def _test_alert(cfg: config.Config) -> int:
    notifier = Notifier(cfg.notify_urls)
    if not notifier.configured:
        print("No notification service is set up. Set NOTIFY_URLS.")
        return 1
    if notifier.send("test", "Alerts from Posteryard reach you."):
        print("Test alert sent.")
        return 0
    print("A notification service did not accept the test alert. The log above has the details.")
    return 1


def main(argv: list[str] | None = None) -> int:  # noqa: C901, PLR0912
    logfmt.setup()
    args = _parser().parse_args(argv)
    try:
        cfg = config.load()
        _set_log_level(cfg.log_level)
        if args.command == "serve":
            config.require_service(cfg)
        elif args.command in ("art", "forget", "find", "restore", "why"):
            config.require_server(cfg)
    except config.ConfigError as exc:
        print(exc)
        return 2
    if args.command == "health":
        return _health(cfg)
    if args.command == "preview":
        return _preview(args, cfg)
    if args.command == "test-alert":
        return _test_alert(cfg)
    try:
        if args.command == "find":
            return _find(args, cfg)
        plex = _server(cfg)
        store = Store(cfg.state_path)
    except http.RequestError as exc:
        print(exc)
        return 1
    except StoreError as exc:
        print(exc)
        return 2
    try:
        if args.command != "serve" and (problem := other_server(plex, store)):
            print(problem)
            return 2
        # Only the service keeps alert state. A command run beside it would overwrite the service's copy.
        state = store if args.command == "serve" else None
        worker = Worker(cfg, plex, store, Notifier(cfg.notify_urls, cfg.notify_events, state))
        if args.command == "serve":
            return Service(cfg, plex, store, worker).run()
        if args.command == "restore":
            return _restore(worker)
        if args.command == "why":
            return _why(args, cfg, plex, store, worker)
        return _change(args, cfg, plex, store, worker)
    except http.RequestError as exc:
        print(exc)
        return 1
    finally:
        store.close()


def _restore(worker: Worker) -> int:
    counts = worker.restore_all()
    print(f"Gave {counts.restored} images back to {worker.server.name}.")
    if counts.kept:
        print(f"Left {counts.kept} images that were changed by hand.")
    if counts.failed:
        print(f"{counts.failed} images could not be restored. The log above has the details. Run the command again.")
        return 1
    return 0


def _why(args: argparse.Namespace, cfg: config.Config, plex: MediaServer, store: Store, worker: Worker) -> int:
    try:
        match = _resolve(plex, cfg, args)
    except lookup.TitleError as exc:
        print(exc.explain("why", _options(args)))
        return 1
    except ValueError as exc:
        print(exc)
        return 1
    item = plex.item(match.rating_key)
    if item is None:
        print(f"{plex.name} has no item {match.rating_key}")
        return 1
    worker.ctx.sources.choices = why.ReadOnlyChoices(store)
    worker.ctx.sources.marks = None
    try:
        result = why.report(worker.ctx, item, store)
    except (pipeline.NotFoundError, overrides.ArtError) as exc:
        print(f"{match.label}: {exc}")
        return 1
    print("\n".join(why.lines(match.label, result)))
    return 0


def _change(args: argparse.Namespace, cfg: config.Config, plex: MediaServer, store: Store, worker: Worker) -> int:
    try:
        match = _resolve(plex, cfg, args)
    except lookup.TitleError as exc:
        print(exc.explain(_command(args), _options(args)))
        return 1
    except ValueError as exc:
        print(exc)
        return 1
    key = match.rating_key
    try:
        if args.command == "forget":
            store.forget(key)
            outcome = worker.process(key)
        elif args.art_command == "set":
            image = overrides.from_url(args.url) if args.url else overrides.from_file(args.file)
            outcome = worker.set_custom(key, image)
        elif args.art_command == "next":
            outcome = worker.next_art(key)
        else:
            outcome = worker.reset_art(key)
    except (overrides.ArtError, pipeline.NotFoundError) as exc:
        print(f"{match.label}: {exc}")
        return 1
    print(f"{match.label}: {outcome}{DRY_RUN_NOTE if cfg.dry_run and outcome == Outcome.PREVIEW else ''}")
    return 1 if outcome == Outcome.FAILED else 0
