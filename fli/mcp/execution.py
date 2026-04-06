"""Execution and serialization helpers for MCP flight tools."""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from fli.core.parsers import ParseError
from fli.models import TripType
from fli.search import SearchFlights as DefaultSearchFlights

from .app import CONFIG, google_request_params
from .internal.execution_payloads import (
    date_failure_payload,
    date_success_payload,
    flight_error_payload,
    journey_failure_payload,
    journey_success_payload,
    success_payload,
)
from .internal.multicity import execute_multicity_decomposed
from .params import (
    DateSearchParams,
    FlightSearchParams,
    JourneySearchParams,
    _build_date_search_queries,
    _build_flight_filters,
    _build_journey_search_queries,
    _determine_trip_type,
)

_ROUND_TRIP_BATCH_PARALLELISM_CAP = 3


def _serialize_flight_leg(leg: Any) -> dict[str, Any]:
    """Serialize a single flight leg to a dictionary."""
    return {
        "departure_airport": getattr(leg.departure_airport, "name", leg.departure_airport),
        "arrival_airport": getattr(leg.arrival_airport, "name", leg.arrival_airport),
        "departure_time": _serialize_datetime(leg.departure_datetime),
        "arrival_time": _serialize_datetime(leg.arrival_datetime),
        "duration": leg.duration,
        "airline": getattr(leg.airline, "name", leg.airline),
        "flight_number": leg.flight_number,
    }


def _serialize_datetime(value: Any) -> str | None:
    """Serialize an optional datetime-like object to ISO 8601."""
    if value is None:
        return None
    return value.isoformat()


def _serialize_flight_result(flight: Any, is_round_trip: bool = False) -> dict[str, Any]:
    """Serialize a flight result (or round-trip pair) to a dictionary."""
    if is_round_trip and isinstance(flight, tuple):
        outbound, return_flight = flight
        return {
            "price": outbound.price,
            "currency": CONFIG.default_currency,
            "legs": [
                *[_serialize_flight_leg(leg) for leg in outbound.legs],
                *[_serialize_flight_leg(leg) for leg in return_flight.legs],
            ],
        }
    payload = {
        "price": flight.price,
        "currency": CONFIG.default_currency,
        "legs": [_serialize_flight_leg(leg) for leg in flight.legs],
    }
    if getattr(flight, "segment_prices", None) is not None:
        payload["segment_prices"] = flight.segment_prices
    if getattr(flight, "cabin_bag_included", None) is not None:
        payload["cabin_bag_included"] = flight.cabin_bag_included
    return payload


def _execute_flight_search(params: FlightSearchParams) -> dict[str, Any]:
    """Execute a flight search and return formatted results."""
    try:
        filters, trip_type = _build_flight_filters(params)
        if trip_type == TripType.MULTI_CITY:
            return execute_multicity_decomposed(
                params,
                trip_type,
                search_client_factory=_search_flights_client,
                collect_flights_fn=_collect_flights,
            )
        flights = _collect_flights(_search_flights_client(), filters, trip_type)
        return success_payload(flights, trip_type.name, CONFIG.max_results)
    except ParseError as exc:
        return {"success": False, "error": str(exc), "flights": []}
    except Exception as exc:
        return flight_error_payload(exc)


def _collect_flights(search_client: Any, filters: Any, trip_type: TripType) -> list[dict[str, Any]]:
    """Run the requested flight search and normalize the serialized results."""
    raw_flights = search_client.search(filters)
    if not raw_flights:
        return []
    is_round_trip = trip_type == TripType.ROUND_TRIP
    return [_serialize_flight_result(flight, is_round_trip) for flight in raw_flights]


