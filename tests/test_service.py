"""The service around the model: which key it reads, what it puts, and where."""

from __future__ import annotations

import keelson
import pytest
from keelson.payloads.Primitives_pb2 import TimestampedFloat

from keelson_sil_echosounder import main as service

NS = 1_000_000_000


class _Pub:
    def __init__(self, key):
        self.key, self.puts = key, []

    def put(self, payload, **_):
        self.puts.append(payload)


class _Sample:
    def __init__(self, payload: bytes):
        self.payload = self

        self._bytes = payload

    def to_bytes(self) -> bytes:
        return self._bytes


def truth(value: float, t_ns: int) -> _Sample:
    message = TimestampedFloat(value=value)
    message.timestamp.FromNanoseconds(t_ns)
    return _Sample(keelson.enclose(message.SerializeToString(), enclosed_at=t_ns))


def a_sounder(monkeypatch, *argv):
    pubs: dict[str, _Pub] = {}

    def declare(_session, key, **__):
        pubs[key] = pub = _Pub(key)
        return pub

    monkeypatch.setattr(service, "declare_publisher", declare)
    args = service.parse_args(["--entity-id", "slipway-alpha", *argv])
    return service.EchoSounder(object(), args), pubs


def values(pub: _Pub) -> list[float]:
    return [TimestampedFloat.FromString(keelson.uncover(p)[2]).value for p in pub.puts]


def test_it_reads_the_truth_planes_range_for_its_own_vessel(monkeypatch):
    sounder, _ = a_sounder(monkeypatch)
    assert sounder.truth_key == "rise/@v0/slipway-alpha/pubsub/depth_below_transducer_m/truth"


def test_it_publishes_under_its_own_source_and_only_what_it_was_given(monkeypatch):
    _, pubs = a_sounder(monkeypatch)
    assert sorted(pubs) == [
        "rise/@v0/slipway-alpha/pubsub/depth_below_surface_m/echo_sounder",
        "rise/@v0/slipway-alpha/pubsub/depth_below_transducer_m/echo_sounder",
    ]


def test_a_ping_puts_each_depth_at_the_truth_samples_own_time(monkeypatch):
    sounder, pubs = a_sounder(
        monkeypatch, "--sigma-m", "0", "--sigma-fraction", "0",
        "--transducer-depth-m", "0.4", "--keel-offset-m", "0.6",
    )
    sounder.on_truth(truth(10.0, 3 * NS))

    key = "rise/@v0/slipway-alpha/pubsub/{}/echo_sounder".format
    assert values(pubs[key("depth_below_transducer_m")]) == [pytest.approx(10.0)]
    assert values(pubs[key("depth_below_surface_m")]) == [pytest.approx(10.4)]
    assert values(pubs[key("depth_below_keel_m")]) == [pytest.approx(9.4)]
    stamped = TimestampedFloat.FromString(
        keelson.uncover(pubs[key("depth_below_transducer_m")].puts[0])[2]
    )
    assert stamped.timestamp.ToNanoseconds() == 3 * NS


def test_ten_samples_a_second_is_one_ping_a_second(monkeypatch):
    sounder, pubs = a_sounder(monkeypatch)
    for tick in range(20):
        sounder.on_truth(truth(10.0, tick * NS // 10))
    assert sounder.pings == 2
    assert all(len(p.puts) == 2 for p in pubs.values())


def test_losing_the_bottom_puts_nothing(monkeypatch):
    sounder, pubs = a_sounder(monkeypatch, "--max-range-m", "50")
    sounder.on_truth(truth(80.0, NS))
    assert sounder.lost == 1
    assert all(p.puts == [] for p in pubs.values())


def test_a_sample_that_is_not_a_float_is_ignored(monkeypatch):
    sounder, _ = a_sounder(monkeypatch)
    sounder.on_truth(_Sample(b"not keelson"))
    assert sounder.pings == 0 and sounder.lost == 0
