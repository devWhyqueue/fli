"""Facade for MCP request models and builders."""

from fli.core.mcp_params import (
    DateSearchParams,
    DateSearchSegmentParams,
    FlightSearchParams,
    FlightSearchSegmentParams,
    _build_date_search_queries,
    _build_flight_segments_from_params,
    _determine_trip_type,
    _materialize_date_search_segments,
    _validate_segment_count,
)
from fli.core.mcp_params import (
    _build_flight_filters as _core_build_flight_filters,
)

from .app import CONFIG


def _build_flight_filters(params: FlightSearchParams):
    """Build flight filters using the MCP server defaults."""
    return _core_build_flight_filters(
        params,
        default_departure_window=CONFIG.default_departure_window,
    )


__all__ = [
    "DateSearchParams",
    "DateSearchSegmentParams",
    "FlightSearchParams",
    "FlightSearchSegmentParams",
    "_build_date_search_queries",
    "_build_flight_filters",
    "_build_flight_segments_from_params",
    "_determine_trip_type",
    "_materialize_date_search_segments",
    "_validate_segment_count",
]
