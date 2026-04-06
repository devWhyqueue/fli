"""Flight search implementation.

This module provides the core flight search functionality, interfacing directly
with Google Flights' API to find available flights and their details.
"""

import json
from copy import deepcopy
from typing import Any, cast

from curl_cffi.requests import Response

from fli.models import (
    FlightResult,
    FlightSearchFilters,
)
from fli.models.google_flights.base import TripType
from fli.search.client import get_client
from fli.search.internal.flight_parsing import (
    parse_flights_data,
)


def _filter_by_cabin_bag(
    flights: list[FlightResult],
    num_cabin_luggage: int | None,
) -> list[FlightResult]:
    """Drop flights whose fare excludes cabin bags when luggage was requested.

    Google Flights' shopping API ignores the cabin-luggage parameter and
    always returns base fares.  The response *does* flag whether each fare
    includes a cabin bag (``cabin_bag_included``).  When the caller asked
    for >=1 cabin bag we keep only fares that already cover it, so the
    returned prices are accurate.  If that would eliminate every result we
    fall back to the unfiltered list to avoid empty responses.
    """
    if not num_cabin_luggage or num_cabin_luggage < 1:
        return flights
    with_bag = [f for f in flights if f.cabin_bag_included is not False]
    return with_bag if with_bag else flights


class _ClientProxy:
    """Delegate request calls to the current thread's shared HTTP client."""

    def get(self, *args: Any, **kwargs: Any) -> Response:
        """Forward GET requests to the active thread-local client."""
        return get_client().get(*args, **kwargs)

    def post(self, *args: Any, **kwargs: Any) -> Response:
        """Forward POST requests to the active thread-local client."""
        return get_client().post(*args, **kwargs)


class SearchFlights:
    """Flight search implementation using Google Flights' API.

    This class handles searching for specific flights with detailed filters,
    parsing the results into structured data models.
    """

    BASE_URL = "https://www.google.com/_/FlightsFrontendUi/data/travel.frontend.flights.FlightsFrontendService/GetShoppingResults"
    DEFAULT_HEADERS = {
        "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
    }

    def __init__(self, *, request_params: dict[str, str] | None = None):
        """Initialize the search client for flight searches."""
        self.client = _ClientProxy()
        self.request_params = request_params or {}

    def search(
        self, filters: FlightSearchFilters, top_n: int = 5
    ) -> list[FlightResult | tuple[FlightResult, FlightResult]] | None:
        """Search for flights using the given filters."""
        try:
            flights = self._search_one_way(filters)
            if (
                flights is None
                or filters.trip_type != TripType.ROUND_TRIP
                or filters.flight_segments[0].selected_flight is not None
            ):
                return cast(list[FlightResult | tuple[FlightResult, FlightResult]] | None, flights)
            return cast(
                list[FlightResult | tuple[FlightResult, FlightResult]],
                self._search_round_trip_returns(filters, flights, top_n=top_n),
            )
        except Exception as e:
            raise Exception(f"Search failed: {str(e)}") from e

    def _search_round_trip_returns(
        self,
        filters: FlightSearchFilters,
        outbound_flights: list[FlightResult],
        top_n: int = 5,
    ) -> list[tuple[FlightResult, FlightResult]]:
        """Fetch return options for each outbound candidate in a round-trip search."""
        flight_pairs = []
        for selected_flight in outbound_flights[:top_n]:
            selected_flight_filters = deepcopy(filters)
            selected_flight_filters.flight_segments[0].selected_flight = selected_flight
            return_flights = self.search(selected_flight_filters, top_n=top_n)
            if return_flights is not None:
                flight_pairs.extend(
                    (selected_flight, return_flight) for return_flight in return_flights
                )
        return flight_pairs

    def _search_one_way(self, filters: FlightSearchFilters) -> list[FlightResult] | None:
        """Execute a single shopping request and parse the returned itineraries."""
        encoded_filters = filters.encode()
        response = self.client.post(
            url=self.BASE_URL,
            data=f"f.req={encoded_filters}",
            params=self.request_params or None,
            impersonate="chrome",
            allow_redirects=True,
        )
        response.raise_for_status()

        parsed = json.loads(response.text.lstrip(")]}'"))[0][2]
        if not parsed:
            return None

        encoded_filters = json.loads(parsed)
        flights_data = [
            item
            for i in [2, 3]
            if isinstance(encoded_filters[i], list)
            for item in encoded_filters[i][0]
        ]
        results = [self._parse_flights_data(flight) for flight in flights_data]
        return _filter_by_cabin_bag(results, filters.passenger_info.num_cabin_luggage)

    @staticmethod
    def _parse_flights_data(data: list) -> FlightResult:
        """Parse raw flight data into a structured FlightResult.

        Args:
            data: Raw flight data from the API response

        Returns:
            Structured FlightResult object with all flight details

        """
        return parse_flights_data(data)
