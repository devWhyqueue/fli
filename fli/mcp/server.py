"""Flight Search MCP Server.

This module provides an MCP (Model Context Protocol) server for flight search
functionality, enabling AI assistants to search for flights and find cheapest
travel dates.
"""

import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.tools import Tool as FastMCPTool
from mcp.types import (
    GetPromptResult,
    ListPromptsResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
    Tool,
    ToolAnnotations,
)
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from fli.core import (
    build_time_restrictions,
    parse_airlines,
    parse_cabin_class,
    parse_max_stops,
    parse_sort_by,
    resolve_airport,
)
from fli.core.parsers import ParseError
from fli.models import (
    FlightSearchFilters,
    FlightSegment,
    PassengerInfo,
    TripType,
)
from fli.search import SearchFlights


class FlightSearchConfig(BaseSettings):
    """Optional configuration for the Flight Search MCP server."""

    model_config = SettingsConfigDict(env_prefix="FLI_MCP_")

    default_passengers: int = Field(
        1,
        ge=1,
        description="Default number of adult passengers to include in searches.",
    )
    default_currency: str = Field(
        "USD",
        min_length=3,
        max_length=3,
        description="Three-letter currency code returned with search results.",
    )
    default_cabin_class: str = Field(
        "ECONOMY",
        description="Default cabin class used when none is provided.",
    )
    default_sort_by: str = Field(
        "CHEAPEST",
        description="Default sorting strategy for flight results.",
    )
    default_departure_window: str | None = Field(
        None,
        description="Optional default departure window in 'HH-HH' 24-hour format.",
    )
    max_results: int | None = Field(
        None,
        gt=0,
        description="Optional maximum number of results returned by each tool.",
    )


CONFIG = FlightSearchConfig()
CONFIG_SCHEMA = FlightSearchConfig.model_json_schema()


@dataclass
class PromptSpec:
    """Container for prompt metadata and builder."""

    description: str
    build_messages: Callable[[dict[str, str]], list[PromptMessage]]
    arguments: list[PromptArgument] | None = None


class FliMCP(FastMCP):
    """Extended FastMCP server with prompt and annotation support."""

    def __init__(self, name: str | None = None, **settings: Any):
        """Initialize the MCP server with metadata tracking for tools and prompts."""
        self._tool_annotations: dict[str, ToolAnnotations] = {}
        self._prompts: dict[str, PromptSpec] = {}
        super().__init__(name=name, **settings)

    def _setup_handlers(self) -> None:
        """Register MCP protocol handlers including prompts."""
        super()._setup_handlers()
        self._mcp_server.list_tools()(self.list_tools)
        self._mcp_server.list_prompts()(self.list_prompts)
        self._mcp_server.get_prompt()(self.get_prompt)

    def add_tool(
        self,
        func: Callable,
        name: str | None = None,
        description: str | None = None,
        annotations: dict[str, Any] | ToolAnnotations | None = None,
    ) -> None:
        """Register a tool with optional annotations."""
        tool = FastMCPTool.from_function(fn=func, name=name, description=description)
        self._tool_manager.add_tool(tool)
        tool_name = name or func.__name__
        if annotations:
            self._tool_annotations[tool_name] = (
                annotations
                if isinstance(annotations, ToolAnnotations)
                else ToolAnnotations(**annotations)
            )

    def tool(
        self,
        name: str | None = None,
        description: str | None = None,
        annotations: dict[str, Any] | ToolAnnotations | None = None,
    ) -> Callable:
        """Register a tool with optional annotations."""
        if callable(name):
            raise TypeError(
                "The @tool decorator was used incorrectly. "
                "Did you forget to call it? Use @tool() instead of @tool"
            )

        def decorator(func: Callable) -> Callable:
            """Register the wrapped function as an MCP tool."""
            self.add_tool(func, name=name, description=description, annotations=annotations)
            return func

        return decorator

    async def list_tools(self) -> list[Tool]:
        """List all available tools with annotations."""
        tools = list((await self._tool_manager.get_tools()).values())
        return [
            Tool(
                name=info.name,
                description=info.description,
                inputSchema=info.parameters,
                annotations=self._tool_annotations.get(info.name),
            )
            for info in tools
        ]

    def add_prompt(
        self,
        name: str,
        description: str,
        *,
        arguments: list[PromptArgument] | None = None,
        build_messages: Callable[[dict[str, str]], list[PromptMessage]],
    ) -> None:
        """Register a prompt template that can be listed and fetched."""
        self._prompts[name] = PromptSpec(
            description=description,
            arguments=arguments,
            build_messages=build_messages,
        )

    async def list_prompts(self) -> ListPromptsResult:
        """Return all registered prompts."""
        prompts = [
            Prompt(
                name=name,
                description=spec.description,
                arguments=spec.arguments,
            )
            for name, spec in self._prompts.items()
        ]
        return ListPromptsResult(prompts=prompts)

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, str] | None = None,
    ) -> GetPromptResult:
        """Generate prompt content by name."""
        spec = self._prompts.get(name)
        if not spec:
            raise ValueError(f"Unknown prompt: {name}")
        messages = spec.build_messages(arguments or {})
        return GetPromptResult(description=spec.description, messages=messages)


