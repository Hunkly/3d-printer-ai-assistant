"""Recommendation engine abstraction (Phase 3A).

The engine is read-only: ``recommend`` never writes slicer profiles, never
modifies the input model, and never touches the printer. Results are
suggestions only.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from print_engineer.core.recommendation import RecommendationRequest, RecommendationSet


class Recommender(ABC):
    """Produces validated print recommendations for a model."""

    @abstractmethod
    def recommend(self, request: RecommendationRequest) -> RecommendationSet:
        """Analyze the model, gather current settings, and recommend."""
