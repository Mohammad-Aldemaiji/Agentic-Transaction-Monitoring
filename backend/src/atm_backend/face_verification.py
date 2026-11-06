"""Face verification as the staff workspace's second sign-in factor.

The browser runs face-api.js locally and sends only 128-number face descriptors.
The server stores the enrolled descriptors and makes every match decision, so a
browser cannot declare itself verified. Descriptors are biometric data: they are
kept only in the isolated local database and removed when a user is deleted or
an administrator resets the enrollment.

Limitations: there is no liveness detection, so a good photo or video of the
enrolled person can pass. This is a research prototype factor, not bank-grade
biometric authentication.
"""

import math
from typing import Annotated

from pydantic import Field, StrictBool, field_validator

from atm_backend import contracts as dto

DESCRIPTOR_SIZE = 128
# Euclidean distance below which two descriptors are treated as the same person.
# face-api.js commonly uses 0.6; 0.5 is stricter and matches the FaceKey prototype.
MATCH_THRESHOLD = 0.5
VERIFY_SAMPLES = 3  # live samples, each of which must match, to sign in
ENROLL_SAMPLES = 5  # samples stored as the user's templates
MAX_SAMPLES = 5
MAX_FAILURES = 5  # consecutive failed scans before a temporary lock
LOCK_SECONDS = 15 * 60
# Two descriptors from different camera frames are never bit-identical. A near-zero
# distance means a replayed vector, not a live scan.
REPLAY_DISTANCE = 1e-6

Component = Annotated[float, Field(ge=-1.5, le=1.5, allow_inf_nan=False)]
Descriptor = Annotated[
    list[Component], Field(min_length=DESCRIPTOR_SIZE, max_length=DESCRIPTOR_SIZE)
]


class FaceInput(dto.Input):
    samples: list[Descriptor] = Field(min_length=VERIFY_SAMPLES, max_length=MAX_SAMPLES)
    enroll: StrictBool = False

    @field_validator("samples")
    @classmethod
    def usable(cls, samples: list[list[float]]) -> list[list[float]]:
        for sample in samples:
            if math.sqrt(sum(x * x for x in sample)) < 0.1:
                raise ValueError("Empty face descriptor")
        return samples


def distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def distinct(samples: list[list[float]]) -> bool:
    """Every sample must come from a different frame."""
    return all(
        distance(a, b) > REPLAY_DISTANCE for i, a in enumerate(samples) for b in samples[i + 1 :]
    )


def consistent(samples: list[list[float]]) -> bool:
    """Enrollment samples must all show the same person."""
    return all(
        distance(a, b) < MATCH_THRESHOLD for i, a in enumerate(samples) for b in samples[i + 1 :]
    )


def match(templates: list[list[float]], samples: list[list[float]]) -> float | None:
    """Average distance when every live sample matches an enrolled template, else None."""
    best = []
    for sample in samples:
        nearest = min(distance(template, sample) for template in templates)
        if nearest <= REPLAY_DISTANCE or nearest >= MATCH_THRESHOLD:
            return None
        best.append(nearest)
    return sum(best) / len(best)
