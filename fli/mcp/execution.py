"""Execution and serialization helpers for MCP flight tools."""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from fli.core.parsers import ParseError
from fli.models import NativeMultiCityResult, TripType
from fli.search import SearchFlights as DefaultSearchFlights

from .app import CONFIG
from .params import (
    DateSearchParams,
    FlightSearchParams,
    _build_date_search_queries,
    _build_flight_filters,
    _determine_trip_type,
)


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
    return payload


def _serialize_native_multi_city_as_flight(result: NativeMultiCityResult) -> dict[str, Any]:
    """Serialize a native multi-city result into the standard flight payload shape."""
    payload = _serialize_flight_result(result.completed_itinerary, is_round_trip=False)
    payload["price"] = result.final_price
    if result.segment_prices is not None:
        payload["segment_prices"] = result.segment_prices
    return payload


def _execute_flight_search(params: FlightSearchParams) -> dict[str, Any]:
    """Execute a flight search and return formatted results."""
    try:
        filters, trip_type = _build_flight_filters(params)
        flights = _collect_flights(_search_flights_client(), filters, trip_type)
        return _success_payload(flights, trip_type.name)
    except ParseError as exc:
        return {"success": False, "error": str(exc), "flights": []}
    except Exception as exc:
        return _flight_error_payload(exc)


def _collect_flights(search_client: Any, filters: Any, trip_type: TripType) -> list[dict[str, Any]]:
    """Run the requested flight search and normalize the serialized results."""
    if trip_type == TripType.MULTI_CITY:
        result = search_client.search_multi_city_native(filters)
        return [] if result is None else [_serialize_native_multi_city_as_flight(result)]
    raw_flights = search_client.search(filters)
    if not raw_flights:
        return []
    is_round_trip = trip_type == TripType.ROUND_TRIP
    return [_serialize_flight_result(flight, is_round_trip) for flight in raw_flights]


def _success_payload(flights: list[dict[str, Any]], trip_type_name: str) -> dict[str, Any]:
    """Build a successful flight-search response payload."""
    limited_flights = flights[: CONFIG.max_results] if CONFIG.max_results else flights
    return {
        "success": True,
        "flights": limited_flights,
        "count": len(limited_flights),
        "trip_type": trip_type_name,
    }


def _flight_error_payload(exc: Exception) -> dict[str, Any]:
    """Build a normalized flight-search error payload."""
    error_msg = str(exc)
    if "validation error" in error_msg.lower():
        error_msg = "Invalid parameter value"
    else:
        error_msg = f"Search failed: {error_msg}"
    return {"success": False, "error": error_msg, "flights": []}


def _execute_date_search(params: DateSearchParams) -> dict[str, Any]:
    """Execute a date search and return formatted results."""
    try:
        queries = _build_date_search_queries(params)
        trip_type = _determine_trip_type(params.segments)
        executed = _resolve_batch_executor()(queries, min(8, len(queries)))
        if executed["failed"]:
            return _date_failure_payload(executed["results"])
        return _date_success_payload(executed["results"], queries, params, trip_type)
    except ParseError as exc:
        return {"success": False, "error": str(exc), "dates": []}
    except ValueError as exc:
        return {"success": False, "error": str(exc), "dates": []}
    except Exception as exc:
        return {"success": False, "error": f"Search failed: {exc}", "dates": []}


def _date_failure_payload(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a normalized date-search failure payload."""
    error_result = next((item for item in results if not item.get("success", False)), None)
    error = (
        error_result.get("error", "Date search failed") if error_result else "Date search failed"
    )
    return {"success": False, "error": error, "dates": []}


def _date_success_payload(
    results: list[dict[str, Any]],
    queries: list[tuple[int, FlightSearchParams]],
    params: DateSearchParams,
    trip_type: TripType,
) -> dict[str, Any]:
    """Build a successful date-search response payload."""
    date_results = [_build_date_result(result, queries, trip_type) for result in results]
    date_results = [result for result in date_results if result is not None]
    if not date_results:
        return _empty_date_payload(params, trip_type.name)
    if params.sort_by_price:
        date_results.sort(key=lambda item: item["price"])
    limited_results = date_results[: CONFIG.max_results] if CONFIG.max_results else date_results
    return {
        "success": True,
        "dates": limited_results,
        "count": len(limited_results),
        "trip_type": trip_type.name,
        "date_range": f"{params.start_date} to {params.end_date}",
    }


def _build_date_result(
    result: dict[str, Any],
    queries: list[tuple[int, FlightSearchParams]],
    trip_type: TripType,
) -> dict[str, Any] | None:
    """Build one summarized date-search result row."""
    flights = result["flights"]
    if not flights:
        return None
    segment_dates = [segment.date for segment in queries[result["index"]][1].segments]
    return {
        "date": segment_dates[0],
        "segment_dates": segment_dates,
        "price": min(flight["price"] for flight in flights),
        "currency": CONFIG.default_currency,
        "return_date": segment_dates[1] if trip_type == TripType.ROUND_TRIP else None,
    }


def _empty_date_payload(params: DateSearchParams, trip_type_name: str) -> dict[str, Any]:
    """Build the empty-success date-search payload."""
    return {
        "success": True,
        "dates": [],
        "count": 0,
        "trip_type": trip_type_name,
        "date_range": f"{params.start_date} to {params.end_date}",
    }


def _execute_flight_batch(
    queries: list[tuple[int, FlightSearchParams]],
    parallelism: int,
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
            results[position] = _resolve_batch_result(future, original_index)
    final_results = [item for item in results if item is not None]
    failures = sum(1 for item in final_results if not item.get("success", False))
    return {
        "success": failures == 0,
        "results": final_results,
        "count": len(final_results),
        "failed": failures,
        "parallelism": parallelism,
    }


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
        return DefaultSearchFlights()
    return server_module.SearchFlights()


def _resolve_batch_executor() -> Any:
    """Resolve the current batch executor from the public facade."""
    server_module = sys.modules.get("fli.mcp.server")
    if server_module is None:
        return _execute_flight_batch
    return server_module._execute_flight_batch
