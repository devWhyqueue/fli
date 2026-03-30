import pytest


def _get_flag(config: pytest.Config, name: str) -> bool:
    """Return a custom boolean pytest option if it exists, else False."""
    try:
        return bool(config.getoption(name))
    except ValueError:
        return False


def pytest_addoption(parser) -> None:
    """Add options to pytest."""
    parser.addoption("--fuzz", action="store_true", help="Run fuzz tests")
    parser.addoption("--live", action="store_true", help="Run live network tests")
    parser.addoption("--mcp", action="store_true", help="Run MCP tests")
    parser.addoption("--all", action="store_true", help="Run all tests")


def pytest_runtest_setup(item) -> None:
    """Skip opt-in test categories unless explicitly enabled."""
    fuzz_marker = item.get_closest_marker("fuzz")
    if fuzz_marker is not None:
        if not _get_flag(item.config, "--fuzz") and not _get_flag(item.config, "--all"):
            pytest.skip("need --fuzz or --all option to run this test")

    live_marker = item.get_closest_marker("live")
    if live_marker is not None:
        if not _get_flag(item.config, "--live") and not _get_flag(item.config, "--all"):
            pytest.skip("need --live or --all option to run this test")


def pytest_collection_modifyitems(config, items) -> None:
    """Modify collection based on custom flags."""
    if _get_flag(config, "--mcp"):
        # Only keep MCP tests when --mcp flag is used
        items[:] = [item for item in items if "mcp" in item.nodeid]
    elif _get_flag(config, "--fuzz"):
        # Only keep fuzz tests when --fuzz flag is used (and not --all)
        if not _get_flag(config, "--all"):
            items[:] = [item for item in items if item.get_closest_marker("fuzz")]
    elif not _get_flag(config, "--all"):
        # Remove fuzz tests from normal runs
        items[:] = [item for item in items if not item.get_closest_marker("fuzz")]
