"""The sounder's arithmetic: what it reports, when, and when it reports nothing."""

from __future__ import annotations

import statistics

import pytest

from keelson_sil_echosounder.model import NS, Sounder, due, measure

EXACT = Sounder(sigma_m=0.0, sigma_fraction=0.0, transducer_depth_m=0.4, keel_offset_m=0.6)


def test_with_no_noise_the_three_depths_are_the_offsets_apart():
    assert measure(EXACT, 1, "alpha", NS, 10.0) == {
        "depth_below_transducer_m": pytest.approx(10.0),
        "depth_below_surface_m": pytest.approx(10.4),
        "depth_below_keel_m": pytest.approx(9.4),
    }


def test_no_keel_offset_entered_is_no_depth_below_keel():
    assert "depth_below_keel_m" not in measure(Sounder(), 1, "alpha", NS, 10.0)


@pytest.mark.parametrize("true_range_m", [0.2, 250.0], ids=["too-shallow", "too-deep"])
def test_outside_its_range_it_reports_nothing(true_range_m):
    """Losing the bottom is a gap, never a clamped value."""
    assert measure(EXACT, 1, "alpha", NS, true_range_m) == {}


def test_the_same_coordinates_draw_the_same_sounding():
    """A rerun from the same epoch, or a restored run, gets the same reading."""
    assert measure(Sounder(), 7, "alpha", 5 * NS, 12.0) == measure(
        Sounder(), 7, "alpha", 5 * NS, 12.0
    )


def test_two_vessels_and_two_seeds_draw_independently():
    a = measure(Sounder(), 7, "alpha", 5 * NS, 12.0)
    assert a != measure(Sounder(), 7, "bravo", 5 * NS, 12.0)
    assert a != measure(Sounder(), 8, "alpha", 5 * NS, 12.0)


def test_the_error_has_the_spread_it_was_given():
    """Floor plus share of depth: 0.05 + 0.005 * 40 = 0.25 m at forty metres."""
    errors = [
        measure(Sounder(), 3, "alpha", t * NS, 40.0)["depth_below_transducer_m"] - 40.0
        for t in range(4000)
    ]
    assert statistics.fmean(errors) == pytest.approx(0.0, abs=0.02)
    assert statistics.pstdev(errors) == pytest.approx(0.25, rel=0.05)


def test_noise_cannot_put_the_bottom_above_the_transducer():
    rough = Sounder(sigma_m=5.0, min_range_m=0.0)
    assert all(
        measure(rough, 1, "alpha", t * NS, 0.1)["depth_below_transducer_m"] >= 0.0
        for t in range(500)
    )


def test_it_pings_once_a_period_whatever_the_tick_rate():
    """Ten truth samples a second, one ping a second."""
    sounder = Sounder(rate_hz=1.0)
    last = None
    pings = []
    for tick in range(30):
        t_ns = tick * NS // 10
        if due(sounder, t_ns, last):
            last = t_ns
            pings.append(t_ns)
    assert pings == [0, NS, 2 * NS]


def test_time_going_backwards_pings_at_once():
    """A restore moves the clock back, and the sounder does not sulk."""
    assert due(Sounder(), 2 * NS, last_ns=10 * NS)


@pytest.mark.parametrize(
    "kwargs",
    [{"rate_hz": 0.0}, {"sigma_m": -1.0}, {"min_range_m": 50.0, "max_range_m": 10.0}],
)
def test_a_sounder_no_one_could_build_is_refused(kwargs):
    with pytest.raises(ValueError):
        Sounder(**kwargs)
