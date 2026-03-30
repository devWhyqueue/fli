"""Builders for MCP request models."""

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

from fli.core import (
    build_time_restrictions,
    parse_airlines,
    parse_cabin_class,
    parse_max_stops,
    parse_sort_by,
    resolve_airport,
)
from fli.models import (
    FlightSearchFilters,
    FlightSegment,
    LayoverRestrictions,
    PassengerInfo,
    TripType,
)

from .mcp_models import (
    DateSearchParams,
    DateSearchSegmentParams,
    FlightSearchParams,
    FlightSearchSegmentParams,
    _validate_segment_count,
)


def _determine_trip_type(
    segments: Sequence[FlightSearchSegmentParams | DateSearchSegmentParams],
) -> TripType:
    """Infer the itinerary type from ordered segments."""
    _validate_segment_count(len(segments))
    if len(segments) == 1:
        return TripType.ONE_WAY
    if len(segments) == 2 and _is_round_trip_pair(segments[0], segments[1]):
        return TripType.ROUND_TRIP
    return TripType.MULTI_CITY


def _is_round_trip_pair(
    first: FlightSearchSegmentParams | DateSearchSegmentParams,
    second: FlightSearchSegmentParams | DateSearchSegmentParams,
) -> bool:
    """Return whether the two segments represent a round trip."""
    return first.origin == second.destination and first.destination == second.origin


def _build_flight_filters(
    params: FlightSearchParams,
    *,
    default_departure_window: str | None,
) -> tuple[FlightSearchFilters, TripType]:
    """Build the exact-date flight-search filters from MCP params."""
    departure_window = (
        params.departure_time_window or params.departure_window or default_departure_window
    )
    time_restrictions = build_time_restrictions(
        departure_window=departure_window,
        arrival_window=params.arrival_time_window,
    )
    segments, trip_type = _build_flight_segments_from_params(params.segments, time_restrictions)
    return FlightSearchFilters(
        trip_type=trip_type,
        passenger_info=PassengerInfo(
            adults=params.passengers,
            num_cabin_luggage=params.num_cabin_luggage,
        ),
        flight_segments=segments,
        stops=parse_max_stops(params.max_stops),
        seat_type=parse_cabin_class(params.cabin_class),
        airlines=parse_airlines(params.airlines),
        max_duration=params.duration,
        layover_restrictions=_build_layover_restrictions(params.max_layover_time),
        sort_by=parse_sort_by(params.sort_by),
    ), trip_type


def _build_flight_segments_from_params(
    segments: list[FlightSearchSegmentParams],
    time_restrictions: Any,
) -> tuple[list[FlightSegment], TripType]:
    """Resolve exact-date segment params into flight segments."""
    trip_type = _determine_trip_type(segments)
    return [
        FlightSegment(
            departure_airport=[[resolve_airport(segment.origin), 0]],
            arrival_airport=[[resolve_airport(segment.destination), 0]],
            travel_date=segment.date,
            time_restrictions=time_restrictions,
        )
        for segment in segments
    ], trip_type


def _build_layover_restrictions(max_layover_time: int | None) -> LayoverRestrictions | None:
    """Create layover restrictions when a max layover is provided."""
    if max_layover_time is None:
        return None
    return LayoverRestrictions(max_duration=max_layover_time)


def _build_date_search_queries(params: DateSearchParams) -> list[tuple[int, FlightSearchParams]]:
    """Materialize each date in the flexible window into an exact itinerary search."""
    start_date = datetime.strptime(params.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(params.end_date, "%Y-%m-%d").date()
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")
    return [
        _build_date_search_query(index, start_date + timedelta(days=index), params)
        for index in range((end_date - start_date).days + 1)
    ]


def _build_date_search_query(
    index: int,
    anchor_date: date,
    params: DateSearchParams,
) -> tuple[int, FlightSearchParams]:
    """Build one exact-date query for a flexible date search."""
    return (
        index,
        FlightSearchParams(
            segments=_materialize_date_search_segments(params.segments, anchor_date),
            departure_window=params.departure_window,
            departure_time_window=params.departure_window,
            arrival_time_window=params.arrival_time_window,
            airlines=params.airlines,
            cabin_class=params.cabin_class,
            max_stops=params.max_stops,
            sort_by="CHEAPEST",
            passengers=params.passengers,
            num_cabin_luggage=None,
            duration=None,
            max_layover_time=None,
        ),
    )


def _materialize_date_search_segments(
    segments: list[DateSearchSegmentParams],
    anchor_date: date,
) -> list[FlightSearchSegmentParams]:
    """Convert a date-search segment template into an exact-date itinerary."""
    exact_segments: list[FlightSearchSegmentParams] = []
    for index, segment in enumerate(segments):
        offset = 0 if index == 0 else segment.day_offset or 0
        exact_segments.append(
            FlightSearchSegmentParams(
                origin=segment.origin,
                destination=segment.destination,
                date=(anchor_date + timedelta(days=offset)).isoformat(),
            )
        )
    return exact_segments