mcp = FliMCP("Flight Search MCP Server")


# =============================================================================
# Request/Response Models
# =============================================================================


class FlightSearchParams(BaseModel):
    """Parameters for searching flights on a specific date."""

    segments: list["FlightSearchSegmentParams"] = Field(
        description="Ordered itinerary segments with explicit IATA airports and dates",
        min_length=1,
        max_length=6,
    )
    departure_window: str | None = Field(
        None,
        description=(
            "Deprecated alias for departure_time_window in 'HH-HH' 24h format (e.g., '6-20')"
        ),
    )
    departure_time_window: str | None = Field(
        None, description="Preferred departure time window in 'HH-HH' 24h format (e.g., '6-20')"
    )
    arrival_time_window: str | None = Field(
        None, description="Preferred arrival time window in 'HH-HH' 24h format (e.g., '8-22')"
    )
    airlines: list[str] | None = Field(
        None, description="Filter by airline IATA codes (e.g., ['BA', 'AA'])"
    )
    cabin_class: str = Field(
        CONFIG.default_cabin_class,
        description="Cabin class: ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST",
    )
    max_stops: str = Field(
        "ANY", description="Maximum stops: ANY, NON_STOP, ONE_STOP, or TWO_PLUS_STOPS"
    )
    sort_by: str = Field(
        CONFIG.default_sort_by,
        description="Sort results by: CHEAPEST, DURATION, DEPARTURE_TIME, or ARRIVAL_TIME",
    )
    passengers: int = Field(
        CONFIG.default_passengers,
        ge=1,
        description="Number of adult passengers",
    )
    num_cabin_luggage: int | None = Field(
        None, ge=0, le=2, description="Number of cabin luggage pieces to include in fare pricing"
    )
    duration: int | None = Field(
        None, ge=1, description="Maximum total itinerary duration in minutes"
    )

    def model_post_init(self, _context: object) -> None:
        """Validate exact-date segment ordering."""
        _validate_segment_count(len(self.segments))
        travel_dates = [
            datetime.strptime(segment.date, "%Y-%m-%d").date() for segment in self.segments
        ]
        if travel_dates != sorted(travel_dates):
            raise ValueError("Flight-search segment dates must be non-decreasing")


class FlightSearchSegmentParams(BaseModel):
    """A single exact-date itinerary segment."""

    origin: str = Field(description="Departure airport IATA code (e.g., 'JFK')")
    destination: str = Field(description="Arrival airport IATA code (e.g., 'LHR')")
    date: str = Field(description="Travel date in YYYY-MM-DD format")


class DateSearchSegmentParams(BaseModel):
    """A segment template for flexible date scans."""

    origin: str = Field(description="Departure airport IATA code (e.g., 'JFK')")
    destination: str = Field(description="Arrival airport IATA code (e.g., 'LHR')")
    day_offset: int | None = Field(
        None,
        ge=0,
        description="Days after the first segment's departure date; omit or use 0 for segment 1",
    )


