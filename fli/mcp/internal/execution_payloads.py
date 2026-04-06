"""Payload builders shared by MCP execution helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypedDict

from fli.core.mcp_params import DateSearchParams, FlightSearchParams
from fli.models import TripType


class DateResultRow(TypedDict):
    """Summarized row returned by flexible date searches."""

    date: str
    segment_dates: list[str]
    price: float
    currency: str
    return_date: str | None


def flight_error_payload(exc: Exception) -> dict[str, object]:
    """Build a normalized flight-search error payload."""
    error_msg = str(exc)
    if "validation error" in error_msg.lower():
        error_msg = "Invalid parameter value"
    else:
        error_msg = f"Search failed: {error_msg}"
    return {"success": False, "error": error_msg, "flights": []}


def success_payload(
    flights: list[dict[str, object]], trip_type_name: str, max_results: int | None
) -> dict[str, object]:
    """Build a successful flight-search response payload."""
    limited_flights = flights[:max_results] if max_results else flights
    return {
        "success": True,
        "flights": limited_flights,
        "count": len(limited_flights),
        "trip_type": trip_type_name,
    }


def date_failure_payload(results: list[dict[str, object]]) -> dict[str, object]:
    """Build a normalized date-search failure payload."""
    error_result = next((item for item in results if not item.get("success", False)), None)
    error = (
        error_result.get("error", "Date search failed") if error_result else "Date search failed"
    )
    return {"success": False, "error": error, "dates": []}


def date_success_payload(
    results: list[dict[str, Any]],
    queries: Sequence[tuple[int, FlightSearchParams]],
    params: DateSearchParams,
    trip_type: TripType,
    max_results: int | None,
    currency: str,
) -> dict[str, object]:
    """Build a successful date-search response payload."""
    date_results = [build_date_result(result, queries, trip_type, currency) for result in results]
    date_results = [result for result in date_results if result is not None]
    if not date_results:
        return empty_date_payload(params, trip_type.name)
    if params.sort_by_price:
        date_results.sort(key=lambda item: item["price"])
    limited_results = date_results[:max_results] if max_results else date_results
    return {
        "success": True,
        "dates": limited_results,
        "count": len(limited_results),
        "trip_type": trip_type.name,
        "date_range": f"{params.start_date} to {params.end_date}",
    }


def build_date_result(
    result: dict[str, Any],
    queries: Sequence[tuple[int, FlightSearchParams]],
    trip_type: TripType,
    currency: str,
) -> DateResultRow | None:
    """Build one summarized date-search result row."""
    flights = result["flights"]
    if not flights:
        return None
    index = int(result["index"])
    segment_dates = [segment.date for segment in queries[index][1].segments]
    return {
        "date": segment_dates[0],
        "segment_dates": segment_dates,
        "price": min(float(flight["price"]) for flight in flights),
        "currency": currency,
        "return_date": segment_dates[1] if trip_type.name == "ROUND_TRIP" else None,
    }


def empty_date_payload(params: DateSearchParams, trip_type_name: str) -> dict[str, object]:
    """Build the empty-success date-search payload."""
    return {
        "success": True,
        "dates": [],
        "count": 0,
        "trip_type": trip_type_name,
        "date_range": f"{params.start_date} to {params.end_date}",
    }
