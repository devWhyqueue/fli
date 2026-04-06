"""Benchmark internal exact-date execution strategies.

Usage:
    uv run python scripts/benchmark_batch.py --count 100 --parallelism 8 --strategy compare
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta

from fli.mcp.execution import _execute_flight_batch, _execute_flight_search
from fli.mcp.server import FlightSearchParams, FlightSearchSegmentParams


def build_queries(count: int, departure_date: str, mode: str) -> list[FlightSearchParams]:
    """Create concrete exact-date query payloads for benchmarking."""
    if mode == "multi-city":
        anchor = datetime.strptime(departure_date, "%Y-%m-%d").date()
        onward_date = (anchor + timedelta(days=4)).isoformat()
        return_date = (anchor + timedelta(days=10)).isoformat()
        return [
            FlightSearchParams(
                segments=[
                    FlightSearchSegmentParams(
                        origin="BER",
                        destination="FCO",
                        date=departure_date,
                    ),
                    FlightSearchSegmentParams(
                        origin="FCO",
                        destination="VCE",
                        date=onward_date,
                    ),
                    FlightSearchSegmentParams(
                        origin="VCE",
                        destination="BER",
                        date=return_date,
                    ),
                ],
                num_cabin_luggage=1,
            )
            for _ in range(count)
        ]
    return [
        FlightSearchParams(
            segments=[
                FlightSearchSegmentParams(
                    origin="BER",
                    destination="LHR",
                    date=departure_date,
                )
            ]
        )
        for _ in range(count)
    ]


def run_sequential(queries: list[FlightSearchParams]) -> dict[str, float | int]:
    """Run exact-date searches one by one."""
    start = time.perf_counter()
    successes = sum(int(bool(_execute_flight_search(query).get("flights"))) for query in queries)
    elapsed = time.perf_counter() - start
    return {
        "elapsed_seconds": round(elapsed, 2),
        "successes": successes,
    }


def run_batched(
    queries: list[FlightSearchParams],
    parallelism: int,
) -> dict[str, float | int]:
    """Run exact-date searches through the internal batch executor."""
    start = time.perf_counter()
    result = _execute_flight_batch(list(enumerate(queries)), parallelism)
    elapsed = time.perf_counter() - start
    return {
        "elapsed_seconds": round(elapsed, 2),
        "successes": sum(int(bool(item.get("flights"))) for item in result["results"]),
        "effective_parallelism": result["parallelism"],
    }


def main() -> None:
    """Run benchmark and print a compact result dictionary."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--parallelism", type=int, default=8)
    parser.add_argument("--departure-date", type=str, default="2026-06-11")
    parser.add_argument("--mode", choices=("one-way", "multi-city"), default="one-way")
    parser.add_argument(
        "--strategy",
        choices=("sequential", "batched", "compare"),
        default="compare",
    )
    args = parser.parse_args()

    queries = build_queries(args.count, args.departure_date, args.mode)
    result: dict[str, object] = {
        "count": args.count,
        "parallelism": args.parallelism,
        "mode": args.mode,
        "strategy": args.strategy,
    }

    if args.strategy in {"sequential", "compare"}:
        result["sequential"] = run_sequential(queries)
    if args.strategy in {"batched", "compare"}:
        result["batched"] = run_batched(queries, args.parallelism)

    print(result)


if __name__ == "__main__":
    main()
