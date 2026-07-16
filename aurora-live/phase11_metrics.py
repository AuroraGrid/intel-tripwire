from __future__ import annotations

import math


def validate_probability(value):
    value = float(value)
    if value < 0.0 or value > 1.0:
        raise ValueError("probability must be between 0 and 1")
    return value


def brier_score(probability, outcome):
    probability = validate_probability(probability)
    observed = 1.0 if bool(outcome) else 0.0
    return (probability - observed) ** 2


def logarithmic_score(probability, outcome, epsilon=1e-15):
    probability = validate_probability(probability)
    probability = min(1.0 - epsilon, max(epsilon, probability))
    observed = 1.0 if bool(outcome) else 0.0
    return -(observed * math.log(probability) + (1.0 - observed) * math.log(1.0 - probability))


def calibration_error(records, bins=10):
    rows = list(records)
    if not rows:
        return 0.0
    buckets = [[] for _ in range(max(1, int(bins)))]
    for row in rows:
        probability = validate_probability(row["probability"])
        observed = 1.0 if bool(row["outcome"]) else 0.0
        index = min(len(buckets) - 1, int(probability * len(buckets)))
        buckets[index].append((probability, observed))
    total = len(rows)
    error = 0.0
    for bucket in buckets:
        if not bucket:
            continue
        forecast = sum(value[0] for value in bucket) / len(bucket)
        observed = sum(value[1] for value in bucket) / len(bucket)
        error += (len(bucket) / total) * abs(forecast - observed)
    return error


def scorecard(records, bins=10):
    rows = list(records)
    if not rows:
        return {"count": 0, "brier": None, "log_loss": None, "calibration_error": None}
    return {
        "count": len(rows),
        "brier": sum(brier_score(row["probability"], row["outcome"]) for row in rows) / len(rows),
        "log_loss": sum(logarithmic_score(row["probability"], row["outcome"]) for row in rows) / len(rows),
        "calibration_error": calibration_error(rows, bins),
    }
