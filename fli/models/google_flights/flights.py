import json
import urllib.parse
from enum import Enum

from pydantic import BaseModel, PositiveInt, model_validator

from fli.models.airline import Airline
from fli.models.airport import Airport
from fli.models.google_flights.base import (
    FlightSegment,
    LayoverRestrictions,
    MaxStops,
    PassengerInfo,
    PriceLimit,
    SeatType,
    SortBy,
    TripType,
)


class FlightSearchFilters(BaseModel):
    """Complete set of filters for flight search.

    This model matches required Google Flights' API structure.
    """

    trip_type: TripType = TripType.ONE_WAY
    passenger_info: PassengerInfo
    flight_segments: list[FlightSegment]
    stops: MaxStops = MaxStops.ANY
    seat_type: SeatType = SeatType.ECONOMY
    price_limit: PriceLimit | None = None
    airlines: list[Airline] | None = None
    max_duration: PositiveInt | None = None
    layover_restrictions: LayoverRestrictions | None = None
    sort_by: SortBy = SortBy.NONE

    @model_validator(mode="after")
    def validate_flight_segments(self) -> "FlightSearchFilters":
        """Ensure segment counts align with the selected trip type."""
        segment_count = len(self.flight_segments)
        if segment_count == 0:
            raise ValueError("At least one flight segment is required")
        if segment_count > 6:
            raise ValueError("No more than 6 flight segments are supported")

        if self.trip_type == TripType.ONE_WAY and segment_count != 1:
            raise ValueError("One-way trip must have one flight segment")
        if self.trip_type == TripType.ROUND_TRIP and segment_count != 2:
            raise ValueError("Round trip must have two flight segments")
        if self.trip_type == TripType.MULTI_CITY and segment_count < 2:
            raise ValueError("Multi-city trip must have at least two flight segments")

        return self

    def format(self) -> list:
        """Format filters into the nested Google Flights API payload."""
        return [
            [],  # empty array at start
            _build_root_filter_block(self),
            _serialize_filter_value(self.sort_by.value),
            0,  # constant
            0,  # constant
            2,  # constant
        ]

    def encode(self) -> str:
        """URL encode the formatted filters for API request."""
        formatted_filters = self.format()
        # First convert the formatted filters to a JSON string
        formatted_json = json.dumps(formatted_filters, separators=(",", ":"))
        # Then wrap it in a list with null
        wrapped_filters = [None, formatted_json]
        # Finally, encode the whole thing
        return urllib.parse.quote(json.dumps(wrapped_filters, separators=(",", ":")))


def _serialize_filter_value(obj: object) -> object:
    """Serialize a nested filter value into the wire format."""
    if isinstance(obj, Airport | Airline):
        return obj.name
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, list):
        return [_serialize_filter_value(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _serialize_filter_value(value) for key, value in obj.items()}
    if isinstance(obj, BaseModel):
        return _serialize_filter_value(obj.model_dump(exclude_none=True))
    return obj


def _format_segment(filters: FlightSearchFilters, segment: FlightSegment) -> list[object]:
    """Format a single flight segment into the Google Flights wire shape."""
    return [
        _build_airport_filters(segment.departure_airport),
        _build_airport_filters(segment.arrival_airport),
        _build_time_filters(segment),
        _serialize_filter_value(filters.stops.value),
        _build_airlines_filters(filters.airlines),
        None,
        segment.travel_date,
        [filters.max_duration] if filters.max_duration else None,
        _build_selected_flights(segment),
        _build_layover_airports(filters.layover_restrictions),
        None,
        None,
        _build_layover_duration(filters.layover_restrictions),
        None,
        3,
    ]


def _build_airport_filters(airports: list[list[Airport | int]]) -> list[list[list[object]]]:
    """Format the nested airport filter structure expected by Google Flights."""
    return [[_serialize_airport_filter(airport) for airport in airports]]


def _serialize_airport_filter(airport: list[Airport | int]) -> list[object]:
    """Serialize one airport filter row."""
    return [_serialize_filter_value(airport[0]), _serialize_filter_value(airport[1])]


def _build_time_filters(segment: FlightSegment) -> list[int | None] | None:
    """Build time restriction filters for one segment."""
    if segment.time_restrictions is None:
        return None
    return [
        segment.time_restrictions.earliest_departure,
        segment.time_restrictions.latest_departure,
        segment.time_restrictions.earliest_arrival,
        segment.time_restrictions.latest_arrival,
    ]


def _build_airlines_filters(airlines: list[Airline] | None) -> list[object] | None:
    """Build the airline restriction list."""
    if not airlines:
        return None
    return [
        _serialize_filter_value(airline)
        for airline in sorted(airlines, key=lambda item: item.value)
    ]


def _build_selected_flights(segment: FlightSegment) -> list[list[object | None]] | None:
    """Build the selected-flight continuation payload for native flows."""
    if segment.selected_flight is None:
        return None
    return [
        [
            _serialize_filter_value(leg.departure_airport.name),
            _serialize_filter_value(leg.departure_datetime.strftime("%Y-%m-%d")),
            _serialize_filter_value(leg.arrival_airport.name),
            segment.selected_flight.selection_token,
            _serialize_filter_value(leg.airline.name),
            _serialize_filter_value(leg.flight_number),
        ]
        for leg in segment.selected_flight.legs
    ]


def _build_layover_airports(
    layover_restrictions: LayoverRestrictions | None,
) -> list[object] | None:
    """Build layover airport restrictions."""
    if layover_restrictions is None or not layover_restrictions.airports:
        return None
    return [_serialize_filter_value(airport) for airport in layover_restrictions.airports]


def _build_layover_duration(layover_restrictions: LayoverRestrictions | None) -> int | None:
    """Build the optional layover duration restriction."""
    if layover_restrictions is None:
        return None
    return layover_restrictions.max_duration


def _build_passenger_filters(passenger_info: PassengerInfo) -> list[int]:
    """Build the passenger summary array used by Google Flights."""
    passenger_filters = [
        passenger_info.adults,
        passenger_info.children,
        passenger_info.infants_on_lap,
        passenger_info.infants_in_seat,
    ]
    if passenger_info.num_cabin_luggage is not None:
        passenger_filters.append(passenger_info.num_cabin_luggage)
    return passenger_filters


def _build_root_filter_block(filters: FlightSearchFilters) -> list[object]:
    """Build the main nested filter block for the Google Flights payload."""
    return [
        None,
        None,
        _serialize_filter_value(filters.trip_type.value),
        None,
        [],
        _serialize_filter_value(filters.seat_type.value),
        _build_passenger_filters(filters.passenger_info),
        [None, filters.price_limit.max_price] if filters.price_limit else None,
        None,
        None,
        None,
        None,
        None,
        [_format_segment(filters, segment) for segment in filters.flight_segments],
        None,
        None,
        None,
        1,
    ]


_VULTURE_REFERENCES = (FlightSearchFilters.validate_flight_segments,)
