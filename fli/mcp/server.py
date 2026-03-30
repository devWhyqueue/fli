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
from datetime import datetime, timedelta, timezone
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
    build_date_search_segments,
    build_flight_segments,
    build_time_restrictions,
    parse_airlines,
    parse_cabin_class,
    parse_max_stops,
    parse_sort_by,
    resolve_airport,
)
from fli.core.parsers import ParseError
from fli.models import (
    DateSearchFilters,
    FlightSearchFilters,
    PassengerInfo,
    TripType,
)
from fli.search import SearchDates, SearchFlights


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

    origin: str = Field(description="Departure airport IATA code (e.g., 'JFK', 'LAX')")
    destination: str = Field(description="Arrival airport IATA code (e.g., 'LHR', 'NRT')")
    departure_date: str = Field(description="Outbound travel date in YYYY-MM-DD format")
    return_date: str | None = Field(
        None, description="Return date in YYYY-MM-DD format (omit for one-way)"
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


class DateSearchParams(BaseModel):
    """Parameters for finding the cheapest travel dates within a range."""

    origin: str = Field(description="Departure airport IATA code (e.g., 'JFK', 'LAX')")
    destination: str = Field(description="Arrival airport IATA code (e.g., 'LHR', 'NRT')")
    start_date: str = Field(description="Start of date range in YYYY-MM-DD format")
    end_date: str = Field(description="End of date range in YYYY-MM-DD format")
    trip_duration: int = Field(
        3, ge=1, description="Trip duration in days (for round-trip searches)"
    )
    is_round_trip: bool = Field(False, description="Search for round-trip flights")
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
    sort_by_price: bool = Field(False, description="Sort results by price (lowest first)")
    passengers: int = Field(
        CONFIG.default_passengers,
        ge=1,
        description="Number of adult passengers",
    )


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
        return {
            "price": flight.price,
            "currency": CONFIG.default_currency,
            "legs": [_serialize_flight_leg(leg) for leg in flight.legs],
        }


def _serialize_date_result(date_result: Any) -> dict[str, Any]:
    """Serialize a date price result to a dictionary."""
    return {
        "date": date_result.date,
        "price": date_result.price,
        "currency": CONFIG.default_currency,
        "return_date": getattr(date_result, "return_date", None),
    }


# =============================================================================
# Search Execution
# =============================================================================


def _execute_flight_search(params: FlightSearchParams) -> dict[str, Any]:
    """Execute a flight search and return formatted results."""
    try:
        # Parse inputs using shared utilities
        origin = resolve_airport(params.origin)
        destination = resolve_airport(params.destination)
        cabin_class = parse_cabin_class(params.cabin_class)
        max_stops = parse_max_stops(params.max_stops)
        sort_by = parse_sort_by(params.sort_by)
        airlines = parse_airlines(params.airlines)

        # Build time restrictions
        departure_window = (
            params.departure_time_window
            or params.departure_window
            or CONFIG.default_departure_window
        )
        time_restrictions = build_time_restrictions(
            departure_window=departure_window,
            arrival_window=params.arrival_time_window,
        )

        # Build flight segments
        segments, trip_type = build_flight_segments(
            origin=origin,
            destination=destination,
            departure_date=params.departure_date,
            return_date=params.return_date,
            time_restrictions=time_restrictions,
        )

        # Create search filters
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
        # Parse inputs using shared utilities
        origin = resolve_airport(params.origin)
        destination = resolve_airport(params.destination)
        cabin_class = parse_cabin_class(params.cabin_class)
        max_stops = parse_max_stops(params.max_stops)
        airlines = parse_airlines(params.airlines)

        # Build time restrictions
        departure_window = params.departure_window or CONFIG.default_departure_window
        time_restrictions = build_time_restrictions(departure_window) if departure_window else None

        # Build flight segments
        segments, trip_type = build_date_search_segments(
            origin=origin,
            destination=destination,
            start_date=params.start_date,
            trip_duration=params.trip_duration,
            is_round_trip=params.is_round_trip,
            time_restrictions=time_restrictions,
        )

        # Create search filters
        filters = DateSearchFilters(
            trip_type=trip_type,
            passenger_info=PassengerInfo(adults=params.passengers),
            flight_segments=segments,
            stops=max_stops,
            seat_type=cabin_class,
            airlines=airlines,
            from_date=params.start_date,
            to_date=params.end_date,
            duration=params.trip_duration if params.is_round_trip else None,
        )

        # Perform search
        search_client = SearchDates()
        dates = search_client.search(filters)

        if not dates:
            return {
                "success": True,
                "dates": [],
                "count": 0,
                "trip_type": trip_type.name,
                "date_range": f"{params.start_date} to {params.end_date}",
            }

        if params.sort_by_price:
            dates.sort(key=lambda x: x.price)

        # Serialize results
        date_results = [_serialize_date_result(d) for d in dates]

        if CONFIG.max_results:
            date_results = date_results[: CONFIG.max_results]

        return {
            "success": True,
            "dates": date_results,
            "count": len(date_results),
            "trip_type": trip_type.name,
            "date_range": f"{params.start_date} to {params.end_date}",
            "duration": params.trip_duration if params.is_round_trip else None,
        }

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
    origin: Annotated[str, Field(description="Departure airport IATA code (e.g., 'JFK')")],
    destination: Annotated[str, Field(description="Arrival airport IATA code (e.g., 'LHR')")],
    departure_date: Annotated[str, Field(description="Travel date in YYYY-MM-DD format")],
    return_date: Annotated[
        str | None,
        Field(description="Return date in YYYY-MM-DD format (omit for one-way)"),
    ] = None,
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
    """Search for flights between two airports on a specific date.

    Returns a list of available flights with prices, durations, and leg details.
    Supports one-way and round-trip searches with various filtering options.
    """
    effective_departure_window = (
        departure_time_window or departure_window or CONFIG.default_departure_window
    )
    params = FlightSearchParams(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
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
    origin: Annotated[str, Field(description="Departure airport IATA code (e.g., 'JFK')")],
    destination: Annotated[str, Field(description="Arrival airport IATA code (e.g., 'LHR')")],
    start_date: Annotated[str, Field(description="Start of date range in YYYY-MM-DD format")],
    end_date: Annotated[str, Field(description="End of date range in YYYY-MM-DD format")],
    trip_duration: Annotated[
        int,
        Field(description="Trip duration in days for round-trips", ge=1),
    ] = 3,
    is_round_trip: Annotated[
        bool,
        Field(description="Search for round-trip flights"),
    ] = False,
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
    sort_by_price: Annotated[
        bool,
        Field(description="Sort results by price (lowest first)"),
    ] = False,
    passengers: Annotated[
        int | None,
        Field(description="Number of adult passengers", ge=1),
    ] = None,
) -> dict[str, Any]:
    """Find the cheapest travel dates between two airports within a date range.

    Returns a list of dates with their prices, useful for flexible travel planning.
    Supports both one-way and round-trip searches.
    """
    effective_departure_window = departure_window or CONFIG.default_departure_window
    params = DateSearchParams(
        origin=origin,
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        trip_duration=trip_duration,
        is_round_trip=is_round_trip,
        airlines=airlines,
        cabin_class=cabin_class,
        max_stops=max_stops,
        departure_window=effective_departure_window,
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
    duration = args.get("duration", "7")
    text = (
        "Use the `search_dates` tool to find the lowest fares between "
        f"{origin} and {destination} for trips between {start_date} and {end_date}. "
        f"Set trip_duration to {duration} days and sort the results by price."
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


def run():
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
