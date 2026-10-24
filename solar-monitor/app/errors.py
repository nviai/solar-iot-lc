"""Error type shared across the client and the routers."""

from __future__ import annotations


class SolaxError(RuntimeError):
    """Raised when the SolaX API rejects a request or cannot be reached."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