class DateSearchParams(BaseModel):
    """Parameters for finding the cheapest travel dates within a range."""

    segments: list[DateSearchSegmentParams] = Field(
        description="Ordered itinerary segments; segment 1 uses the scanned date and later segments use day_offset",
        min_length=1,
        max_length=6,
    )
    start_date: str = Field(description="Start of date range in YYYY-MM-DD format")
    end_date: str = Field(description="End of date range in YYYY-MM-DD format")
    airlines: list[str] | None = Field(
        None, description="Filter by airline IATA codes (e.g., ['BA', 'AA'])"
    )
    cabin_class: str = Field(
        CONFIG.default_cabin_class,
        description="Cabin class: ECONOMY, PREMIUM_ECONOMY, BUSINESS, or FIRST",
    )
    max_stops: str = Field(
        "ANY", description="Maximum stops: ANY, NON_STOP, ONE_STOP, or TWO_PLUS_STOPS"
    )
    departure_window: str | None = Field(
        None, description="Preferred departure time window in 'HH-HH' 24h format (e.g., '6-20')"
    )
    arrival_time_window: str | None = Field(
        None, description="Preferred arrival time window in 'HH-HH' 24h format (e.g., '8-22')"
    )
    sort_by_price: bool = Field(False, description="Sort results by price (lowest first)")
    passengers: int = Field(
        CONFIG.default_passengers,
        ge=1,
        description="Number of adult passengers",
    )

    def model_post_init(self, _context: object) -> None:
        """Validate the segment template shape."""
        _validate_segment_count(len(self.segments))
        first_offset = self.segments[0].day_offset
        if first_offset not in (None, 0):
            raise ValueError("First date-search segment cannot define a non-zero day_offset")

        offsets = [0]
        for index, segment in enumerate(self.segments[1:], start=1):
            if segment.day_offset is None:
                raise ValueError(f"Segment {index + 1} must define day_offset")
            offsets.append(segment.day_offset)

        if offsets != sorted(offsets):
            raise ValueError("Date-search segment day_offset values must be non-decreasing")


def _validate_segment_count(segment_count: int) -> None:
    """Validate supported itinerary lengths."""
    if segment_count < 1:
        raise ValueError("At least one segment is required")
    if segment_count > 6:
        raise ValueError("No more than 6 segments are supported")


def _determine_trip_type(
    segments: list[FlightSearchSegmentParams | DateSearchSegmentParams],
) -> TripType:
    """Infer the itinerary type from ordered segments."""
    _validate_segment_count(len(segments))
    if len(segments) == 1:
        return TripType.ONE_WAY

    if len(segments) == 2:
        first, second = segments
        if first.origin == second.destination and first.destination == second.origin:
            return TripType.ROUND_TRIP

    return TripType.MULTI_CITY


def _build_flight_segments_from_params(
    segments: list[FlightSearchSegmentParams],
    time_restrictions: Any,
) -> tuple[list[FlightSegment], TripType]:
    """Resolve exact-date segment params into flight segments."""
    trip_type = _determine_trip_type(segments)
    resolved_segments = []
    for segment in segments:
        resolved_segments.append(
            FlightSegment(
                departure_airport=[[resolve_airport(segment.origin), 0]],
                arrival_airport=[[resolve_airport(segment.destination), 0]],
                travel_date=segment.date,
                time_restrictions=time_restrictions,
            )
        )
    return resolved_segments, trip_type


def _build_flight_filters(params: FlightSearchParams) -> tuple[FlightSearchFilters, TripType]:
    """Build the exact-date flight-search filters from MCP params."""
    cabin_class = parse_cabin_class(params.cabin_class)
    max_stops = parse_max_stops(params.max_stops)
    sort_by = parse_sort_by(params.sort_by)
    airlines = parse_airlines(params.airlines)
    departure_window = (
        params.departure_time_window or params.departure_window or CONFIG.default_departure_window
    )
    time_restrictions = build_time_restrictions(
        departure_window=departure_window,
        arrival_window=params.arrival_time_window,
    )
    segments, trip_type = _build_flight_segments_from_params(params.segments, time_restrictions)
    filters = FlightSearchFilters(
        trip_type=trip_type,
        passenger_info=PassengerInfo(
            adults=params.passengers,
            num_cabin_luggage=params.num_cabin_luggage,
        ),
        flight_segments=segments,
        stops=max_stops,
        seat_type=cabin_class,
        airlines=airlines,
        max_duration=params.duration,
        sort_by=sort_by,
    )
    return filters, trip_type


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


