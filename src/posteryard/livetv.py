"""Posters for the shows and movies in a Live TV guide, uploaded to the guide itself.

Plex rebuilds guide entries on every guide refresh and selects the guide's own image again. It keeps the uploaded
image, so after a refresh the stored upload is selected again without uploading it twice. Jellyfin keeps an upload
on an airing until the guide changes that airing, and has one item per airing, so each airing gets the poster.
"""

import hashlib
import json
import logging
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image

from posteryard import http, pipeline
from posteryard.render import designs, livetv
from posteryard.server import GuideServer, Item
from posteryard.store import GuideRecord, Store
from posteryard.tmdb import Kind, open_image
from posteryard.worker import jpeg

log = logging.getLogger(__name__)
LIVE_VERSION = "3"
WINDOW_SECONDS = 4 * 86400
CHECK_SECONDS = 86400
FORGET_SECONDS = 14 * 86400
MATCH_PREFIX = "livetv-match:"
MATCH_HIT_SECONDS = 30 * 86400
MATCH_MISS_SECONDS = 7 * 86400
# Several exact matches: the most voted one wins only with this many times the votes of the next.
VOTE_LEAD = 3
TRAILING_NOTE = re.compile(r"(\s*[\(\[][^\)\]]*[\)\]])+\s*$")
# Emoji and pictographs have no glyph in the poster fonts. The zero-width non-joiner stays: Persian needs it.
SYMBOLS = re.compile("[\ufe0e\ufe0f\u200d]")
JPEG_QUALITY = 90
LOGO_CACHE = 16


@dataclass(frozen=True)
class GuideShow:
    provider: str
    key: str
    kind: Kind
    title: str
    channel: str
    logo: str
    picture: str
    guid: str = ""
    airtime: int = 0
    targets: tuple[str, ...] = ()
    uploaded: bool = False

    def record_key(self, target: str) -> str:
        return f"{self.provider}/{target}"


def shown(text: str) -> str:
    """A guide title as drawn: without emoji, which the fonts lack, and with single spaces."""
    kept = "".join(ch for ch in SYMBOLS.sub("", text) if unicodedata.category(ch) not in ("So", "Sk", "Cs"))
    return " ".join(kept.split()) or " ".join(text.split())


def norm(text: str) -> str:
    return "".join(ch for ch in TRAILING_NOTE.sub("", text).casefold() if ch.isalnum())


