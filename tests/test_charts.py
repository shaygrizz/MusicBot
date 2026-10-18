"""Чарты страны для новичков: страна по часовому поясу и ленте Apple."""

from musicbot import charts

FEED = {
    "feed": {
        "results": [
            {"artistName": "Zivert", "name": "Credo", "artworkUrl100": "https://x/100x100bb.jpg"},
            {"artistName": "", "name": "Без исполнителя"},
            {"artistName": "Мот", "name": "Капли"},
        ]
    }
}


def test_country_comes_from_the_time_zone_then_from_the_language():
    assert charts.country_for("Europe/Moscow", "en") == "RU"
    assert charts.country_for("Asia/Vladivostok", "en") == "RU"
    assert charts.country_for("Europe/Rome", "ru") == "IT"
    assert charts.country_for("Antarctica/Troll", "ru") == "RU"  # пояса нет в таблице: язык интерфейса
    assert charts.country_for(None, "it") == "IT"
    assert charts.country_for("", "xx") == "US"


def test_chart_is_parsed_cached_and_a_failure_is_not_asked_again_at_once(monkeypatch):
    monkeypatch.setattr(charts, "_cache", {})
    calls = []

    def get_json(url, timeout=10, headers=None):
        calls.append(url)
        if "/xx/" in url:
            raise RuntimeError("504")
        return FEED

    monkeypatch.setattr(charts.net, "get_json", get_json)
    chart = charts.country_chart("RU")
    assert [t["title"] for t in chart] == ["Credo", "Капли"]  # запись без исполнителя отброшена
    assert chart[0]["cover"] == "https://x/300x300bb.jpg" and chart[0]["auto"] is True
    assert charts.country_chart("RU") == chart and len(calls) == 1
    assert charts.country_chart("XX") == [] and charts.country_chart("XX") == [] and len(calls) == 2
