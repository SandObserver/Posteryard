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
# Emoji and pictographs have no glyph in the poster fonts. The zero-width non-joiner stays: Persian needs it.
SYMBOLS = re.compile("[\ufe0e\ufe0f\u200d]")
JPEG_QUALITY = 90
LOGO_CACHE = 16
HTTP_NOT_FOUND = 404
PROBES = 3
CONFIRMED = frozenset({"uploaded", "unchanged", "selected again"})
# Plex's own guide comes with artwork. Only a TMDB poster replaces it.
OWN_ART = ("tv.plex.providers.epg.cloud:",)


class LeftAloneError(Exception):
    """The guide's own image stays."""


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
    # The guide's picture before shared pictures are dropped. Stored, so a later run can count its users.
    guide_picture: str = ""

    def record_key(self, target: str) -> str:
        return f"{self.provider}/{target}"


def shown(text: str) -> str:
    """A guide title as drawn: without emoji, which the fonts lack, and with single spaces."""
    kept = "".join(ch for ch in SYMBOLS.sub("", text) if unicodedata.category(ch) not in ("So", "Sk", "Cs"))
    return " ".join(kept.split()) or " ".join(text.split())


def without_notes(text: str) -> str:
    """The title without trailing notes in brackets, such as "(Tekrar)" or "[HD]". Linear in the title length."""
    end, cut = len(text.rstrip()), None
    while end and text[end - 1] in ")]":
        opener, i = None, end - 2
        while i >= 0 and text[i] not in ")]":
            if text[i] in "([":
                opener = i
            i -= 1
        if opener is None:
            break
        while opener and text[opener - 1].isspace():
            opener -= 1
        cut = end = opener
    return text if cut is None else text[:cut]


def norm(text: str) -> str:
    return "".join(ch for ch in without_notes(text).casefold() if ch.isalnum())


def _web(url: str) -> bool:
    return url.startswith(("http://", "https://"))


