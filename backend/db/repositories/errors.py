"""Errors shared by every repository implementation."""

from __future__ import annotations


class DuplicateTripError(ValueError):
    """`TripRepository.add` was called with a trip_id that is already stored."""


class EmbeddingDimensionError(ValueError):
    """A vector's length does not match the stored/expected embedding dimension."""
