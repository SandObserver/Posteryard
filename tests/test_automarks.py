from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from posteryard import automarks
from posteryard.artwork import MemoryChoices
from posteryard.automarks import AutoMarks, cut, network_for, one_colour
from posteryard.render import layers
from posteryard.services import Offer
from posteryard.tmdb import Tmdb


def flat_icon() -> Image.Image:
    icon = Image.new("RGB", (300, 300), (230, 20, 90))
    draw = ImageDraw.Draw(icon)
    draw.rectangle((60, 120, 100, 180), fill=(255, 255, 255))
    draw.rectangle((200, 120, 240, 180), fill=(255, 255, 255))
    draw.rectangle((60, 140, 240, 160), fill=(255, 255, 255))
    return icon


def gradient_icon() -> Image.Image:
    ramp = np.linspace(0, 255, 300, dtype=np.uint8)
    return Image.fromarray(np.stack([np.tile(ramp, (300, 1))] * 3, axis=-1))


def test_a_flat_icon_gives_a_white_mark_and_a_gradient_does_not() -> None:
    mark = cut(flat_icon())
    assert mark is not None
    assert mark.size == (181, 61)
    assert mark.getpixel((20, 30)) == (255, 255, 255, 255)
    assert mark.getchannel("A").getpixel((90, 5)) == 0
    assert cut(gradient_icon()) is None


def test_marks_are_cut_once_and_drawn_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[str] = []

    def image(path: str) -> Image.Image:
        fetched.append(path)
        return flat_icon() if path == "/flat.jpg" else gradient_icon()

    monkeypatch.setattr(Tmdb, "image", staticmethod(image))
    marks = AutoMarks(tmp_path / "marks", MemoryChoices())
    name = marks.get(Offer(223, "Hayu", "/flat.jpg"))
    assert name == f"auto-223-v{automarks.VERSION}"
    assert marks.get(Offer(223, "Hayu", "/flat.jpg")) == name
    assert marks.get(Offer(9, "Gradient", "/gradient.jpg")) is None
    assert marks.get(Offer(9, "Gradient", "/gradient.jpg")) is None
    assert fetched == ["/flat.jpg", "/gradient.jpg"]
    assert layers.mark(name, 40).height == 40
    assert marks.get(Offer(0, "No id", "/flat.jpg")) is None


def two_colour_logo() -> Image.Image:
    logo = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(logo)
    draw.ellipse((0, 0, 120, 120), fill=(40, 90, 220, 255))
    draw.polygon([(40, 30), (40, 90), (95, 60)], fill=(255, 255, 255, 255))
    draw.rectangle((150, 30, 390, 90), fill=(40, 90, 220, 255))
    draw.rectangle((180, 50, 360, 70), fill=(0, 0, 0, 0))
    return logo


def test_light_parts_of_a_coloured_logo_become_holes() -> None:
    mark = one_colour(two_colour_logo())
    assert mark is not None
    alpha = mark.getchannel("A")
    assert alpha.getpixel((20, 60)) == 255
    assert alpha.getpixel((60, 60)) == 0


def test_a_logo_that_fills_its_box_is_not_a_mark() -> None:
    assert one_colour(Image.new("RGBA", (200, 200), (200, 30, 30, 255))) is None


def test_scattered_specks_are_not_a_mark() -> None:
    noise = np.random.default_rng(1).random((120, 300)) > 0.8
    speckled = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    speckled.putalpha(Image.fromarray((noise * 255).astype(np.uint8)))
    assert one_colour(speckled) is None


def test_white_ink_is_cut_from_a_gradient_icon() -> None:
    icon = gradient_icon().convert("RGB")
    icon = Image.fromarray((np.asarray(icon) * [0.3, 0.1, 0.6]).astype(np.uint8))
    ImageDraw.Draw(icon).rectangle((60, 120, 100, 180), fill=(255, 255, 255))
    ImageDraw.Draw(icon).rectangle((200, 120, 240, 180), fill=(255, 255, 255))
    ImageDraw.Draw(icon).rectangle((60, 140, 240, 160), fill=(255, 255, 255))
    mark = cut(icon)
    assert mark is not None
    assert mark.size == (181, 61)


