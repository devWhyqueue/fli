"""Facade for shared MCP request models and builders."""

from .mcp_builders import (
    _build_date_search_queries,
    _build_flight_filters,
    _build_flight_segments_from_params,
    _build_journey_search_queries,
    _determine_trip_type,
    _materialize_date_search_segments,
)
from .mcp_models import (
    DateSearchParams,
    DateSearchSegmentParams,
    FlightSearchParams,
    FlightSearchSegmentParams,
    JourneySearchParams,
    JourneySearchSegmentParams,
    _validate_segment_count,
)

__all__ = [
    "DateSearchParams",
    "DateSearchSegmentParams",
    "FlightSearchParams",
    "FlightSearchSegmentParams",
    "JourneySearchParams",
    "JourneySearchSegmentParams",
    "_build_date_search_queries",
    "_build_flight_filters",
    "_build_flight_segments_from_params",
    "_build_journey_search_queries",
    "_determine_trip_type",
    "_materialize_date_search_segments",
    "_validate_segment_count",
]
