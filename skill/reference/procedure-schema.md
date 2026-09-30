# npie-procedure/1 and npie-run/1

## procedure.json

```json
{
  "schema": "npie-procedure/1",
  "board": "PCB-0018-A_bldc-motor-driver",
  "generated": "2026-09-29T12:00:00Z",
  "inputs": {"netlist": {"path": "kicad/....net", "sha256": "..."},
             "constraints": {...}, "bom": {...}, "manifest": {...} | null},
  "roles": ["psu", "dmm", "scope", "console", "probe"],
  "skipped": [{"stage": "programming", "reason": "no firmware/fwe-manifest.json"}],
  "overrides": [{"step": "rails.02", "generated": {...}, "override": {"max": 5.1}}],
  "stages": [
    {"id": "power-up", "title": "Current-limited power-up", "steps": [
      {"id": "power-up.01", "type": "human",
       "text": "Connect the supply to TP101 (+) and TP102 (-). Output off."},
      {"id": "power-up.02", "type": "supply", "role": "psu",
       "set": {"v": 12.0, "i_limit": 0.2, "output": true}},
      {"id": "power-up.03", "type": "measure", "role": "psu", "quantity": "current",
       "expect": {"nominal": 0.03, "min": 0.002, "max": 0.2, "unit": "A"},
       "derived_from": "constraints power[] idle budget via 80% efficiency"}
    ]}
  ]
}
```

Common step keys: `id` (`<stage>.<nn>`, stable across regenerations of the
same design), `type`, `text` (what a person reads), `derived_from` (every
step with `expect`). Type-specific keys:

| type | keys |
|---|---|
| `human` | `text`, `value` (optional: `{"unit": "V"}` when the person types a reading), `expect` when `value` is set |
| `supply` | `role`, `set: {v, i_limit, output}` |
| `measure` | `role`, `quantity` (`voltage`, `current`, `resistance`, `diode`), `points: {"plus": ..., "minus": ...}`, `expect` |
| `scope` | `role`, `channel`, `points`, `quantity` (`mean`, `vpp`, `freq`, `duty`), `expect`, `screenshot` |
| `logic` | `role`, `channels`, `samplerate`, `duration_s`, `expect: {"edges_min": n}` |
| `flash` | `role` (`probe`), `artifact` (`elf`/`hex`/`bin`) |
| `console` | `role`, `send` (or null to only read), `expect_re`, `timeout_s`, `fields` (JSON path -> expect) |
| `wait` | `seconds` |

`points` are `{"ref": "TP101", "pin": "1", "net": "/power_in/VM_IN", "label": "TP101 (VM_IN)"}`.

## run.json

```json
{
  "schema": "npie-run/1",
  "procedure_sha256": "...", "board": "...", "bench": "owner-laptop | sim",
  "started": "...", "finished": "... | null",
  "status": "running | awaiting_human | passed | failed | aborted",
  "cursor": "rails.04",
  "steps": [
    {"id": "rails.04", "verdict": "pass | fail | skip | pending",
     "value": 3.301, "unit": "V", "expect": {...},
     "confirmed_by": "ihsan", "confirmed_at": "...", "note": "...",
     "captures": ["captures/rails.07.png"], "t": "..."},
    {"id": "programming.02", "verdict": "pass", "log": "...",
     "artifact_sha256": "<manifest artifact.sha256 of the flashed kind>"}
  ],
  "safe_state": "supply outputs off at ... (rails.04 out of limits)",
  "error": "rails.04: ... (only when aborted)",
  "faults": ["short:+5V"], "seed": 1, "hold": false
}
```

`faults`, `seed` and `hold` are present only on a simulated run (`bench:
"sim"`); a live run records the `bench_config` it used instead.

A human step's record always has `confirmed_by` and `confirmed_at`; a run
cannot pass with one missing.