def _build_date_search_queries(params: DateSearchParams) -> list[tuple[int, FlightSearchParams]]:
    """Materialize each date in the flexible window into an exact itinerary search."""
    start_date = datetime.strptime(params.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(params.end_date, "%Y-%m-%d").date()
    if end_date < start_date:
        raise ValueError("end_date must be on or after start_date")

    queries: list[tuple[int, FlightSearchParams]] = []
    for index in range((end_date - start_date).days + 1):
        anchor_date = start_date + timedelta(days=index)
        queries.append(
            (
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
                ),
            )
        )
    return queries


# =============================================================================
# Result Serialization
# =============================================================================


def _serialize_flight_leg(leg: Any) -> dict[str, Any]:
    """Serialize a single flight leg to a dictionary."""
    return {
        "departure_airport": leg.departure_airport,
        "arrival_airport": leg.arrival_airport,
        "departure_time": leg.departure_datetime,
        "arrival_time": leg.arrival_datetime,
        "duration": leg.duration,
        "airline": leg.airline,
        "flight_number": leg.flight_number,
    }


def _serialize_flight_result(flight: Any, is_round_trip: bool = False) -> dict[str, Any]:
    """Serialize a flight result (or round-trip pair) to a dictionary."""
    if is_round_trip and isinstance(flight, tuple):
        outbound, return_flight = flight
        return {
            # Google Flights returns the full round-trip price on the outbound leg
            "price": outbound.price,
            "currency": CONFIG.default_currency,
            "legs": [
                *[_serialize_flight_leg(leg) for leg in outbound.legs],
                *[_serialize_flight_leg(leg) for leg in return_flight.legs],
            ],
        }
    else:
        payload = {
            "price": flight.price,
            "currency": CONFIG.default_currency,
            "legs": [_serialize_flight_leg(leg) for leg in flight.legs],
        }
        if getattr(flight, "segment_prices", None) is not None:
            payload["segment_prices"] = flight.segment_prices
        return payload


def _execute_flight_search(params: FlightSearchParams) -> dict[str, Any]:
    """Execute a flight search and return formatted results."""
    try:
        filters, trip_type = _build_flight_filters(params)

        # Perform search
        search_client = SearchFlights()
        flights = search_client.search(filters)

        if not flights:
            return {"success": True, "flights": [], "count": 0, "trip_type": trip_type.name}

        # Serialize results
        is_round_trip = trip_type == TripType.ROUND_TRIP
        flight_results = [_serialize_flight_result(f, is_round_trip) for f in flights]

        if CONFIG.max_results:
            flight_results = flight_results[: CONFIG.max_results]

        return {
            "success": True,
            "flights": flight_results,
            "count": len(flight_results),
            "trip_type": trip_type.name,
        }

    except ParseError as e:
        return {"success": False, "error": str(e), "flights": []}
    except Exception as e:
        error_msg = str(e)
        if "validation error" in error_msg.lower():
            return {"success": False, "error": "Invalid parameter value", "flights": []}
        return {"success": False, "error": f"Search failed: {error_msg}", "flights": []}


def _execute_date_search(params: DateSearchParams) -> dict[str, Any]:
    """Execute a date search and return formatted results."""
    try:
        queries = _build_date_search_queries(params)
        trip_type = _determine_trip_type(params.segments)
        executed = _execute_flight_batch(queries, min(8, len(queries)))

        if executed["failed"]:
            error_result = next(
                (item for item in executed["results"] if not item.get("success", False)),
                None,
            )
            return {
                "success": False,
                "error": error_result.get("error", "Date search failed")
                if error_result
                else "Date search failed",
                "dates": [],
            }

        date_results = []
        for result in executed["results"]:
            flights = result["flights"]
            if not flights:
                continue

            segment_dates = [segment.date for segment in queries[result["index"]][1].segments]
            cheapest_price = min(flight["price"] for flight in flights)
            date_results.append(
                {
                    "date": segment_dates[0],
                    "segment_dates": segment_dates,
                    "price": cheapest_price,
                    "currency": CONFIG.default_currency,
                    "return_date": segment_dates[1] if trip_type == TripType.ROUND_TRIP else None,
                }
            )

        if not date_results:
            return {
                "success": True,
                "dates": [],
                "count": 0,
                "trip_type": trip_type.name,
                "date_range": f"{params.start_date} to {params.end_date}",
            }

        if params.sort_by_price:
            date_results.sort(key=lambda x: x["price"])

        if CONFIG.max_results:
            date_results = date_results[: CONFIG.max_results]

        return {
            "success": True,
            "dates": date_results,
            "count": len(date_results),
            "trip_type": trip_type.name,
            "date_range": f"{params.start_date} to {params.end_date}",
        }

    except ValueError as e:
        return {"success": False, "error": str(e), "dates": []}
    except ParseError as e:
        return {"success": False, "error": str(e), "dates": []}
    except Exception as e:
        return {"success": False, "error": f"Search failed: {str(e)}", "dates": []}


