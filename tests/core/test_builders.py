from datetime import datetime, timedelta

import pytest

from fli.core.builders import build_date_search_segments, build_flight_segments, normalize_date
from fli.models import Airport, TripType


def future_date(days_from_now: int) -> tuple[str, str]:
    """Return the same future date in padded and unpadded forms."""
    target = datetime.now().date() + timedelta(days=days_from_now)
    padded = target.isoformat()
    unpadded = f"{target.year}-{target.month}-{target.day}"
    return padded, unpadded


class TestNormalizeDate:
    """Tests for normalize_date."""

    def test_already_padded(self):
        assert normalize_date("2026-04-02") == "2026-04-02"

    def test_single_digit_month_and_day(self):
        assert normalize_date("2026-4-2") == "2026-04-02"

    def test_single_digit_day(self):
        assert normalize_date("2026-12-5") == "2026-12-05"

    def test_single_digit_month(self):
        assert normalize_date("2026-1-15") == "2026-01-15"

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError):
            normalize_date("not-a-date")

    def test_invalid_month_raises(self):
        with pytest.raises(ValueError):
            normalize_date("2026-13-01")


class TestBuildFlightSegments:
    """Tests for date normalization in build_flight_segments."""

    def test_normalizes_departure_date(self):
        padded, unpadded = future_date(30)
        segments, _ = build_flight_segments(
            origin=Airport.JFK,
            destination=Airport.LAX,
            departure_date=unpadded,
        )
        assert segments[0].travel_date == padded

    def test_normalizes_return_date(self):
        departure_padded, departure_unpadded = future_date(30)
        return_padded, return_unpadded = future_date(37)
        segments, trip_type = build_flight_segments(
            origin=Airport.JFK,
            destination=Airport.LAX,
            departure_date=departure_unpadded,
            return_date=return_unpadded,
        )
        assert trip_type == TripType.ROUND_TRIP
        assert segments[0].travel_date == departure_padded
        assert segments[1].travel_date == return_padded


class TestBuildDateSearchSegments:
    """Tests for date normalization in build_date_search_segments."""

    def test_normalizes_start_date(self):
        padded, unpadded = future_date(30)
        segments, _ = build_date_search_segments(
            origin=Airport.JFK,
            destination=Airport.LAX,
            start_date=unpadded,
        )
        assert segments[0].travel_date == padded

    def test_normalizes_start_date_round_trip(self):
        start_padded, start_unpadded = future_date(30)
        return_padded, _ = future_date(37)
        segments, trip_type = build_date_search_segments(
            origin=Airport.JFK,
            destination=Airport.LAX,
            start_date=start_unpadded,
            is_round_trip=True,
            trip_duration=7,
        )
        assert trip_type == TripType.ROUND_TRIP
        assert segments[0].travel_date == start_padded
        assert segments[1].travel_date == return_padded
