import re
import xml.etree.ElementTree as ET
from pathlib import Path

from posteryard import config

ROOT = Path(__file__).parent.parent
TEMPLATE = ET.parse(ROOT / "templates" / "posteryard.xml").getroot()
REQUIRED = ("Name", "Repository", "Overview", "Category", "Support", "Project", "TemplateURL", "Icon")


def _configs(kind: str) -> dict[str, str]:
    return {c.attrib["Target"]: c.text or "" for c in TEMPLATE.iter("Config") if c.attrib["Type"] == kind}


def test_the_template_has_every_field_community_applications_needs() -> None:
    for name in REQUIRED:
        assert (TEMPLATE.findtext(name) or "").strip(), name
    assert ET.parse(ROOT / "ca_profile.xml").getroot().findtext("WebPage")


def test_the_template_defaults_start_the_service() -> None:
    env = _configs("Variable") | {
        "TMDB_API_KEY": "example",
        "PLEX_URL": "http://127.0.0.1:32400",
        "PLEX_TOKEN": "example",
        "WEBHOOK_SECRET": "example",
    }
    config.require_service(config.load(env))


def test_the_template_matches_the_image_and_the_readme() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert _configs("Path").keys() == {re.search(r"DATA_DIR=(\S+)", dockerfile)[1]}  # type: ignore[index]
    assert _configs("Port").keys() == {re.search(r"EXPOSE (\d+)", dockerfile)[1]}  # type: ignore[index]
    readme = (ROOT / "README.md").read_text()
    assert [name for name in _configs("Variable") if f"`{name}`" not in readme] == []
