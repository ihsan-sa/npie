# hifi-usb (test fixture)

A stand-in for the hi-fi streamer amp (hifi-lite / hifi-max) while those boards
are still being designed: an ESP32-WROVER-E behind a CH340C on one USB-C, a
CH224A PD sink on the same connector (28 V EPR, 20 V fallback, as on Plus/Max),
a PCM5102A DAC with a buffered sub out, a TAS5825M amp on the PD rail, and a
PCM1863 whose input mux reads back the DAC outputs, the sub out and the
speaker outputs (through 47k/4k7 dividers) for the self-test.

Not a real board: npie's tests generate and run its procedure on the
simulated bench only.