def _execute_date_search(params: DateSearchParams) -> dict[str, Any]:
    """Execute a date search and return formatted results."""
    try:
        queries = _build_date_search_queries(params)
        trip_type = _determine_trip_type(params.segments)
        executed = _resolve_batch_executor()(queries, min(8, len(queries)))
        if executed["failed"]:
            return date_failure_payload(executed["results"])
        return date_success_payload(
            executed["results"],
            queries,
            params,
            trip_type,
            CONFIG.max_results,
            CONFIG.default_currency,
        )
    except ParseError as exc:
        return {"success": False, "error": str(exc), "dates": []}
    except ValueError as exc:
        return {"success": False, "error": str(exc), "dates": []}
    except Exception as exc:
        return {"success": False, "error": f"Search failed: {exc}", "dates": []}


def _execute_journey_search(params: JourneySearchParams) -> dict[str, Any]:
    """Execute an exact-date journey matrix search and return ranked journeys."""
    try:
        queries, skipped_combinations = _build_journey_search_queries(params)
        if not queries:
            return journey_success_payload(
                [],
                queries,
                params,
                skipped_combinations,
                CONFIG.max_results,
                CONFIG.default_currency,
            )
        executed = _resolve_batch_executor()(queries, min(8, len(queries)))
        if executed["failed"] and not any(item.get("flights") for item in executed["results"]):
            return journey_failure_payload(executed["results"])
        return journey_success_payload(
            executed["results"],
            queries,
            params,
            skipped_combinations,
            CONFIG.max_results,
            CONFIG.default_currency,
        )
    except ParseError as exc:
        return {"success": False, "error": str(exc), "journeys": []}
    except ValueError as exc:
        return {"success": False, "error": str(exc), "journeys": []}
    except Exception as exc:
        return {"success": False, "error": f"Search failed: {exc}", "journeys": []}


def _execute_flight_batch(
    queries: list[tuple[int, FlightSearchParams]],
    parallelism: int,
) -> dict[str, Any]:
    """Execute batch flight searches with bounded concurrency."""
    if not queries:
        return {"success": True, "results": [], "count": 0, "failed": 0}
    effective_parallelism = _effective_batch_parallelism(queries, parallelism)
    results: list[dict[str, Any] | None] = [None] * len(queries)
    with ThreadPoolExecutor(max_workers=effective_parallelism) as executor:
        futures = {
            executor.submit(_execute_flight_search, query): (position, original_index)
            for position, (original_index, query) in enumerate(queries)
        }
        for future in as_completed(futures):
            position, original_index = futures[future]
            results[position] = _resolve_batch_result(future, original_index)
    final_results = [item for item in results if item is not None]
    failures = sum(1 for item in final_results if not item.get("success", False))
    return {
        "success": failures == 0,
        "results": final_results,
        "count": len(final_results),
        "failed": failures,
        "parallelism": effective_parallelism,
    }


def _effective_batch_parallelism(
    queries: list[tuple[int, FlightSearchParams]],
    requested_parallelism: int,
) -> int:
    """Bound batch concurrency only for query types that fan out upstream."""
    if not queries:
        return requested_parallelism
    if any(_determine_trip_type(query.segments) == TripType.ROUND_TRIP for _, query in queries):
        return min(requested_parallelism, _ROUND_TRIP_BATCH_PARALLELISM_CAP)
    return requested_parallelism


def _resolve_batch_result(future: Any, original_index: int) -> dict[str, Any]:
    """Resolve one batch future into the standardized result payload."""
    try:
        return {"index": original_index, **future.result()}
    except Exception as exc:  # pragma: no cover
        return {
            "index": original_index,
            "success": False,
            "error": f"Batch item execution failed: {exc}",
            "flights": [],
        }


def _search_flights_client() -> Any:
    """Instantiate the current SearchFlights class from the public facade."""
    server_module = sys.modules.get("fli.mcp.server")
    if server_module is None:
        return DefaultSearchFlights(request_params=google_request_params())
    return server_module.SearchFlights(request_params=google_request_params())


def _resolve_batch_executor() -> Any:
    """Resolve the current batch executor from the public facade."""
    server_module = sys.modules.get("fli.mcp.server")
    if server_module is None:
        return _execute_flight_batch
    return server_module._execute_flight_batch
