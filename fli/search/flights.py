"""Flight search implementation.

This module provides the core flight search functionality, interfacing directly
with Google Flights' API to find available flights and their details.
"""

import json
from copy import deepcopy
from typing import cast

from fli.models import (
    FlightResult,
    FlightSearchFilters,
)
from fli.models.google_flights.base import TripType
from fli.search.client import get_client
from fli.search.internal.flight_parsing import (
    parse_flights_data,
)


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
        self.client = get_client()
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
        return [self._parse_flights_data(flight) for flight in flights_data]

    @staticmethod
    def _parse_flights_data(data: list) -> FlightResult:
        """Parse raw flight data into a structured FlightResult.

        Args:
            data: Raw flight data from the API response

        Returns:
            Structured FlightResult object with all flight details

        """
        return parse_flights_data(data)
