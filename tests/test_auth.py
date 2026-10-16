import hashlib
import hmac
import json
import time
import urllib.parse

import pytest

from musicbot.web.app import validate_init_data

TOKEN = "123456:TEST-TOKEN"


def init_data(token=TOKEN, user=None, age=0, tamper=False):
    params = {
        "auth_date": str(int(time.time()) - age),
        "query_id": "AAH",
        "user": json.dumps(user or {"id": 42, "first_name": "Rese"}),
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    params["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        params["user"] = json.dumps({"id": 1})
    return urllib.parse.urlencode(params)


def test_signed_data_gives_the_user():
    assert validate_init_data(init_data(), TOKEN)["id"] == 42


def test_data_signed_with_another_token_is_rejected():
    with pytest.raises(ValueError):
        validate_init_data(init_data(token="999:OTHER"), TOKEN)


def test_changed_user_is_rejected():
    with pytest.raises(ValueError):
        validate_init_data(init_data(tamper=True), TOKEN)


def test_old_data_is_rejected():
    with pytest.raises(ValueError):
        validate_init_data(init_data(age=2 * 24 * 3600), TOKEN)


def test_missing_hash_is_rejected():
    raw = urllib.parse.urlencode({"auth_date": str(int(time.time())), "user": json.dumps({"id": 1})})
    with pytest.raises(ValueError):
        validate_init_data(raw, TOKEN)


def test_empty_header_is_rejected():
    with pytest.raises(ValueError):
        validate_init_data("", TOKEN)
