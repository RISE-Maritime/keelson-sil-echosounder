"""What an echo sounder reports, as opposed to the water that is there.

Pure — no I/O, no wall clock, no zenoh. The middle stage of the pipeline a
software-in-the-loop sensor sits in:

    truth -> measurement model -> noise/dropout -> payload

## What it is given, and what it is not

A transducer measures one thing: the range from itself to the bottom. That is
`depth_below_transducer_m`, and it is the only truth this reads. The other two
depths a sounder reports are **derived inside the instrument** from offsets
somebody entered when it was installed:

* below the surface = below the transducer + the transducer's depth;
* below the keel = below the transducer - the keel offset (keel below the
  transducer).

So a sounder knows nothing about the tide or about her squat, and it should not
be told. A squatting hull carries its transducer down with it, and the
instrument's below-surface figure is then short of the water there by the
squat. That is a real instrument's error, and modelling it is the point.

## The derivation rule

Every random value is a pure function of `(seed, stream, instant)`, computed
from a hash — the same rule keelson-sil-gnss follows (Slipway ADR-0020). There
is no generator and no sequence state. A restored run gets exactly the same
soundings back without this process knowing a restore happened, and a second
vessel cannot perturb the first one's noise.

The instant is the truth sample's own timestamp. The simulator stamps every
sample of a tick with the same simulated time, so two runs from the same
epoch draw the same noise.

## The figures

Nominal, spec-sheet-class values: a fixed floor plus a share of the depth, as
a survey or navigation sounder's datasheet states its accuracy. They are not a
validated model of any one unit, and they say so here rather than being
mistaken for one.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

NS = 1_000_000_000

# 53 bits is the mantissa of a float64, so this maps a 64-bit draw onto the
# representable reals in [0, 1) without bunching.
_MANTISSA = 1 << 53
_SCALE = 1.0 / _MANTISSA


def _uniforms(seed: int, stream: str, instant_ns: int) -> tuple[float, float]:
    """Two independent uniforms from one hash of the coordinates."""
    digest = hashlib.blake2b(f"{seed}|{stream}|{instant_ns}".encode(), digest_size=16).digest()
    first = int.from_bytes(digest[:8], "big") >> 11
    second = int.from_bytes(digest[8:], "big") >> 11
    # (0, 1]: Box–Muller takes its log.
    return (first + 1) * _SCALE, second * _SCALE


def gaussian(seed: int, stream: str, instant_ns: int) -> float:
    """One standard normal for this exact coordinate (Box–Muller)."""
    u1, u2 = _uniforms(seed, stream, instant_ns)
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


@dataclass(frozen=True)
class Sounder:
    """One installed echo sounder: its rate, its accuracy, its range, its offsets.

    `keel_offset_m` is how far the keel sits below the transducer. None is a
    unit nobody entered a keel offset into, and such a unit reports no
    below-keel depth.
    """

    rate_hz: float = 1.0
    #: The accuracy floor, metres, one sigma.
    sigma_m: float = 0.05
    #: And the share of the depth added to it, as a datasheet's "± x % of depth".
    sigma_fraction: float = 0.005
    #: Inside this range the transducer is still ringing when the echo returns.
    min_range_m: float = 0.5
    #: Beyond this it hears no echo at all.
    max_range_m: float = 200.0
    transducer_depth_m: float = 0.0
    keel_offset_m: float | None = None

    def __post_init__(self) -> None:
        if self.rate_hz <= 0.0:
            raise ValueError(f"rate_hz must be positive, not {self.rate_hz}")
        if self.sigma_m < 0.0 or self.sigma_fraction < 0.0:
            raise ValueError("an accuracy is not negative")
        if not 0.0 <= self.min_range_m < self.max_range_m:
            raise ValueError(
                f"range {self.min_range_m}..{self.max_range_m} m is not a range a sounder has"
            )

    @property
    def period_ns(self) -> int:
        return round(NS / self.rate_hz)


def due(sounder: Sounder, instant_ns: int, last_ns: int | None) -> bool:
    """Whether a truth sample at `instant_ns` is one this sounder pings on.

    It pings once per period, on the first sample at or after each period
    boundary, so its rate is its own and not the simulator's tick rate. A
    restore, which moves time backwards, starts it pinging again at once rather
    than waiting out a period it already spent.
    """
    if last_ns is None or instant_ns < last_ns:
        return True
    return instant_ns // sounder.period_ns != last_ns // sounder.period_ns


def measure(
    sounder: Sounder, seed: int, entity_id: str, instant_ns: int, true_range_m: float
) -> dict[str, float]:
    """What it reports for this ping, by keelson subject; empty for nothing.

    **Nothing outside its range** — too shallow to hear the echo apart from the
    ping, or too deep to hear it at all — rather than a clamped or frozen
    value. Losing the bottom is a gap, and a consumer should see one.
    """
    if not sounder.min_range_m <= true_range_m <= sounder.max_range_m:
        return {}
    sigma = sounder.sigma_m + sounder.sigma_fraction * true_range_m
    measured = true_range_m + sigma * gaussian(seed, f"{entity_id}/echo-sounder", instant_ns)
    # A noisy draw cannot put the bottom above the transducer face.
    measured = max(measured, 0.0)
    report = {
        "depth_below_transducer_m": measured,
        "depth_below_surface_m": measured + sounder.transducer_depth_m,
    }
    if sounder.keel_offset_m is not None:
        report["depth_below_keel_m"] = measured - sounder.keel_offset_m
    return report
