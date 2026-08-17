"""Errors raised by the domain and platform layers."""

from __future__ import annotations


class PlatformError(RuntimeError):
    """The machine cannot be inspected in a way the app can trust."""
