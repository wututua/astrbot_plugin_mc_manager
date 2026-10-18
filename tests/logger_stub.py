"""In-memory logger doubles for isolated SDK tests; never used by the plugin."""

from unittest.mock import Mock


def logger_stub(name="mc-test-logger"):
    instance = Mock(
        name=name,
        spec=("debug", "info", "warning", "error", "exception", "critical", "getEffectiveLevel"),
    )
    instance.getEffectiveLevel.return_value = 20
    return instance
