"""
Unit tests for the scoring module — pure functions, no I/O.
"""

from django.test import SimpleTestCase

from ranking.scoring import apply_fraud_penalty, compute_score


class TestComputeScore(SimpleTestCase):

    def test_basic_score(self):
        # weighted = 10*3 + 5*5 + 2*8 = 71; 71 / (2**1.5) ≈ 25.10
        score = compute_score(likes=10, comments=5, shares=2, age_hours=2.0)
        self.assertAlmostEqual(score, 25.10, places=2)

    def test_minimum_age_clamped_to_one(self):
        self.assertEqual(
            compute_score(likes=3, comments=0, shares=0, age_hours=0.0),
            compute_score(likes=3, comments=0, shares=0, age_hours=1.0),
        )

    def test_negative_age_clamped_to_one(self):
        score = compute_score(likes=3, comments=0, shares=0, age_hours=-5.0)
        self.assertEqual(score, 9.0)  # 3*3 / 1.0

    def test_zero_engagement(self):
        self.assertEqual(compute_score(likes=0, comments=0, shares=0, age_hours=10.0), 0.0)

    def test_shares_weighted_highest(self):
        s_likes    = compute_score(likes=1, comments=0, shares=0, age_hours=1.0)
        s_comments = compute_score(likes=0, comments=1, shares=0, age_hours=1.0)
        s_shares   = compute_score(likes=0, comments=0, shares=1, age_hours=1.0)
        self.assertGreater(s_shares, s_comments)
        self.assertGreater(s_comments, s_likes)

    def test_older_posts_score_lower(self):
        self.assertGreater(
            compute_score(likes=10, comments=5, shares=2, age_hours=1.0),
            compute_score(likes=10, comments=5, shares=2, age_hours=24.0),
        )

    def test_score_deterministic(self):
        self.assertEqual(
            compute_score(likes=7, comments=3, shares=1, age_hours=5.0),
            compute_score(likes=7, comments=3, shares=1, age_hours=5.0),
        )

    def test_large_values(self):
        # weighted = 30000 + 25000 + 8000 = 63000; age=1 → 63000
        self.assertEqual(
            compute_score(likes=10000, comments=5000, shares=1000, age_hours=1.0),
            63000.0,
        )


class TestApplyFraudPenalty(SimpleTestCase):

    def test_normal_velocity_no_penalty(self):
        score, flagged = apply_fraud_penalty(100.0, event_count=10, window_seconds=60.0)
        self.assertEqual(score, 100.0)
        self.assertFalse(flagged)

    def test_high_velocity_penalty(self):
        score, flagged = apply_fraud_penalty(100.0, event_count=60, window_seconds=60.0)
        self.assertEqual(score, 50.0)
        self.assertFalse(flagged)

    def test_extreme_velocity_flag(self):
        score, flagged = apply_fraud_penalty(100.0, event_count=250, window_seconds=60.0)
        self.assertEqual(score, 50.0)
        self.assertTrue(flagged)

    def test_zero_window_handled(self):
        score, flagged = apply_fraud_penalty(100.0, event_count=5, window_seconds=0.0)
        self.assertEqual(score, 50.0)
        self.assertTrue(flagged)

    def test_custom_thresholds(self):
        score, flagged = apply_fraud_penalty(
            100.0, event_count=15, window_seconds=60.0, threshold=10, extreme_threshold=20
        )
        self.assertEqual(score, 50.0)
        self.assertFalse(flagged)

    def test_just_below_threshold(self):
        score, flagged = apply_fraud_penalty(100.0, event_count=49, window_seconds=60.0)
        self.assertEqual(score, 100.0)
        self.assertFalse(flagged)

    def test_exactly_at_threshold(self):
        score, flagged = apply_fraud_penalty(100.0, event_count=50, window_seconds=60.0)
        self.assertEqual(score, 50.0)
        self.assertFalse(flagged)
