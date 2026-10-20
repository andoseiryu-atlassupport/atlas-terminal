"""Keep third-party network/payment diagnostics away from application output."""

import logging

PRIVATE_LIBRARIES = (
    "x402",
    "mcp",
    "httpx",
    "httpcore",
    "httpx2",
    "httpcore2",
    "eth_account",
    "web3",
)


class PrivateDiagnosticFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not any(
            record.name == name or record.name.startswith(name + ".") for name in PRIVATE_LIBRARIES
        )


def private_library_logs() -> None:
    # httpcore DEBUG can include response headers (the recovery capability).
    # x402 DEBUG can include signed authorization messages. Atlas emits safe
    # domain errors instead. A same-user host can reconfigure logging, just as
    # it can read local secrets; this is not an OS isolation boundary.
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, PrivateDiagnosticFilter) for f in handler.filters):
            handler.addFilter(PrivateDiagnosticFilter())
    for name in PRIVATE_LIBRARIES:
        logger = logging.getLogger(name)
        logger.handlers = [logging.NullHandler()]
        logger.disabled = True
        logger.propagate = False
        for child_name in list(logging.Logger.manager.loggerDict):
            if child_name.startswith(name + "."):
                child = logging.getLogger(child_name)
                child.disabled = True
                child.propagate = False