def test_network_follows_the_offer_region() -> None:
    assert network_for(Offer(1, "Binge", "/x.png", "AU")) == 5490
    assert network_for(Offer(1, "Exxen", "/x.png", "TR")) == 4405
    assert network_for(Offer(1, "Exxen", "/x.png", "US")) == 4405
    assert network_for(Offer(1, "Binge", "/x.png", "US")) is None
    assert network_for(Offer(1, "Not a network anywhere", "/x.png", "US")) is None


def test_a_service_without_a_network_in_the_region_uses_its_network_from_another() -> None:
    assert network_for(Offer(99, "Shudder", "/x.png", "GB")) == 2949
    assert network_for(Offer(520, "Discovery +", "/x.png", "CA")) == 4353
    assert network_for(Offer(524, "Discovery+", "/x.png", "IE")) == 4883
    assert network_for(Offer(524, "Discovery+", "/x.png", "IT")) == 4741
    assert network_for(Offer(1, "Shudder", "/x.png", "GB")) is None


class FakeTmdb:
    def __init__(self, logo: str = "/exxen.svg") -> None:
        self.asked: list[int] = []
        self.logo = logo

    def network_logo(self, network_id: int) -> str | None:
        self.asked.append(network_id)
        return self.logo if network_id == 4405 else None


@pytest.mark.parametrize("logo", ["/exxen.svg", "/exxen.png"])
def test_a_network_logo_is_preferred_over_the_provider_icon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, logo: str
) -> None:
    fetched: list[tuple[str, str]] = []

    def image(path: str, size: str = "original") -> Image.Image:
        fetched.append((path, size))
        return two_colour_logo() if path == "/exxen.png" else flat_icon()

    monkeypatch.setattr(Tmdb, "image", staticmethod(image))
    tmdb = FakeTmdb(logo)
    marks = AutoMarks(tmp_path / "marks", MemoryChoices())
    offer = Offer(5, "Exxen", "/icon.jpg", "TR")
    assert marks.get(offer, tmdb) == f"net-4405-v{automarks.VERSION}"  # type: ignore[arg-type]
    assert marks.get(offer, tmdb) == f"net-4405-v{automarks.VERSION}"  # type: ignore[arg-type]
    assert fetched == [("/exxen.png", "w500")]
    assert tmdb.asked == [4405]
    hidive = Offer(6, "HIDIVE", "/icon.jpg", "US")
    assert marks.get(hidive, tmdb) == f"auto-6-v{automarks.VERSION}"  # type: ignore[arg-type]
    assert marks.get(hidive) == f"auto-6-v{automarks.VERSION}"


def test_only_parts_inside_other_ink_become_holes() -> None:
    logo = Image.new("RGBA", (400, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(logo)
    draw.rectangle((0, 0, 110, 110), fill=(250, 200, 40, 255))
    draw.ellipse((25, 25, 85, 85), outline=(10, 10, 10, 255), width=14)
    draw.rectangle((150, 20, 190, 110), fill=(255, 255, 255, 255))
    draw.rectangle((230, 20, 390, 110), fill=(255, 255, 255, 255))
    mark = one_colour(logo)
    assert mark is not None
    alpha = mark.getchannel("A")
    assert alpha.getpixel((5, 5)) == 255
    assert alpha.getpixel((30, 55)) == 0
    assert alpha.getpixel((160, 50)) == 255


def test_a_box_bigger_than_its_letters_stays_ink() -> None:
    logo = Image.new("RGBA", (400, 100), (0, 0, 0, 0))
    draw = ImageDraw.Draw(logo)
    draw.rectangle((0, 0, 399, 99), fill=(0, 0, 0, 255))
    for x in (40, 160, 280):
        draw.rectangle((x, 25, x + 60, 75), fill=(255, 255, 255, 255))
    mark = one_colour(logo)
    assert mark is not None
    alpha = mark.getchannel("A")
    assert alpha.getpixel((10, 10)) == 255
    assert alpha.getpixel((70, 50)) == 0
