# Screenshots of the vendor app

Screenshots of the SC20's own web interface, kept as a reference for what the device
exposes and how it labels things.

They are most useful for confirming three things the protocol analysis could only infer:

- **Channel labelling** — that this unit's channels really are White, Blue and Red, in
  that order, and how the vendor app names them in German.
- **Which features are in use** — whether moonlight, cloud simulation and acclimatisation
  are configured, and with what values, so the decoded settings can be checked against
  what the app displays.
- **The daycycle editor** — the setpoint list, which should match what `GET_DYCL` returns.

If a screenshot contradicts something in [`../protocol/`](../protocol/), the screenshot
wins: those documents are reverse-engineered and label their own uncertainty.
