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


def build_filters(departure_date: str) -> FlightSearchFilters:
    """Create a single reusable one-way filter payload for benchmarking."""
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


def run_one(search: SearchFlights, departure_date: str) -> bool:
    """Execute one search request and return whether results were found."""
    flights = search.search(build_filters(departure_date))
    return bool(flights)


def main() -> None:
    """Run benchmark and print a compact result dictionary."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--parallelism", type=int, default=1)
    parser.add_argument("--departure-date", type=str, default="2026-06-11")
    args = parser.parse_args()

    search = SearchFlights()
    start = time.perf_counter()
    successes = 0

    if args.parallelism <= 1:
        for _ in range(args.count):
            successes += int(run_one(search, args.departure_date))
    else:
        with ThreadPoolExecutor(max_workers=args.parallelism) as executor:
            futures = [
                executor.submit(run_one, search, args.departure_date) for _ in range(args.count)
            ]
            for future in as_completed(futures):
                successes += int(future.result())

    elapsed = time.perf_counter() - start
    print(
        {
            "count": args.count,
            "parallelism": args.parallelism,
            "elapsed_seconds": round(elapsed, 2),
            "successes": successes,
        }
    )


if __name__ == "__main__":
    main()
