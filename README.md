# keelson-sil-echosounder

A simulated echo sounder for software-in-the-loop testing, in the keelson
family beside `keelson-sil-gnss`.

It subscribes to the exact range from a vessel's transducer to the bottom,
`depth_below_transducer_m` under the `truth` source, which a simulator such as
Slipway publishes for a hull fitted with a sounder. It publishes what a
sounder would have reported, under its own source id (`echo_sounder` by
default):

| subject | is |
| --- | --- |
| `depth_below_transducer_m` | the range, with the sounder's noise |
| `depth_below_surface_m` | that plus `--transducer-depth-m` |
| `depth_below_keel_m` | that minus `--keel-offset-m`; only when one is given |

The two derived depths come from installed offsets, as a real unit's do, so the
sounder knows nothing about tide or squat. A squatting hull's below-surface
figure is short by the squat, and that is the instrument's real error.

```bash
uv run keelson-sil-echosounder --realm rise --entity-id slipway-usv-alpha \
    --transducer-depth-m 0.4 --keel-offset-m 0.6 --mode client --connect tcp/127.0.0.1:7447
```

## The model

- **Rate:** `--rate-hz` (1 Hz by default). It pings on the first truth sample of
  each period, so its rate is its own and not the simulator's tick rate.
- **Accuracy:** one sigma of `--sigma-m` plus `--sigma-fraction` of the depth
  (0.05 m + 0.5 % by default). These are nominal, spec-sheet-class values, not
  a validated model of any one unit.
- **Range:** outside `--min-range-m`..`--max-range-m` (0.5–200 m) nothing is
  published. Losing the bottom is a gap, never a frozen or clamped value, and
  so is water nobody surveyed, which the simulator never puts on truth at all.
- **Noise** is a pure function of `--seed`, the entity and the sample's own
  timestamp, so a rerun from the same epoch, or a restored run, reproduces it.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check .
```

Licensed under Apache-2.0.
