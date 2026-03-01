"""
Unit tests for the scoring module — pure functions, no I/O.
"""

import pytest

from ranking.scoring import apply_fraud_penalty, compute_score


class TestComputeScore:
    """Tests for compute_score."""

    def test_basic_score(self):
        # weighted = 10*3 + 5*5 + 2*8 = 30 + 25 + 16 = 71
        # age_hours = 2.0 → 71 / (2 ** 1.5) = 71 / 2.828... ≈ 25.10
        score = compute_score(likes=10, comments=5, shares=2, age_hours=2.0)
        assert round(score, 2) == 25.10

    def test_minimum_age_clamped_to_one(self):
        # age_hours < 1 should be clamped to 1
        score_zero = compute_score(likes=3, comments=0, shares=0, age_hours=0.0)
        score_one = compute_score(likes=3, comments=0, shares=0, age_hours=1.0)
        assert score_zero == score_one

    def test_negative_age_clamped_to_one(self):
        score = compute_score(likes=3, comments=0, shares=0, age_hours=-5.0)
        expected = 3 * 3 / (1.0 ** 1.5)  # = 9.0
        assert score == expected

    def test_zero_engagement(self):
        score = compute_score(likes=0, comments=0, shares=0, age_hours=10.0)
        assert score == 0.0

    def test_shares_weighted_highest(self):
        score_likes = compute_score(likes=1, comments=0, shares=0, age_hours=1.0)
        score_comments = compute_score(likes=0, comments=1, shares=0, age_hours=1.0)
        score_shares = compute_score(likes=0, comments=0, shares=1, age_hours=1.0)
        assert score_shares > score_comments > score_likes

    def test_older_posts_score_lower(self):
        score_new = compute_score(likes=10, comments=5, shares=2, age_hours=1.0)
        score_old = compute_score(likes=10, comments=5, shares=2, age_hours=24.0)
        assert score_new > score_old

    def test_score_deterministic(self):
        s1 = compute_score(likes=7, comments=3, shares=1, age_hours=5.0)
        s2 = compute_score(likes=7, comments=3, shares=1, age_hours=5.0)
        assert s1 == s2

    def test_large_values(self):
        score = compute_score(likes=10000, comments=5000, shares=1000, age_hours=1.0)
        # weighted = 30000 + 25000 + 8000 = 63000
        assert score == 63000.0


class TestApplyFraudPenalty:
    """Tests for apply_fraud_penalty."""

    def test_normal_velocity_no_penalty(self):
        # 10 events in 60 seconds = 10/min, below threshold
        score, should_flag = apply_fraud_penalty(100.0, event_count=10, window_seconds=60.0)
        assert score == 100.0
        assert should_flag is False

    def test_high_velocity_penalty(self):
        # 60 events in 60 seconds = 60/min, above threshold=50
        score, should_flag = apply_fraud_penalty(100.0, event_count=60, window_seconds=60.0)
        assert score == 50.0
        assert should_flag is False

    def test_extreme_velocity_flag(self):
        # 250 events in 60 seconds = 250/min, above extreme_threshold=200
        score, should_flag = apply_fraud_penalty(100.0, event_count=250, window_seconds=60.0)
        assert score == 50.0
        assert should_flag is True

    def test_zero_window_handled(self):
        # Should not crash with window_seconds=0
        score, should_flag = apply_fraud_penalty(100.0, event_count=5, window_seconds=0.0)
        # 5 / 1.0 * 60 = 300/min → extreme
        assert score == 50.0
        assert should_flag is True

    def test_custom_thresholds(self):
        score, should_flag = apply_fraud_penalty(
            100.0, event_count=15, window_seconds=60.0, threshold=10, extreme_threshold=20
        )
        assert score == 50.0
        assert should_flag is False

    def test_just_below_threshold(self):
        # 49 events in 60s = 49/min, just below threshold=50
        score, should_flag = apply_fraud_penalty(100.0, event_count=49, window_seconds=60.0)
        assert score == 100.0
        assert should_flag is False

    def test_exactly_at_threshold(self):
        # 50 events in 60s = 50/min, at threshold
        score, should_flag = apply_fraud_penalty(100.0, event_count=50, window_seconds=60.0)
        assert score == 50.0
        assert should_flag is False