def _web(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def guide_shows(provider: str, airings: Iterable[Item]) -> list[GuideShow]:
    """One entry per show or movie. A picture that several shows share is a channel image, not the show's own.

    Plex keeps one entry for a title on several channels, so its one poster gets no channel logo.
    """
    shows: dict[str, GuideShow] = {}
    users: dict[str, set[str]] = {}
    channels: dict[str, set[str]] = {}
    for item in airings:
        airing: dict[str, Any] = dict(item)
        movie = airing.get("type") == "movie"
        key = str((airing.get("ratingKey") if movie else airing.get("grandparentRatingKey")) or "")
        title = str((airing.get("title") if movie else airing.get("grandparentTitle")) or "")
        if not key or not title:
            continue
        media = (airing.get("Media") or [{}])[0]
        logo = str(media.get("channelThumb") or "")
        thumb = str((airing.get("thumb") if movie else airing.get("grandparentThumb")) or "")
        picture = thumb if _web(thumb) and thumb != logo else ""
        uploaded = thumb.startswith("/")
        if picture:
            users.setdefault(picture, set()).add(key)
        length = int(media.get("endsAt") or 0) - int(media.get("beginsAt") or 0)
        target = str(airing.get("targetKey") or key)
        channels.setdefault(key, set()).add(str(media.get("channelCallSign") or media.get("channelTitle") or ""))
        if key not in shows:
            channel = str(media.get("channelCallSign") or media.get("channelTitle") or "")
            guid = str((airing.get("guid") if movie else airing.get("grandparentGuid")) or "")
            kind: Kind = "movie" if movie else "tv"
            shows[key] = GuideShow(
                provider, key, kind, title, channel, logo, picture, guid, max(length, 0), (target,), uploaded
            )
        else:
            known = shows[key]
            targets = known.targets if target in known.targets else (*known.targets, target)
            airtime = known.airtime + max(length, 0)
            shows[key] = replace(known, picture=known.picture or picture, airtime=airtime, targets=targets)
    return [
        replace(
            show,
            picture="" if show.picture and len(users[show.picture]) > 1 else show.picture,
            logo="" if len(channels[key]) > 1 else show.logo,
        )
        for key, show in shows.items()
    ]


def fetch_image(url: str) -> Image.Image:
    if not _web(url):
        raise ValueError(f"not a web address: {url}")
    return open_image(http.request("GET", url, timeout=60))


class LiveTv:
    def __init__(  # noqa: PLR0913
        self,
        server: GuideServer,
        store: Store,
        ctx: pipeline.Context,
        *,
        dry_run: bool,
        preview_dir: Path,
        fetch: Callable[[str], Image.Image] = fetch_image,
    ) -> None:
        self.server, self.store, self.ctx = server, store, ctx
        self.dry_run, self.preview_dir = dry_run, preview_dir
        self._fetch = fetch
        self._shows: list[GuideShow] = []
        self._featured: dict[tuple[str, str], str | None] = {}
        self._unreadable: set[str] = set()

    def due(self, now: float | None = None) -> bool:
        """After every guide refresh, and once a day."""
        now = time.time() if now is None else now
        known = json.loads(self.store.meta("livetv_refreshed", "{}"))
        current = {g.provider: g.refreshed_at for g in self.server.guides()}
        return current != known or now - float(self.store.meta("livetv_checked", "0")) >= CHECK_SECONDS

    def run(self, now: float | None = None, tick: Callable[[], bool] = lambda: True) -> Counter[str]:
        """`tick` runs before each show. When it returns False, the run stops and is done again later."""
        now = time.time() if now is None else now
        tally: Counter[str] = Counter()
        guides = self.server.guides()
        fetch = lru_cache(maxsize=LOGO_CACHE)(self._fetch)
        for guide in guides:
            airings = self.server.guide_airings(guide.provider, int(now), int(now + WINDOW_SECONDS))
            shows = guide_shows(guide.provider, airings)
            self._shows, self._featured, self._unreadable = shows, {}, set()
            for show in shows:
                if not tick():
                    return tally
                tally.update(self.apply(show, fetch))
            self.store.guide_seen([show.record_key(t) for show in shows for t in show.targets], now)
        self.store.guide_drop_unseen(now - FORGET_SECONDS)
        self.store.set_meta("livetv_refreshed", json.dumps({g.provider: g.refreshed_at for g in guides}))
        self.store.set_meta("livetv_checked", str(now))
        log.info("live TV posters checked", extra=dict(sorted(tally.items())))
        return tally

    def apply(self, show: GuideShow, fetch: Callable[[str], Image.Image]) -> Counter[str]:
        """One poster per show, put on each of its targets: the show on Plex, every airing on Jellyfin."""
        first = self.store.guide(show.record_key(show.targets[0])) if show.targets else None
        # Plex shows the upload, not the guide's picture. Only then is the remembered picture still the guide's.
        if show.uploaded and not show.picture and first and first.picture:
            show = replace(show, picture=first.picture)
        try:
            plan = self.plan(show, fetch)
            if self.dry_run:
                return Counter([self._preview(show, plan, first)])
        except (http.RequestError, pipeline.NotFoundError, OSError, ValueError) as exc:
            log.warning("live TV poster failed", extra={"title": show.title, "reason": str(exc)})
            return Counter({"failed": len(show.targets)})
        tally: Counter[str] = Counter()
        drawn: list[bytes] = []
        for target in show.targets:
            try:
                tally[self._apply_target(show, target, plan, drawn)] += 1
            except (http.RequestError, pipeline.NotFoundError, OSError, ValueError) as exc:
                log.warning("live TV poster failed", extra={"title": show.title, "reason": str(exc)})
                tally["failed"] += 1
        if tally["uploaded"]:
            log.info("live TV poster uploaded", extra={"title": show.title, "using": "; ".join(plan.notes)})
        return tally

    def _apply_target(self, show: GuideShow, target: str, plan: pipeline.Plan, drawn: list[bytes]) -> str:
        if not self.server.guide_is(show.provider, target, show.guid):
            log.info("the server returns another guide entry for this title, skipped", extra={"title": show.title})
            return "skipped"
        record = self.store.guide(show.record_key(target))
        images = self.server.guide_images(show.provider, target)
        selected = next((str(i.get("ratingKey")) for i in images if i.get("selected")), "")
        ours = record.image_key if record else ""
        if selected.startswith("upload://") and selected != ours:
            return "manual"
        stored = {str(i.get("ratingKey")) for i in images}
        if record and ours in stored and record.fingerprint == plan.fingerprint:
            if selected == ours:
                return "unchanged"
            self.server.guide_select(show.provider, target, ours)
            return "selected again"
        if not drawn:
            drawn.append(jpeg(plan.draw()))
        image_key = self.server.guide_upload(show.provider, target, drawn[0])
        self.store.guide_uploaded(show.record_key(target), show.title, plan.fingerprint, image_key, show.picture)
        return "uploaded"

    def _preview(self, show: GuideShow, plan: pipeline.Plan, record: GuideRecord | None) -> str:
        if record and record.fingerprint == plan.fingerprint:
            return "unchanged"
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^a-z0-9]+", "-", show.title.lower()).strip("-")[:40] or "show"
        digest = hashlib.sha1(show.record_key(show.key).encode(), usedforsecurity=False).hexdigest()[:8]
        path = self.preview_dir / f"livetv-{name}-{digest}.jpg"
        plan.draw().convert("RGB").save(path, quality=JPEG_QUALITY)
        # Keep the record of an upload. Going live again must still know which poster is Posteryard's.
        if show.targets and not (record and record.image_key):
            self.store.guide_uploaded(show.record_key(show.targets[0]), show.title, plan.fingerprint, "", show.picture)
        log.info("live TV preview saved", extra={"title": show.title, "using": "; ".join(plan.notes)})
        return "preview"

    def match(self, show: GuideShow) -> int | None:
        """A TMDB id only for an exact title match. A near match is never used."""
        name = norm(show.title)
        if not name:
            return None
        cache_key = f"{MATCH_PREFIX}{show.kind}:{name}"
        hit = self.store.get_choice(cache_key)
        if hit is not None:
            found = int(hit.get("id") or 0)
            if time.time() - float(hit.get("at") or 0) < (MATCH_HIT_SECONDS if found else MATCH_MISS_SECONDS):
                return found or None
        results = self.ctx.sources.tmdb.search(show.kind, TRAILING_NOTE.sub("", show.title).strip())
        label, original = ("title", "original_title") if show.kind == "movie" else ("name", "original_name")
        exact = [
            r
            for r in results
            if r.get("id") and name in {norm(str(r.get(label) or "")), norm(str(r.get(original) or ""))}
        ]
        exact.sort(key=lambda r: -int(r.get("vote_count") or 0))
        lead = len(exact) == 1 or (
            len(exact) > 1
            and int(exact[0].get("vote_count") or 0) >= VOTE_LEAD * max(1, int(exact[1].get("vote_count") or 0))
        )
        found = int(exact[0]["id"]) if exact and lead else 0
        self.store.put_choice(cache_key, {"id": found, "at": time.time()})
        return found or None

    def featured_art(self, show: GuideShow) -> str | None:
        """TMDB art of the show with the most airtime on this channel, for the channel's own blocks."""
        channel = (show.provider, norm(show.channel))
        if channel not in self._featured:
            self._featured[channel] = None
            siblings = [
                s for s in self._shows if (s.provider, norm(s.channel)) == channel and norm(s.title) != norm(s.channel)
            ]
            for sibling in sorted(siblings, key=lambda s: (-s.airtime, s.title)):
                tid = self.match(sibling)
                picked = None
                if tid is not None:
                    title = self.ctx.sources.title(sibling.kind, tid, sibling.title, remember=False)
                    picked = self.ctx.sources.art(title)
                if picked is not None:
                    self._featured[channel] = picked.path
                    break
        return self._featured[channel]

    def _image(self, fetch: Callable[[str], Image.Image], url: str, show: GuideShow, what: str) -> Image.Image | None:
        """An image the poster cannot use, such as an SVG logo or a broken link, is left out instead of failing."""
        if not url or url in self._unreadable:
            return None
        try:
            image = fetch(url)
            return livetv.logo_mark(image) if what == "logo" else image
        except (http.RequestError, OSError, ValueError) as exc:
            if url not in self._unreadable:
                self._unreadable.add(url)
                log.warning(
                    f"{what} could not be read, posters are made without it",
                    extra={"channel": show.channel, "url": http.redact(url), "reason": str(exc)},
                )
            return None

    def plan(self, show: GuideShow, fetch: Callable[[str], Image.Image]) -> pipeline.Plan:
        """A block named after its channel gets the channel card. Then TMDB art, the guide's picture, a title card.

        `fetch` is for channel logos, which repeat. Guide pictures are fetched once per show without the cache.
        """
        key = show.record_key(show.key)
        mark = self._image(fetch, show.logo, show, "logo")
        logo = show.logo if mark is not None else ""
        base = {"live": LIVE_VERSION, "title": show.title}
        placeholder = norm(show.title) == norm(show.channel)
        if placeholder and mark is not None:
            art = self.featured_art(show)
            return pipeline.Plan(
                key, "poster", show.title, {**base, "design": "live-channel", "logo": logo, "art": art},
                lambda: livetv.channel_card(mark, self.ctx.sources.load(art) if art else None),
                [f"channel logo {logo}"] + ([f"art from {pipeline.art_source(art)}"] if art else []),
            )  # fmt: skip
        tid = None if placeholder else self.match(show)
        plan: pipeline.Plan | None = None
        if tid is not None:
            name = shown(TRAILING_NOTE.sub("", show.title)) or shown(show.title)
            try:
                plan = pipeline.live_poster(self.ctx, show.kind, tid, key, name)
            except pipeline.NotFoundError as exc:
                log.debug("live TV show has no TMDB art", extra={"title": show.title, "reason": str(exc)})
        picture = self._image(self._fetch, show.picture, show, "guide picture") if plan is None else None
        if plan is None and picture is not None:
            plan = pipeline.Plan(
                key, "poster", show.title, {**base, "design": "live-picture", "picture": show.picture},
                lambda: livetv.picture_card(picture, designs.text_logo(shown(show.title), rows=3)),
                [f"picture from the guide {show.picture}"],
            )  # fmt: skip
        if plan is None:
            return pipeline.Plan(
                key, "poster", show.title, {**base, "design": "live-title", "logo": logo},
                lambda: livetv.title_card(mark, designs.text_logo(shown(show.title), rows=3)),
                [f"channel logo {logo}" if logo else "no channel logo"],
            )  # fmt: skip
        plan.inputs.update(live=LIVE_VERSION, channel_logo=logo)
        if mark is not None:
            draw = plan.draw
            plan.draw = lambda: livetv.with_channel(draw(), mark)
        return plan
