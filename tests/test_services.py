from posteryard.services import pick, service_for


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
