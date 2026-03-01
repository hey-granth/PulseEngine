"""
Pure scoring functions — no side effects, no I/O.
"""


def compute_score(likes: int, comments: int, shares: int, age_hours: float) -> float:
    """
    Compute trending score.

    weighted = likes*3 + comments*5 + shares*8
    score = weighted / (age_hours ** 1.5)
    """
    weighted = likes * 3 + comments * 5 + shares * 8
    age_hours = max(1.0, age_hours)
    return weighted / (age_hours**1.5)


def apply_fraud_penalty(
    score: float,
    event_count: int,
    window_seconds: float,
    threshold: int = 50,
    extreme_threshold: int = 200,
) -> tuple[float, bool]:
    """
    Apply velocity-based fraud penalty.

    Returns (adjusted_score, should_flag).

    If event_count within window_seconds exceeds threshold → multiplier 0.5.
    If extreme → flag the post.
    """
    if window_seconds <= 0:
        window_seconds = 1.0

    velocity = event_count / window_seconds

    # Thresholds are per-minute rates
    rate_per_minute = velocity * 60

    should_flag = False

    if rate_per_minute >= extreme_threshold:
        score *= 0.5
        should_flag = True
    elif rate_per_minute >= threshold:
        score *= 0.5

    return score, should_flag
