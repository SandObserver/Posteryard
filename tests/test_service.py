import json

from posteryard.service import parse_webhook, related_keys


def test_multipart_webhook_payload() -> None:
    payload = {"event": "library.new", "Metadata": {"ratingKey": "7", "type": "episode"}}
    boundary = "XyZ"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="payload"\r\nContent-Type: application/json\r\n\r\n'
        f"{json.dumps(payload)}\r\n--{boundary}--\r\n"
    ).encode()
    assert parse_webhook(f"multipart/form-data; boundary={boundary}", body) == payload


def test_json_webhook_and_garbage() -> None:
    assert parse_webhook("application/json", b'{"event": "media.play"}') == {"event": "media.play"}
    assert parse_webhook("text/plain", b"hello") is None


def test_an_episode_brings_its_season_and_show() -> None:
    assert related_keys({"ratingKey": 7, "parentRatingKey": 6, "grandparentRatingKey": 5}) == ["7", "6", "5"]
    assert related_keys({"ratingKey": "9"}) == ["9"]
