from __future__ import annotations


class EggieError(Exception):
    """One plain sentence for the user; `main` prints it and exits 1."""


class ApiError(EggieError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class JobFailed(EggieError):
    def __init__(self, message: str, result: dict | None):
        super().__init__(message)
        self.result = result or {}
