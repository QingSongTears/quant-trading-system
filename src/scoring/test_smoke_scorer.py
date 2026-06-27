"""Smoke test - well-formed scorer to verify good commits pass."""
from src.scoring.base import BaseScorer


class TestSmokeScorer(BaseScorer):
    """Test scorer for smoke test only."""
    zh_name = "测试评分器"
    en_name = "Test Smoke Scorer"
    description = "Smoke test scorer to verify pre-commit hook works"

    def score(self, data):
        return 0.5
