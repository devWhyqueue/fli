"""Flight search implementation.

This module provides the core flight search functionality, interfacing directly
with Google Flights' API to find available flights and their details.
"""

import json
from copy import deepcopy
from datetime import datetime
from typing import cast

from fli.models import (
    Airline,
    Airport,
    FlightResult,
    FlightSearchFilters,
    NativeMultiCityResult,
)
from fli.models.google_flights.base import TripType
from fli.search.client import get_client
from fli.search.internal.flight_parsing import (
    parse_airline,
    parse_airport,
    parse_datetime,
    parse_flights_data,
    parse_price,
)
from fli.search.native_multi_city import build_multi_city_result, select_cheapest_option
from fli.search.selection import parse_selection_token


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

    def search_multi_city_native(
        self, filters: FlightSearchFilters, top_n: int = 5
    ) -> NativeMultiCityResult | None:
        """Run Google Flights' native multi-city workflow with cheapest auto-picks."""
        if filters.trip_type != TripType.MULTI_CITY:
            raise ValueError("Native multi-city workflow requires a multi-city itinerary")
        selected_segments: list[FlightResult] = []
        step_trace = []
        for step_index, _segment in enumerate(filters.flight_segments):
            if not self._append_native_multi_city_step(
                filters, step_index, selected_segments, step_trace
            ):
                return None
        return build_multi_city_result(selected_segments, step_trace)

    def _append_native_multi_city_step(
        self,
        filters: FlightSearchFilters,
        step_index: int,
        selected_segments: list[FlightResult],
        step_trace: list[object],
    ) -> bool:
        """Append one selected step in the native multi-city flow."""
        step_options = self._search_one_way(filters)
        if not step_options:
            return False
        chosen, trace_step = select_cheapest_option(step_index, step_options)
        selected_segments.append(chosen)
        filters.flight_segments[step_index].selected_flight = chosen
        step_trace.append(trace_step)
        return True

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

    @staticmethod
    def _parse_price(data: list) -> float:
        """Extract price from raw flight data.

        Args:
            data: Raw flight data from the API response

        Returns:
            Flight price, or 0.0 if price data is unavailable

        """
        return parse_price(data)

    @staticmethod
    def _parse_selection_token(data: list) -> str | None:
        """Preserve the historical token parsing entrypoint on SearchFlights."""
        return parse_selection_token(data)

    @staticmethod
    def _parse_datetime(date_arr: list[int], time_arr: list[int]) -> datetime:
        """Convert date and time arrays to datetime.

        Args:
            date_arr: List of integers [year, month, day]
            time_arr: List of integers [hour, minute]

        Returns:
            Parsed datetime object

        Raises:
            ValueError: If arrays contain only None values

        """
        return parse_datetime(date_arr, time_arr)

    @staticmethod
    def _parse_airline(airline_code: str) -> Airline:
        """Convert airline code to Airline enum.

        Args:
            airline_code: Raw airline code from API

        Returns:
            Corresponding Airline enum value

        """
        return parse_airline(airline_code)

    @staticmethod
    def _parse_airport(airport_code: str) -> Airport:
        """Convert airport code to Airport enum.

        Args:
            airport_code: Raw airport code from API

        Returns:
            Corresponding Airport enum value

        """
        return parse_airport(airport_code)


_VULTURE_REFERENCES = (SearchFlights._parse_selection_token,)