def guide_shows(
    provider: str, airings: Iterable[Item], remembered: Callable[[str], str] = lambda _: ""
) -> list[GuideShow]:
    """One entry per show or movie. A picture that several shows share is a channel image, not the show's own.

    Plex keeps one entry for a title on several channels, so its one poster gets no channel logo.
    `remembered` gives the guide picture stored for a record key. Plex shows an upload in place of the picture.
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
        target = str(airing.get("targetKey") or key)
        picture = thumb if _web(thumb) and thumb != logo else ""
        if thumb.startswith("/"):
            picture = remembered(f"{provider}/{target}")
        if picture:
            # The same show on several channels has one key per channel on Jellyfin. Its title is the same.
            users.setdefault(picture, set()).add(norm(title) or key)
        length = int(media.get("endsAt") or 0) - int(media.get("beginsAt") or 0)
        channels.setdefault(key, set()).add(str(media.get("channelCallSign") or media.get("channelTitle") or ""))
        if key not in shows:
            channel = str(media.get("channelCallSign") or media.get("channelTitle") or "")
            guid = str((airing.get("guid") if movie else airing.get("grandparentGuid")) or "")
            kind: Kind = "movie" if movie else "tv"
            shows[key] = GuideShow(
                provider, key, kind, title, channel, logo, picture, guid, max(length, 0), (target,), picture
            )
        else:
            known = shows[key]
            targets = known.targets if target in known.targets else (*known.targets, target)
            airtime = known.airtime + max(length, 0)
            picture = known.picture or picture
            shows[key] = replace(known, picture=picture, airtime=airtime, targets=targets, guide_picture=picture)
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
        self._confirmed: list[str] = []

    def due(self, now: float | None = None) -> bool:
        """After every guide refresh, once a day, when DRY_RUN is turned on or off, and when an upload is gone."""
        now = time.time() if now is None else now
        known = json.loads(self.store.meta("livetv_refreshed", "{}"))
        current = {g.provider: g.refreshed_at for g in self.server.guides()}
        checked = float(self.store.meta("livetv_checked", "0"))
        return (
            current != known
            or now - checked >= CHECK_SECONDS
            or self.store.meta("livetv_dry_run", "") != str(self.dry_run)
            or (not self.dry_run and self._upload_gone())
        )

    def _upload_gone(self) -> bool:
        """Plex deletes all guide uploads at once when it cleans bundles. Checking a few in turn is enough."""
        keys: list[str] = json.loads(self.store.meta("livetv_confirmed", "[]"))
        if not keys:
            return False
        start = int(self.store.meta("livetv_probe", "0")) % len(keys)
        picked = [keys[(start + i) % len(keys)] for i in range(min(PROBES, len(keys)))]
        self.store.set_meta("livetv_probe", str(start + len(picked)))
        for key in picked:
            record = self.store.guide(key)
            if record is None:
                continue
            provider, _, target = record.key.partition("/")
            try:
                images = self.server.guide_images(provider, target)
            except http.HttpError as exc:
                if exc.status == HTTP_NOT_FOUND:
                    continue
                raise
            if record.image_key not in {str(image.get("ratingKey")) for image in images}:
                log.info("a live TV poster is gone from the server, checking the guide", extra={"title": record.title})
                return True
        return False

    def run(self, now: float | None = None, tick: Callable[[], bool] = lambda: True) -> Counter[str]:
        """`tick` runs before each show. When it returns False, the run stops and is done again later."""
        now = time.time() if now is None else now
        tally: Counter[str] = Counter()
        guides = self.server.guides()
        fetch = lru_cache(maxsize=LOGO_CACHE)(self._fetch)
        self._confirmed = []
        for guide in guides:
            airings = self.server.guide_airings(guide.provider, int(now), int(now + WINDOW_SECONDS))
            shows = guide_shows(guide.provider, airings, self._remembered_picture)
            self._shows, self._featured, self._unreadable = shows, {}, set()
            for show in shows:
                if not tick():
                    return tally
                tally.update(self.apply(show, fetch))
            self.store.guide_seen([show.record_key(t) for show in shows for t in show.targets], now)
        self.store.guide_drop_unseen(now - FORGET_SECONDS)
        self.store.set_meta("livetv_refreshed", json.dumps({g.provider: g.refreshed_at for g in guides}))
        self.store.set_meta("livetv_checked", str(now))
        self.store.set_meta("livetv_confirmed", json.dumps(self._confirmed))
        self.store.set_meta("livetv_dry_run", str(self.dry_run))
        log.info("live TV posters checked", extra={k.replace(" ", "_"): v for k, v in sorted(tally.items())})
        return tally

    def _remembered_picture(self, record_key: str) -> str:
        record = self.store.guide(record_key)
        return record.picture if record else ""

    def apply(self, show: GuideShow, fetch: Callable[[str], Image.Image]) -> Counter[str]:
        """One poster per show, put on each of its targets: the show on Plex, every airing on Jellyfin."""
        first = self.store.guide(show.record_key(show.targets[0])) if show.targets else None
        try:
            plan = self.plan(show, fetch)
            if self.dry_run:
                return Counter([self._preview(show, plan, first)])
        except LeftAloneError:
            return Counter({"left alone": len(show.targets)})
        except (http.RequestError, pipeline.NotFoundError, OSError, ValueError) as exc:
            log.warning("live TV poster failed", extra={"title": show.title, "reason": str(exc)})
            return Counter({"failed": len(show.targets)})
        tally: Counter[str] = Counter()
        drawn: list[bytes] = []
        for target in show.targets:
            try:
                outcome, direct = self._apply_target(show, target, plan, drawn)
            except (http.RequestError, pipeline.NotFoundError, OSError, ValueError) as exc:
                log.warning("live TV poster failed", extra={"title": show.title, "reason": str(exc)})
                tally["failed"] += 1
                continue
            tally[outcome] += 1
            record = self.store.guide(show.record_key(target))
            if outcome in CONFIRMED and direct and record and record.image_key.startswith("upload://"):
                self._confirmed.append(record.key)
        if tally["uploaded"]:
            log.info("live TV poster uploaded", extra={"title": show.title, "using": "; ".join(plan.notes)})
        return tally

    def _apply_target(self, show: GuideShow, target: str, plan: pipeline.Plan, drawn: list[bytes]) -> tuple[str, bool]:
        """The outcome, and whether the target itself reaches the entry. The record stays under the target."""
        where = self.server.guide_target(show.provider, target, show.guid)
        if where is None:
            log.info("the server returns another guide entry for this title, skipped", extra={"title": show.title})
            return "skipped", False
        return self._apply_at(show, target, where, plan, drawn), where == target

    def _apply_at(self, show: GuideShow, target: str, where: str, plan: pipeline.Plan, drawn: list[bytes]) -> str:
        record = self.store.guide(show.record_key(target))
        images = self.server.guide_images(show.provider, where)
        selected = next((str(i.get("ratingKey")) for i in images if i.get("selected")), "")
        ours = record.image_key if record else ""
        if selected.startswith("upload://") and selected != ours:
            return "manual"
        stored = {str(i.get("ratingKey")) for i in images}
        if record and ours in stored and record.fingerprint == plan.fingerprint:
            if selected == ours:
                return "unchanged"
            self.server.guide_select(show.provider, where, ours)
            return "selected again"
        if not drawn:
            drawn.append(jpeg(plan.draw()))
        if self.server.guide_target(show.provider, where, show.guid) != where:
            return "skipped"
        image_key = self.server.guide_upload(show.provider, where, drawn[0])
        self.store.guide_uploaded(show.record_key(target), show.title, plan.fingerprint, image_key, show.guide_picture)
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
            self.store.guide_uploaded(
                show.record_key(show.targets[0]), show.title, plan.fingerprint, "", show.guide_picture
            )
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
        results = self.ctx.sources.tmdb.search(show.kind, without_notes(show.title).strip())
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
            return livetv.logo_mark(image) if what == "logo" else livetv.guide_picture(image)
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
        own_art = show.provider.startswith(OWN_ART)
        placeholder = norm(show.title) == norm(show.channel)
        if placeholder and mark is not None and not own_art:
            art = self.featured_art(show)
            return pipeline.Plan(
                key, "poster", show.title, {**base, "design": "live-channel", "logo": logo, "art": art},
                lambda: livetv.channel_card(mark, self.ctx.sources.load(art) if art else None),
                [f"channel logo {http.redact(logo)}"] + ([f"art from {pipeline.art_source(art)}"] if art else []),
            )  # fmt: skip
        tid = None if placeholder else self.match(show)
        plan: pipeline.Plan | None = None
        if tid is not None:
            name = shown(without_notes(show.title)) or shown(show.title)
            try:
                plan = pipeline.live_poster(self.ctx, show.kind, tid, key, name)
            except pipeline.NotFoundError as exc:
                log.debug("live TV show has no TMDB art", extra={"title": show.title, "reason": str(exc)})
        if plan is None and own_art:
            raise LeftAloneError(show.title)
        picture = self._image(self._fetch, show.picture, show, "guide picture") if plan is None else None
        if plan is None and picture is not None:
            plan = pipeline.Plan(
                key, "poster", show.title, {**base, "design": "live-picture", "picture": show.picture},
                lambda: livetv.picture_card(picture, designs.text_logo(shown(show.title), rows=3)),
                [f"picture from the guide {http.redact(show.picture)}"],
            )  # fmt: skip
        if plan is None:
            return pipeline.Plan(
                key, "poster", show.title, {**base, "design": "live-title", "logo": logo},
                lambda: livetv.title_card(mark, designs.text_logo(shown(show.title), rows=3)),
                [f"channel logo {http.redact(logo)}" if logo else "no channel logo"],
            )  # fmt: skip
        plan.inputs.update(live=LIVE_VERSION, channel_logo=logo)
        if mark is not None:
            draw = plan.draw
            plan.draw = lambda: livetv.with_channel(draw(), mark)
        return plan
