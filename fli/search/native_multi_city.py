"""Helpers for native multi-city Google Flights flows."""

from fli.models import FlightResult, NativeMultiCityResult, NativeMultiCityStep


def select_cheapest_option(
    step_index: int,
    step_options: list[FlightResult],
) -> tuple[FlightResult, NativeMultiCityStep]:
    """Select the cheapest option for one native multi-city step."""
    chosen = min(step_options, key=lambda option: option.price)
    return chosen, NativeMultiCityStep(
        step_index=step_index,
        selected_option_rank=0,
        displayed_price=chosen.price,
        legs=chosen.legs,
    )


def build_multi_city_result(
    selected_segments: list[FlightResult],
    step_trace: list[NativeMultiCityStep],
) -> NativeMultiCityResult:
    """Build the combined native multi-city result payload."""
    completed_itinerary = FlightResult(
        legs=[leg for segment in selected_segments for leg in segment.legs],
        price=selected_segments[-1].price,
        duration=sum(segment.duration for segment in selected_segments),
        stops=sum(segment.stops for segment in selected_segments),
    )
    return NativeMultiCityResult(
        selected_segments=selected_segments,
        completed_itinerary=completed_itinerary,
        final_price=completed_itinerary.price,
        step_trace=step_trace,
        segment_prices=None,
    )
