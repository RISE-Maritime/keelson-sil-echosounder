"""keelson-sil-echosounder: a simulated echo sounder.

Reads the exact range from a vessel's transducer to the bottom on a
simulator's truth plane, decides what a sounder would have reported, and
publishes that under its own source id.

    keelson-sil-echosounder --realm rise --entity-id slipway-alpha \\
        --transducer-depth-m 0.4 --keel-offset-m 0.6 --connect tcp/127.0.0.1:7447

It reads one subject, `depth_below_transducer_m` under the `truth` source, and
publishes up to three: below the transducer, below the surface and below the
keel, the last two derived from its installed offsets as a real unit's are
(see `model.py`). It is its own process for the reason keelson-sil-gnss is:
deciding what an instrument would have read has a different owner and
different evidence from deciding where the water is, and the operational plane
then carries a modelled reading by construction.

The measurement keeps no state that moves a reading. The noise at a sample is
a pure function of the seed and the sample's own timestamp, so a rerun from
the same epoch, or a restored run, gets the same soundings back.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

import keelson
import zenoh
from google.protobuf.message import DecodeError
from keelson.payloads.Primitives_pb2 import TimestampedFloat
from keelson.scaffolding import declare_liveliness, declare_publisher
from keelson.scaffolding.cli import add_common_arguments, create_zenoh_config
from keelson.scaffolding.signals import GracefulShutdown

from .model import Sounder, due, measure

logger = logging.getLogger("keelson.sil.echosounder")

# SIGBREAK for a service started as a child process on Windows, as
# keelson-sil-gnss lists it: without it the process is killed rather than shut
# down, and a pause ends as a truncated recording rather than a clean gap.
SHUTDOWN_SIGNALS = [signal.SIGINT, signal.SIGTERM] + (
    [signal.SIGBREAK] if hasattr(signal, "SIGBREAK") else []
)

SOURCE_ID = "echo_sounder"
#: Where a simulator publishes the exact range, and what this reads.
TRUTH_SOURCE = "truth"
TRUTH_SUBJECT = "depth_below_transducer_m"
PUBSUB_SUBJECTS = ("depth_below_transducer_m", "depth_below_surface_m", "depth_below_keel_m")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    defaults = Sounder()
    parser = argparse.ArgumentParser(
        "keelson-sil-echosounder",
        description="Simulated echo sounder: the exact range in, a sounder's depths out.",
    )
    add_common_arguments(parser)
    parser.add_argument("-r", "--realm", default="rise")
    parser.add_argument(
        "-e", "--entity-id", required=True, help="The vessel this sounder is fitted to"
    )
    parser.add_argument(
        "-s", "--source-id", default=SOURCE_ID, help="Names the device, as on a real platform"
    )
    parser.add_argument("--seed", type=int, default=0, help="The noise's seed")
    parser.add_argument("--rate-hz", type=float, default=defaults.rate_hz)
    parser.add_argument(
        "--sigma-m", type=float, default=defaults.sigma_m, help="Accuracy floor, one sigma"
    )
    parser.add_argument(
        "--sigma-fraction",
        type=float,
        default=defaults.sigma_fraction,
        help="Share of the depth added to the floor: 0.005 is a datasheet's ±0.5%%",
    )
    parser.add_argument("--min-range-m", type=float, default=defaults.min_range_m)
    parser.add_argument("--max-range-m", type=float, default=defaults.max_range_m)
    parser.add_argument(
        "--transducer-depth-m",
        type=float,
        default=defaults.transducer_depth_m,
        help="The installed offset that makes depth below the surface",
    )
    parser.add_argument(
        "--keel-offset-m",
        type=float,
        default=None,
        help="How far the keel is below the transducer. Absent, no depth below keel is reported.",
    )
    return parser.parse_args(argv)


def sounder_from(args: argparse.Namespace) -> Sounder:
    return Sounder(
        rate_hz=args.rate_hz,
        sigma_m=args.sigma_m,
        sigma_fraction=args.sigma_fraction,
        min_range_m=args.min_range_m,
        max_range_m=args.max_range_m,
        transducer_depth_m=args.transducer_depth_m,
        keel_offset_m=args.keel_offset_m,
    )


def _timestamped_float(value: float, t_ns: int) -> bytes:
    message = TimestampedFloat()
    message.timestamp.FromNanoseconds(t_ns)
    message.value = value
    return keelson.enclose(message.SerializeToString(), enclosed_at=t_ns)


def read_truth(payload: bytes) -> tuple[int, float] | None:
    """`(timestamp_ns, range_m)` from one truth sample, or None for a bad one."""
    try:
        _, _, body = keelson.uncover(payload)
        message = TimestampedFloat.FromString(body)
    except (DecodeError, ValueError, TypeError):
        return None
    return message.timestamp.ToNanoseconds(), message.value


class EchoSounder:
    """One vessel's sounder. What it keeps is only when it last pinged."""

    def __init__(self, session, args: argparse.Namespace):
        self._args = args
        self.sounder = sounder_from(args)
        self._last_ns: int | None = None
        self._publishers = {
            subject: declare_publisher(
                session,
                keelson.construct_pubsub_key(args.realm, args.entity_id, subject, args.source_id),
            )
            for subject in self.published_subjects
        }
        self.pings = 0
        self.lost = 0

    @property
    def published_subjects(self) -> tuple[str, ...]:
        if self.sounder.keel_offset_m is None:
            return tuple(s for s in PUBSUB_SUBJECTS if s != "depth_below_keel_m")
        return PUBSUB_SUBJECTS

    @property
    def truth_key(self) -> str:
        return keelson.construct_pubsub_key(
            self._args.realm, self._args.entity_id, TRUTH_SUBJECT, TRUTH_SOURCE
        )

    def on_truth(self, sample) -> None:
        reading = read_truth(sample.payload.to_bytes())
        if reading is None:
            return
        t_ns, true_range_m = reading
        if not due(self.sounder, t_ns, self._last_ns):
            return
        self._last_ns = t_ns
        report = measure(self.sounder, self._args.seed, self._args.entity_id, t_ns, true_range_m)
        if not report:
            self.lost += 1
            return
        for subject, value in report.items():
            self._publishers[subject].put(_timestamped_float(value, t_ns))
        self.pings += 1


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    config = create_zenoh_config(args.mode, args.connect, args.listen)
    # Never discover a bus by accident.
    config.insert_json5("scouting/multicast/enabled", "false")

    with zenoh.open(config) as session:
        sounder = EchoSounder(session, args)
        session.declare_subscriber(sounder.truth_key, sounder.on_truth)
        with declare_liveliness(
            session,
            args.realm,
            args.entity_id,
            args.source_id,
            pubsub_subjects=sounder.published_subjects,
        ):
            logger.info(
                "[SOUNDER] %s/%s fitted, %.1f Hz, %.1f-%.0f m",
                args.entity_id,
                args.source_id,
                sounder.sounder.rate_hz,
                sounder.sounder.min_range_m,
                sounder.sounder.max_range_m,
            )
            with GracefulShutdown(SHUTDOWN_SIGNALS) as shutdown:
                while not shutdown.is_requested():
                    shutdown.wait(timeout=1.0)
    logger.info("[SOUNDER] %d pings, %d lost the bottom", sounder.pings, sounder.lost)


if __name__ == "__main__":
    sys.exit(main())
