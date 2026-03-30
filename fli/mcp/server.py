"""Public MCP server facade."""

import os
from typing import Annotated, Any

from pydantic import Field

from fli.core.mcp_params import DateSearchParams, DateSearchSegmentParams
from fli.models import NativeMultiCityResult, NativeMultiCityStep, TripType
from fli.search import SearchFlights

from . import prompts as _prompts  # noqa: F401
from .app import (
    CONFIG,
    CONFIG_SCHEMA,
    FlightSearchConfig,
    FliMCP,
    PromptSpec,
    configuration_resource,
    mcp,
)
from .execution import (
    _execute_date_search,
    _execute_flight_batch,
    _execute_flight_search,
    _serialize_flight_leg,
    _serialize_flight_result,
    _serialize_native_multi_city_as_flight,
)
from .params import (
    FlightSearchParams,
    FlightSearchSegmentParams,
    _build_date_search_queries,
    _build_flight_filters,
    _build_flight_segments_from_params,
    _determine_trip_type,
    _materialize_date_search_segments,
    _validate_segment_count,
)
from .tools import (
    _search_flights_from_params,
    search_flights,
    search_flights_batch,
)

__all__ = [
    "CONFIG",
    "CONFIG_SCHEMA",
    "DateSearchParams",
    "DateSearchSegmentParams",
    "FlightSearchConfig",
    "FlightSearchParams",
    "FlightSearchSegmentParams",
    "FliMCP",
    "NativeMultiCityResult",
    "NativeMultiCityStep",
    "PromptSpec",
    "SearchFlights",
    "TripType",
    "_build_date_search_queries",
    "_build_flight_filters",
    "_build_flight_segments_from_params",
    "_determine_trip_type",
    "_execute_date_search",
    "_execute_flight_batch",
    "_execute_flight_search",
    "_materialize_date_search_segments",
    "_search_flights_from_params",
    "_serialize_flight_leg",
    "_serialize_flight_result",
    "_serialize_native_multi_city_as_flight",
    "_validate_segment_count",
    "configuration_resource",
    "mcp",
    "run",
    "run_http",
    "search_dates",
    "search_flights",
    "search_flights_batch",
]


@mcp.tool(
    annotations={
        "title": "Search Dates",
        "readOnlyHint": True,
        "idempotentHint": True,
    },
)
def search_dates(
    segments: Annotated[
        list[DateSearchSegmentParams],
        Field(description="Ordered itinerary segments; later segments use day_offset"),
    ],
    start_date: Annotated[str, Field(description="Start of date range in YYYY-MM-DD format")],
    end_date: Annotated[str, Field(description="End of date range in YYYY-MM-DD format")],
    airlines: Annotated[
        list[str] | None,
        Field(description="Filter by airline IATA codes (e.g., ['BA', 'AA'])"),
    ] = None,
    cabin_class: Annotated[
        str,
        Field(description="Cabin class: ECONOMY, PREMIUM_ECONOMY, BUSINESS, FIRST"),
    ] = CONFIG.default_cabin_class,
    max_stops: Annotated[
        str,
        Field(description="Maximum stops: ANY, NON_STOP, ONE_STOP, TWO_PLUS_STOPS"),
    ] = "ANY",
    departure_window: Annotated[
        str | None,
        Field(description="Departure time window in 'HH-HH' 24h format (e.g., '6-20')"),
    ] = None,
    arrival_time_window: Annotated[
        str | None,
        Field(description="Arrival time window in 'HH-HH' 24h format (e.g., '8-22')"),
    ] = None,
    sort_by_price: Annotated[
        bool,
        Field(description="Sort results by price (lowest first)"),
    ] = False,
    passengers: Annotated[
        int | None,
        Field(description="Number of adult passengers", ge=1),
    ] = None,
) -> dict[str, Any]:
    """Find the cheapest itinerary dates within a date range."""
    effective_departure_window = departure_window or CONFIG.default_departure_window
    return _execute_date_search(
        DateSearchParams(
            segments=segments,
            start_date=start_date,
            end_date=end_date,
            airlines=airlines,
            cabin_class=cabin_class,
            max_stops=max_stops,
            departure_window=effective_departure_window,
            arrival_time_window=arrival_time_window,
            sort_by_price=sort_by_price,
            passengers=passengers or CONFIG.default_passengers,
        )
    )


def _search_dates_from_params(params: DateSearchParams) -> dict[str, Any]:
    """Compatibility wrapper for tests expecting the params-based signature."""
    return _execute_date_search(params)


search_dates.fn = _search_dates_from_params  # type: ignore[attr-defined]


def run() -> None:
    """Run the MCP server on STDIO."""
    mcp.run(transport="stdio")


def run_http(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the MCP server over HTTP (streamable)."""
    env_host = os.getenv("HOST")
    env_port = os.getenv("PORT")
    bind_host = env_host if env_host else host
    bind_port = int(env_port) if env_port else port
    mcp.run(transport="http", host=bind_host, port=bind_port)


if __name__ == "__main__":
    run()
