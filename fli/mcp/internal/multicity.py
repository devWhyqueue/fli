"""Transparent multi-city decomposition into parallel one-way leg searches.

Google's multi-city endpoint is an order of magnitude slower than one-way.
Searching each leg independently and combining via Cartesian product gives
equivalent results in a fraction of the time.
"""

import itertools
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from fli.models import TripType

from ..app import CONFIG
from ..internal.execution_payloads import success_payload
from ..params import FlightSearchParams, _build_flight_filters

_MULTICITY_TOP_K = 5


def execute_multicity_decomposed(
    params: FlightSearchParams,
    trip_type: TripType,
    *,
    search_client_factory: Any,
    collect_flights_fn: Any,
) -> dict[str, Any]:
    """Decompose a multi-city search into parallel one-way legs and recombine."""
    segment_params_list = _build_one_way_params_per_segment(params)
    client = search_client_factory()
    segment_results = _search_segments_parallel(client, segment_params_list, collect_flights_fn)
    if any(not results for results in segment_results):
        return success_payload([], trip_type.name, CONFIG.max_results)
    combined = _combine_segment_results(segment_results)
    return success_payload(combined, trip_type.name, CONFIG.max_results)


def _build_one_way_params_per_segment(
    params: FlightSearchParams,
) -> list[FlightSearchParams]:
    """Create a one-way FlightSearchParams for each segment of a multi-city query."""
    return [
        FlightSearchParams(
            segments=[segment],
            departure_window=params.departure_window,
            departure_time_window=params.departure_time_window,
            arrival_time_window=params.arrival_time_window,
            airlines=params.airlines,
            cabin_class=params.cabin_class,
            max_stops=params.max_stops,
            sort_by=params.sort_by,
            passengers=params.passengers,
            num_cabin_luggage=params.num_cabin_luggage,
            duration=None,
            max_layover_time=params.max_layover_time,
        )
        for segment in params.segments
    ]


def _search_segments_parallel(
    client: Any,
    segment_params_list: list[FlightSearchParams],
    collect_flights_fn: Any,
) -> list[list[dict[str, Any]]]:
    """Search all segments in parallel, returning top-K results per segment."""
    results: list[list[dict[str, Any]]] = [[] for _ in segment_params_list]
    with ThreadPoolExecutor(max_workers=len(segment_params_list)) as executor:
        futures = {
            executor.submit(_search_one_segment, client, p, collect_flights_fn): idx
            for idx, p in enumerate(segment_params_list)
        }
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()[:_MULTICITY_TOP_K]
    return results


def _search_one_segment(
    client: Any,
    params: FlightSearchParams,
    collect_flights_fn: Any,
) -> list[dict[str, Any]]:
    """Execute a single one-way segment search and return serialized results."""
    filters, one_way_type = _build_flight_filters(params)
    return collect_flights_fn(client, filters, one_way_type)


def _combine_segment_results(
    segment_results: list[list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Cartesian-product per-segment results into combined itineraries, sorted by price."""
    combined: list[dict[str, Any]] = []
    for combo in itertools.product(*segment_results):
        total_price = sum(seg["price"] for seg in combo)
        all_legs: list[dict[str, Any]] = []
        segment_prices: list[float] = []
        for seg in combo:
            all_legs.extend(seg["legs"])
            segment_prices.append(seg["price"])
        combined.append(
            {
                "price": total_price,
                "currency": CONFIG.default_currency,
                "segment_prices": segment_prices,
                "legs": all_legs,
            }
        )
    combined.sort(key=lambda x: x["price"])
    return combined
