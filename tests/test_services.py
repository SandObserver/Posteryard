from typing import Any

from posteryard.services import offers, service_for


def pick(providers: dict[str, Any], regions: list[str]) -> str | None:
    return next((key for offer in offers(providers, regions) if (key := service_for(offer.name))), None)


def test_service_names() -> None:
    assert service_for("Netflix Standard with Ads") == "netflix"
    assert service_for("Amazon Prime Video") == "prime"
    assert service_for("Apple TV") == "appletv"
    assert service_for("Max") == "hbomax"
    assert service_for("Netflix Kids") == "netflix"
    assert service_for("Paramount+ Amazon Channel") is None
    assert service_for("Apple TV Store") is None


def test_first_region_with_a_known_service_wins() -> None:
    providers = {
        "CA": {"buy": [{"provider_name": "Apple TV"}], "flatrate": [{"provider_name": "Unknown Service"}]},
        "US": {
            "flatrate": [
                {"provider_name": "Hulu", "display_priority": 2},
                {"provider_name": "Netflix", "display_priority": 1},
            ]
        },
    }
    assert pick(providers, ["CA", "US"]) == "netflix"
    assert pick(providers, ["CA"]) is None


def test_new_services_and_add_on_channels() -> None:
    assert service_for("Crunchyroll") == "crunchyroll"
    assert service_for("Tubi TV") == "tubi"
    assert service_for("Channel 4") == "channel4"
    assert service_for("Now TV") == "now"
    assert service_for("Crunchyroll Amazon Channel") is None
    assert service_for("Paramount Plus Apple TV Channel") is None
    assert service_for("JustWatch TV") is None


def test_offers_skip_add_on_channels_and_keep_order() -> None:
    providers = {
        "CA": {
            "flatrate": [
                {"provider_name": "Crunchyroll Amazon Channel", "provider_id": 1, "display_priority": 1},
                {"provider_name": "Hayu", "provider_id": 223, "logo_path": "/hayu.jpg", "display_priority": 2},
                {"provider_name": "Netflix", "provider_id": 8, "logo_path": "/n.jpg", "display_priority": 3},
            ]
        }
    }
    assert [o.name for o in offers(providers, ["CA"])] == ["Hayu", "Netflix"]
    assert pick(providers, ["CA"]) == "netflix"
