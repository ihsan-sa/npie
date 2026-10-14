# The `selftest` section of fwe-manifest/1 (contract `npie-selftest/1`)

What npie expects of the firmware on a USB-first board: an ESP32 behind a
USB serial bridge (a CH340) on the board's own USB-C, with a loopback ADC that
reads back the board's audio outputs. /fwe owns `fwe-manifest/1`; this file is
npie's side of one section of it, so /fwe can build the firmware against it.
Nothing here exists in firmware yet.

## The flow it serves

Plug the board's USB-C into a laptop or the server: npie finds the bridge,
flashes the app with esptool, resets into it, reads the boot log and runs
the self-test below. Then the PD charger goes on (on a one-port board it
replaces the host's cable, so the console moves to Wi-Fi) and the speakers,
and the same self-test checks the contract, the amp rail and every output.

## Manifest section

```json
{
 "flash": {
  "interface": "usb-uart", "connector": "J1",
  "esptool": {"chip": "esp32", "baud": 460800,
              "images": [{"offset": "0x1000",  "file": "build/bootloader.bin"},
                         {"offset": "0x8000",  "file": "build/partition-table.bin"},
                         {"offset": "0x10000", "file": "build/hifi.bin"}]}
 },
 "artifact": {"bin": "build/hifi.bin", "sha256": {"bin": "<sha256 of the app image>"}},
 "uart": {"connector": "J1", "bridge": "CH340C", "baud": 115200, "format": "8N1",
          "banner_regex": "^hifi-fw "},
 "selftest": {
  "contract": "npie-selftest/1",
  "usb": {"vid_pid": "1a86:7523", "baud": 115200},
  "tcp": {"port": 3333},
  "taps": {"dac_l": "VINL1", "dac_r": "VINR1", "sub": "VINL2",
           "spk_l": "VINL3", "spk_r": "VINR3"},
  "tests": ["rails", "pd", "tone", "net"]
 }
}
```

- `flash.esptool.images`: paths under `firmware/`, written in this order with
  `--before default_reset --after hard_reset`.
- `uart.banner_regex`: the first line the app prints once it is up. npie
  resets the ESP32 (EN through RTS, IO0 high through DTR, as esptool does)
  and reads the boot log until this line.
- `selftest.usb.vid_pid`: how any host finds the board (CH340: `1a86:7523`).
- `selftest.tcp.port`: the console's TCP port once the app is on Wi-Fi.
- `selftest.taps`: every loopback tap the firmware can read, by npie's tap
  name, mapped to the firmware's own input (an ADC input or a mux channel).
  npie's tap name is the netlist net `TAP_<KIND>[_<CH>]` lowercased without
  `TAP_` (`/loopback/TAP_SPK_L` -> `spk_l`); KIND is `DAC`, `SUB` or `SPK`.
  A netlist tap missing here is listed as skipped in the procedure.

## The console

The same lines over USB serial (115200 8N1) and over TCP (`selftest.tcp.port`;
one client at a time). Lines end in `\n` (`\r\n` accepted). npie sends one
command and waits for its reply; log lines (ESP-IDF `I (123) tag: ...`) may
come between them and are skipped.

Request: `st <test> [args]`. Reply: one line, either

    OK {"test":"<test>", ...fields}
    ERR <reason, free text>

The JSON is one object on one line and always carries `"test"`, so npie can
tell this reply from any other. Numbers are plain JSON numbers in the units
below; nothing is scaled by npie.

| Command | Reply fields | Units and meaning |
|---|---|---|
| `st rails` | `v3v3_v`, `vbus_v`, `vamp_v` | V. The 3.3 V rail, VBUS at the USB-C (5 V on a host, the contract on a charger) and the amp's PVDD rail (0 until a PD contract is held) |
| `st pd` | `contract_v`, `epr`, `source` | `contract_v` V: what the CH224A negotiated, read back from VBUS. `epr`: true for an EPR contract (28 V). `source`: `usb-host` (no PD) or `charger` |
| `st tone <tap> <freq_hz> <level_dbfs>` | `tap`, `freq_hz`, `vrms`, `thdn_db` | Play a sine at `level_dbfs` (dB re the DAC's full scale) into the path that tap watches, measure it on the loopback ADC, stop. `freq_hz` Hz measured; `vrms` the level at the tapped node in V rms (the firmware undoes its divider); `thdn_db` THD+N in dB re the fundamental, 20 Hz-20 kHz, unweighted. Speaker taps play through the amp, so they need a contract; the firmware unmutes the amp only for the tone |
| `st net` | `ip`, `port`, `rssi_dbm` | The Wi-Fi console's address (`ip` a string, `port` the TCP port) and signal in dBm. `ERR wifi not joined` when it has no network |

Each command answers within 5 s (`st net` within 20 s while Wi-Fi joins).
How Wi-Fi gets its credentials is the firmware's own business (set at build
time or by its own provisioning); npie only asks where the console is.

### Examples

    > st rails
    < OK {"test":"rails","v3v3_v":3.296,"vbus_v":5.02,"vamp_v":0.0}
    > st tone dac_l 1000 -20
    < I (5120) loop: mux VINL1
    < OK {"test":"tone","tap":"dac_l","freq_hz":1000.03,"vrms":0.2094,"thdn_db":-93.4}
    > st pd
    < OK {"test":"pd","contract_v":27.94,"epr":true,"source":"charger"}
    > st tone sub 1000 -20
    < ERR tap not wired

## What npie checks (limits come from the design)

The procedure generator (npielib/procgen.py) sets every limit from the board
workspace; the firmware only reports. See procgen's rules for the numbers:
VBUS 4.4-5.25 V on a host, the 3.3 V rail +/-5 %, the contract and amp rail
at the constraints `pd-sink` block's `contract_v` or `fallback_v` +/-5 %, and
each tone's frequency +/-0.5 %, level +/-1 dB of the constraints `loopback`
block's `dac_fs_vrms` and the tap's `gain_db`, THD+N under the block's
`thdn_db_max` for the tap's kind.
