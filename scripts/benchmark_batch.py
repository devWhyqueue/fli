"""Benchmark batch flight-search throughput.

Usage:
    uv run python scripts/benchmark_batch.py --count 100 --parallelism 1
"""

from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from fli.models import Airport, FlightSearchFilters, FlightSegment, PassengerInfo, TripType
from fli.search import SearchFlights

logger = logging.getLogger(__name__)


def build_one_way_filters(departure_date: str) -> FlightSearchFilters:
    """Create a reusable one-way filter payload for benchmarking."""
    return FlightSearchFilters(
        trip_type=TripType.ONE_WAY,
        passenger_info=PassengerInfo(adults=1),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.BER, 0]],
                arrival_airport=[[Airport.LHR, 0]],
                travel_date=departure_date,
            )
        ],
    )


def build_multi_city_filters(departure_date: str) -> FlightSearchFilters:
    """Create a reusable multi-city filter payload for benchmarking."""
    return FlightSearchFilters(
        trip_type=TripType.MULTI_CITY,
        passenger_info=PassengerInfo(adults=1, num_cabin_luggage=1),
        flight_segments=[
            FlightSegment(
                departure_airport=[[Airport.BER, 0]],
                arrival_airport=[[Airport.FCO, 0]],
                travel_date=departure_date,
            ),
            FlightSegment(
                departure_airport=[[Airport.FCO, 0]],
                arrival_airport=[[Airport.VCE, 0]],
                travel_date="2026-05-12",
            ),
            FlightSegment(
                departure_airport=[[Airport.VCE, 0]],
                arrival_airport=[[Airport.BER, 0]],
                travel_date="2026-05-18",
            ),
        ],
    )


def run_one(search: SearchFlights, filters: FlightSearchFilters) -> bool:
    """Execute one search request and return whether results were found."""
    flights = search.search(filters)
    return bool(flights)


def main() -> None:
    """Run benchmark and print a compact result dictionary."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--parallelism", type=int, default=1)
    parser.add_argument("--departure-date", type=str, default="2026-06-11")
    parser.add_argument(
        "--mode",
        choices=("one-way", "multi-city"),
        default="one-way",
    )
    args = parser.parse_args()

    search = SearchFlights()
    filters = (
        build_multi_city_filters(args.departure_date)
        if args.mode == "multi-city"
        else build_one_way_filters(args.departure_date)
    )
    start = time.perf_counter()
    successes = 0

    if args.parallelism <= 1:
        for _ in range(args.count):
            successes += int(run_one(search, filters))
    else:
        with ThreadPoolExecutor(max_workers=args.parallelism) as executor:
            futures = [executor.submit(run_one, search, filters) for _ in range(args.count)]
            for future in as_completed(futures):
                successes += int(future.result())

    elapsed = time.perf_counter() - start
    print(
        {
            "count": args.count,
            "parallelism": args.parallelism,
            "mode": args.mode,
            "elapsed_seconds": round(elapsed, 2),
            "successes": successes,
        }
    )


if __name__ == "__main__":
    main()
