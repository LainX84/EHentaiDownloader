"""Reading the temporary IP-ban page of E-Hentai."""

from src.config import BAN_DEFAULT_WAIT
from src.throttle import ban_seconds_from_page

BAN_PAGE = (
    "This IP address has been temporarily banned due to an excessive request rate. "
    "This probably means you are using automated mirroring/harvesting software. "
    "The ban expires in {when}"
)


def test_minutes_and_seconds():
    assert ban_seconds_from_page(BAN_PAGE.format(when="12 minutes and 30 seconds")) == 12 * 60 + 30


def test_hours_and_days():
    assert ban_seconds_from_page(BAN_PAGE.format(when="1 day and 2 hours")) == 86400 + 7200


def test_unreadable_time_uses_the_default_wait():
    assert ban_seconds_from_page(BAN_PAGE.format(when="a while")) == BAN_DEFAULT_WAIT


def test_normal_pages_are_not_bans():
    assert ban_seconds_from_page("<html><title>E-Hentai Galleries</title></html>") is None
    assert ban_seconds_from_page("") is None