# =============================================================================
# MCP Tools
# =============================================================================


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
) -> dict[str, Any]:
    """Search for flights for an exact-date itinerary.

    Supports one-way, round-trip, and multi-city searches with shared filters.
    """
    effective_departure_window = (
        departure_time_window or departure_window or CONFIG.default_departure_window
    )
    params = FlightSearchParams(
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
    )
    return _execute_flight_search(params)


def _search_flights_from_params(params: FlightSearchParams) -> dict[str, Any]:
    """Compatibility wrapper for tests expecting the params-based signature."""
    return _execute_flight_search(params)


search_flights.fn = _search_flights_from_params  # type: ignore[attr-defined]


def _execute_flight_batch(
    queries: list[tuple[int, FlightSearchParams]], parallelism: int
) -> dict[str, Any]:
    """Execute batch flight searches with bounded concurrency."""
    if not queries:
        return {"success": True, "results": [], "count": 0, "failed": 0}

    results: list[dict[str, Any] | None] = [None] * len(queries)
    with ThreadPoolExecutor(max_workers=parallelism) as executor:
        futures = {
            executor.submit(_execute_flight_search, query): (position, original_index)
            for position, (original_index, query) in enumerate(queries)
        }
        for future in as_completed(futures):
            position, original_index = futures[future]
            try:
                payload = future.result()
                results[position] = {"index": original_index, **payload}
            except Exception as e:  # pragma: no cover - defensive guard
                results[position] = {
                    "index": original_index,
                    "success": False,
                    "error": f"Batch item execution failed: {e}",
                    "flights": [],
                }

    final_results = [item for item in results if item is not None]
    failures = sum(1 for item in final_results if not item.get("success", False))
    return {
        "success": failures == 0,
        "results": final_results,
        "count": len(final_results),
        "failed": failures,
        "parallelism": parallelism,
    }


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
        list[dict[str, Any]],
        Field(description="List of flight-search query objects matching search_flights inputs"),
    ],
    parallelism: Annotated[
        int,
        Field(description="Max number of concurrent searches", ge=1, le=32),
    ] = 4,
) -> dict[str, Any]:
    """Run multiple flight searches in one request and return per-item results."""
    valid_queries: list[tuple[int, FlightSearchParams]] = []
    precomputed: list[dict[str, Any] | None] = [None] * len(queries)

    for index, query in enumerate(queries):
        try:
            valid_queries.append((index, FlightSearchParams(**query)))
        except Exception as e:
            precomputed[index] = {
                "index": index,
                "success": False,
                "error": f"Invalid query payload: {e}",
                "flights": [],
            }

    if not valid_queries:
        return {
            "success": False,
            "results": [item for item in precomputed if item is not None],
            "count": len(precomputed),
            "failed": len(precomputed),
            "parallelism": parallelism,
        }

    executed = _execute_flight_batch(valid_queries, parallelism)
    for item in executed["results"]:
        precomputed[item["index"]] = item

    merged_results: list[dict[str, Any]] = [item for item in precomputed if item is not None]

    failures = sum(1 for item in merged_results if not item.get("success", False))
    return {
        "success": failures == 0,
        "results": merged_results,
        "count": len(merged_results),
        "failed": failures,
        "parallelism": parallelism,
    }


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
    """Find the cheapest itinerary dates within a date range.

    Segment 1 varies between start_date and end_date; later segments use day_offset.
    """
    effective_departure_window = departure_window or CONFIG.default_departure_window
    params = DateSearchParams(
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
    return _execute_date_search(params)


def _search_dates_from_params(params: DateSearchParams) -> dict[str, Any]:
    """Compatibility wrapper for tests expecting the params-based signature."""
    return _execute_date_search(params)


search_dates.fn = _search_dates_from_params  # type: ignore[attr-defined]


# =============================================================================
# Prompts
# =============================================================================


def _build_search_prompt(args: dict[str, str]) -> list[PromptMessage]:
    """Create a helper prompt to guide flight searches."""
    origin = args.get("origin", "JFK").upper()
    destination = args.get("destination", "LHR").upper()
    date = args.get("date") or datetime.now(timezone.utc).date().isoformat()
    prefer_non_stop = args.get("prefer_non_stop", "true").lower()
    max_stops_hint = "NON_STOP" if prefer_non_stop in {"true", "1", "yes"} else "ANY"
    text = (
        "Use the `search_flights` tool to look for flights from "
        f"{origin} to {destination} departing on {date}. "
        f"Set `max_stops` to '{max_stops_hint}' and highlight the three most affordable options."
    )
    return [
        PromptMessage(role="user", content=TextContent(type="text", text=text)),
    ]


def _build_budget_prompt(args: dict[str, str]) -> list[PromptMessage]:
    """Create a helper prompt to guide flexible date searches."""
    origin = args.get("origin", "SFO").upper()
    destination = args.get("destination", "NRT").upper()
    today = datetime.now(timezone.utc).date()
    start_date = args.get("start_date") or (today + timedelta(days=30)).isoformat()
    end_date = args.get("end_date") or (today + timedelta(days=90)).isoformat()
    text = (
        "Use the `search_dates` tool to find the lowest fares for an itinerary that starts at "
        f"{origin}, reaches {destination}, and departs between {start_date} and {end_date}. "
        "Represent the trip with `segments` and use day offsets for later legs when needed."
    )
    return [
        PromptMessage(role="user", content=TextContent(type="text", text=text)),
    ]


mcp.add_prompt(
    name="search-direct-flight",
    description=(
        "Generate a tool call to find direct flights between two airports on a target date."
    ),
    arguments=[
        PromptArgument(
            name="origin",
            description="Departure airport IATA code",
            required=True,
        ),
        PromptArgument(
            name="destination",
            description="Arrival airport IATA code",
            required=True,
        ),
        PromptArgument(
            name="date",
            description="Departure date (YYYY-MM-DD)",
            required=False,
        ),
        PromptArgument(
            name="prefer_non_stop",
            description="Set to true to prefer nonstop itineraries",
            required=False,
        ),
    ],
    build_messages=_build_search_prompt,
)

mcp.add_prompt(
    name="find-budget-window",
    description=("Suggest the cheapest travel dates for a route within a flexible window."),
    arguments=[
        PromptArgument(
            name="origin",
            description="Departure airport IATA code",
            required=True,
        ),
        PromptArgument(
            name="destination",
            description="Arrival airport IATA code",
            required=True,
        ),
        PromptArgument(
            name="start_date",
            description="Start of the travel window (YYYY-MM-DD)",
            required=False,
        ),
        PromptArgument(
            name="end_date",
            description="End of the travel window (YYYY-MM-DD)",
            required=False,
        ),
        PromptArgument(
            name="duration",
            description="Desired trip length in days",
            required=False,
        ),
    ],
    build_messages=_build_budget_prompt,
)


# =============================================================================
# Resources
# =============================================================================


@mcp.resource(
    "resource://fli-mcp/configuration",
    name="Fli MCP Configuration",
    description=(
        "Optional configuration defaults and environment variables for the Flight "
        "Search MCP server."
    ),
    mime_type="application/json",
)
def configuration_resource() -> str:
    """Expose configuration defaults and schema as a resource."""
    payload = {
        "defaults": CONFIG.model_dump(),
        "schema": CONFIG_SCHEMA,
        "environment": {
            "prefix": "FLI_MCP_",
            "variables": {
                "FLI_MCP_DEFAULT_PASSENGERS": "Adjust the default passenger count.",
                "FLI_MCP_DEFAULT_CURRENCY": "Override the currency code returned with results.",
                "FLI_MCP_DEFAULT_CABIN_CLASS": "Set a default cabin class.",
                "FLI_MCP_DEFAULT_SORT_BY": "Set the default result sorting strategy.",
                "FLI_MCP_DEFAULT_DEPARTURE_WINDOW": "Provide a default departure window (HH-HH).",
                "FLI_MCP_MAX_RESULTS": "Limit the maximum number of results returned by tools.",
            },
        },
    }
    return json.dumps(payload, indent=2)


# =============================================================================
# Entry Points
# =============================================================================


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
