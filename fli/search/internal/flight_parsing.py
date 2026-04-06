"""Parsing helpers shared by exact-date flight searches."""

from __future__ import annotations

from datetime import datetime

from fli.models import Airline, Airport, FlightLeg, FlightResult
from fli.search.selection import parse_selection_token


def parse_flights_data(data: list) -> FlightResult:
    """Parse raw flight data into a structured FlightResult."""
    return FlightResult(
        price=parse_price(data),
        cabin_bag_included=_parse_cabin_bag_included(data),
        duration=data[0][9],
        stops=len(data[0][2]) - 1,
        selection_token=parse_selection_token(data),
        legs=[
            FlightLeg(
                airline=parse_airline(fl[22][0]),
                flight_number=fl[22][1],
                departure_airport=parse_airport(fl[3]),
                arrival_airport=parse_airport(fl[6]),
                departure_datetime=parse_datetime(fl[20], fl[8]),
                arrival_datetime=parse_datetime(fl[21], fl[10]),
                duration=fl[11],
            )
            for fl in data[0][2]
        ],
    )


def parse_price(data: list) -> float:
    """Extract price from raw flight data."""
    try:
        if data[1] and data[1][0]:
            return data[1][0][-1]
    except (IndexError, TypeError):
        pass
    return 0.0


def _parse_cabin_bag_included(data: list) -> bool | None:
    """Return whether the fare includes a cabin bag (carry-on).

    Google Flights encodes this at ``data[4][6][1]``: 1 means the fare
    already covers a cabin bag, 0 means it does not (common for LCC basic
    fares like Ryanair Value).  Returns *None* when the field is absent.
    """
    try:
        return bool(data[4][6][1])
    except (IndexError, TypeError):
        return None


def parse_datetime(date_arr: list[int], time_arr: list[int]) -> datetime:
    """Convert date and time arrays to datetime."""
    if not any(x is not None for x in date_arr) or not any(x is not None for x in time_arr):
        raise ValueError("Date and time arrays must contain at least one non-None value")
    year = date_arr[0] or 0
    month = date_arr[1] or 0
    day = date_arr[2] or 0
    hour = time_arr[0] or 0
    minute = time_arr[1] if len(time_arr) > 1 and time_arr[1] is not None else 0
    return datetime(year, month, day, hour, minute)


def parse_airline(airline_code: str) -> Airline:
    """Convert airline code to Airline enum."""
    if airline_code[0].isdigit():
        airline_code = f"_{airline_code}"
    return getattr(Airline, airline_code)


def parse_airport(airport_code: str) -> Airport:
    """Convert airport code to Airport enum."""
    return getattr(Airport, airport_code)
