"""Exact-date MCP tool definitions."""

from typing import Annotated, Any

from pydantic import Field

from fli.core.mcp_params import FlightSearchParams, FlightSearchSegmentParams

from .app import CONFIG, mcp
from .execution import _execute_flight_batch, _execute_flight_search


@mcp.tool(
    annotations={
        "title": "Search Flights",
        "readOnlyHint": True,
        "idempotentHint": True,
    },
)
def search_flights(
    segments: Annotated[
        list[FlightSearchSegmentParams],
        Field(description="Ordered itinerary segments with explicit dates"),
    ],
    departure_window: Annotated[
        str | None,
        Field(description="Deprecated alias for departure_time_window in 'HH-HH' format"),
    ] = None,
    departure_time_window: Annotated[
        str | None,
        Field(description="Departure time window in 'HH-HH' 24h format (e.g., '6-20')"),
    ] = None,
    arrival_time_window: Annotated[
        str | None,
        Field(description="Arrival time window in 'HH-HH' 24h format (e.g., '8-22')"),
    ] = None,
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
    sort_by: Annotated[
        str,
        Field(description="Sort by: CHEAPEST, DURATION, DEPARTURE_TIME, ARRIVAL_TIME"),
    ] = CONFIG.default_sort_by,
    passengers: Annotated[
        int | None,
        Field(description="Number of adult passengers", ge=1),
    ] = None,
    num_cabin_luggage: Annotated[
        int | None,
        Field(description="Number of cabin luggage pieces to include in fare", ge=0, le=2),
    ] = None,
    duration: Annotated[
        int | None,
        Field(description="Maximum itinerary duration in minutes", ge=1),
    ] = None,
    max_layover_time: Annotated[
        int | None,
        Field(
            description="Maximum layover duration in minutes within each searched segment",
            ge=1,
        ),
    ] = None,
) -> dict[str, Any]:
    """Search for flights for an exact-date itinerary."""
    return _execute_flight_search(
        _build_search_flights_params(
            segments=segments,
            departure_window=departure_window,
            departure_time_window=departure_time_window,
            arrival_time_window=arrival_time_window,
            airlines=airlines,
            cabin_class=cabin_class,
            max_stops=max_stops,
            sort_by=sort_by,
            passengers=passengers,
            num_cabin_luggage=num_cabin_luggage,
            duration=duration,
            max_layover_time=max_layover_time,
        )
    )


def _build_search_flights_params(
    *,
    segments: list[FlightSearchSegmentParams],
    departure_window: str | None,
    departure_time_window: str | None,
    arrival_time_window: str | None,
    airlines: list[str] | None,
    cabin_class: str,
    max_stops: str,
    sort_by: str,
    passengers: int | None,
    num_cabin_luggage: int | None,
    duration: int | None,
    max_layover_time: int | None,
) -> FlightSearchParams:
    """Build a normalized exact-date flight-search params model."""
    effective_departure_window = (
        departure_time_window or departure_window or CONFIG.default_departure_window
    )
    return FlightSearchParams(
        segments=segments,
        departure_window=departure_window,
        departure_time_window=effective_departure_window,
        arrival_time_window=arrival_time_window,
        airlines=airlines,
        cabin_class=cabin_class,
        max_stops=max_stops,
        sort_by=sort_by,
        passengers=passengers or CONFIG.default_passengers,
        num_cabin_luggage=num_cabin_luggage,
        duration=duration,
        max_layover_time=max_layover_time,
    )


def _search_flights_from_params(params: FlightSearchParams) -> dict[str, Any]:
    """Compatibility wrapper for tests expecting the params-based signature."""
    return _execute_flight_search(params)


search_flights.fn = _search_flights_from_params  # type: ignore[attr-defined]


@mcp.tool(
    name="search_flights_batch",
    annotations={
        "title": "Search Flights Batch",
        "readOnlyHint": True,
        "idempotentHint": True,
    },
)
def search_flights_batch(
    queries: Annotated[
        list[FlightSearchParams],
        Field(
            description=(
                "List of exact-date flight-search payloads matching search_flights inputs. "
                "Use this for Cartesian-product itinerary ranking across airport/date options "
                "when you need the cheapest complete journey by total price."
            )
        ),
    ],
    parallelism: Annotated[
        int,
        Field(description="Max number of concurrent searches", ge=1, le=32),
    ] = 4,
) -> dict[str, Any]:
    """Run multiple exact-date flight searches in one request and return per-item results."""
    valid_queries, precomputed = _validate_batch_queries(queries)
    effective_parallelism = parallelism
    if valid_queries:
        executed = _execute_flight_batch(valid_queries, parallelism)
        effective_parallelism = executed["parallelism"]
        for item in executed["results"]:
            precomputed[item["index"]] = item
    return _batch_payload(precomputed, effective_parallelism)


def _validate_batch_queries(
    queries: list[FlightSearchParams],
) -> tuple[list[tuple[int, FlightSearchParams]], list[dict[str, Any] | None]]:
    """Validate typed batch query payloads."""
    valid_queries: list[tuple[int, FlightSearchParams]] = []
    precomputed: list[dict[str, Any] | None] = [None] * len(queries)
    for index, query in enumerate(queries):
        try:
            valid_queries.append((index, query))
        except Exception as exc:
            precomputed[index] = {
                "index": index,
                "success": False,
                "error": f"Invalid query payload: {exc}",
                "flights": [],
            }
    return valid_queries, precomputed


def _batch_payload(
    precomputed: list[dict[str, Any] | None],
    parallelism: int,
) -> dict[str, Any]:
    """Build the merged batch response payload."""
    merged_results = [item for item in precomputed if item is not None]
    failures = sum(1 for item in merged_results if not item.get("success", False))
    return {
        "success": failures == 0,
        "results": merged_results,
        "count": len(merged_results),
        "failed": failures,
        "parallelism": parallelism,
    }
