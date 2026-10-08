# npie LEARNINGS

Append-only, dated, tagged. Grep by tag before touching an area.

## Tags
[netlist] [manifest] [limits] [sim] [scpi] [sigrok] [serial] [swd] [report] [fwe]

## 2026-09-29 [netlist] KiCad 10 pinfunction carries the pin number
A kicadsexpr export writes `(pinfunction "S_1")`, `(pinfunction "REF1_7")`: the
function name with `_<pin>` appended. design.parse_netlist strips it; match on the
stripped name (`S`, `D`, `REF1`), never the raw string.

## 2026-09-29 [limits] constraints voltages[] is a rating, not a nominal, for power nets
hwde writes the clearance voltage there: VM and the phases carry 45 V (the TVS
clamp) on a 10-28 V board, bootstrap nets 57 V. Regulated rails carry their
nominal. So a rail rated like the input follows the supply setpoint, and the
operating range comes from requirements.md ("10-28 V operating").

## 2026-09-29 [netlist] hwde's `lib` package name collides
hwde's scripts import `lib`; npie's package is `npielib` so a pytest session that
imports both never gets the wrong one.

## 2026-09-29 [sim][fwe] a hook's reply is more than "OK"
fwe's `version` hook on PCB-0018 expects `^OK {"board":"PCB-0018-A"`; the sim
console's generic `OK {"sim":true}` failed it and stopped the dry run at the
first block. SimBoard now answers a console step with a literal built from the
step's own expect_re when the generic reply does not match. A dry run against
the fixture manifest alone would not have found this: dry-run against the
board's real, built manifest.

## 2026-09-29 [fwe] the manifest's artifact.sha256 is a dict
`artifact.sha256` is keyed by artifact kind (`{"elf": ...}`), not one hash;
the flash step records `sha256[<step artifact>]`.

## 2026-10-06 [fwe][serial] the FPGA PWM generator has no frequency register
fwe-pwm8-reg/1 sets duty and phase in steps of a period and enables/commits;
f_rf is whatever the bitstream's PLLs make (13.56 MHz +19.75 ppm on the ECP5
board, N = 56). The pwm driver treats a step's f_hz as a check and aborts on a
mismatch; a different frequency is a rebuild or the SDG6032X. A read reply of
0x45 is a value, not 'E': the driver only reads known addresses.

## 2026-10-06 [scpi] verify-later: fwe-pwm8 and scpi-sdg have not met hardware
Both are written from their sources (the fwe gateware's pwm8_ctrl.v; Siglent's
SDG programming guide: Cn:BSWV, Cn:OUTP, MODE PHASE-LOCKED) and tested against
protocol models only. First bench run: check the SDG's BSWV? reply format and
units, and that the PWM UART answers 0xF8 after the EVN host-link rework
(R34/R35 fitted as 0R; see the next entry).

## 2026-10-06 [serial] the EVN host-link rework keeps R22/R23
PR #3 said R22/R23 come off; wrong. They stay fitted because they carry I2C.
Per the Lattice user guide only R34/R35 get fitted as 0R. Then FT2232H port
B's EEPROM is backed up and switched to UART with fixFT2232_ecp5evn; the
FPGA's rx is P2 and its tx P3. The procedure's EVN steps are human-confirmed.

## 2026-10-08 [limits][fwe] a boost's output and bootstrap nets are ratings too
On PCB-0026-A the boost output (+48V) carries 60 V and the bootstrap nets
(HB, BOOT) 65 V and 35 V in voltages[]. Idle, the output sits at the input
less the high side's reverse conduction, so it follows the supply (-3 V/+1 %);
bootstrap nets are not rails. The fwe boost firmware drives HI as LO's
complement, so open loop Vout = Vin/(1-D) even unloaded, and `duty` is refused
while `arm` regulates. Its input trips latch only while switching.

## 2026-10-08 [scpi] verify-later: the scope's dead-time query
ScpiScope reads `deadtime` as `MEAS:FRD? CHAN1,CHAN2` (falling-to-rising delay,
seconds), written from the generic Rigol-style set and never sent to a scope.
First bench run on PCB-0026-A: check the query and its sign on the real scope.
