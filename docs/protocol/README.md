# daytime SC20 — recovered protocol reference

The daytime SC20 ("Smart Control SC20", OEM aquaLEDs.de UG "Light-Symphony") has no public API
documentation. Everything in this directory was recovered by reverse engineering. It is the
source of truth for `custom_components/daytime_sc20/`.

## Documents

| File | Covers |
|---|---|
| [`ws-protocol.md`](ws-protocol.md) | The WebSocket envelope, the full inbound/outbound message inventory, addressing and the mesh model, `USRDTA`, connection lifecycle and pacing |
| [`lighting-model.md`](lighting-model.md) | The channel model, `DYCL` daycycle format, `CCV` live values, manual-vs-schedule modes, and the `MOON` / `CLOUD` / `ACCLIMATE` effects |
| [`firmware.md`](firmware.md) | Evidence from the vendor firmware image: HTTP endpoint list, the mesh-internal `/login?<CMD>` API, the SPIFFS contents, revision scheme, and the absence of authentication |
| [`scenarios/`](scenarios/) | The three official vendor `.scen` files, used to decode the daycycle format |
| `capture/` | Live frame captures from the device (produced by `tools/sc20_probe.py`) |

Every claim in these documents is labelled **CONFIRMED** (with a quoted code snippet and a byte
offset or line number), **INFERRED**, or **UNKNOWN**. Treat that labelling as load-bearing: the
analysis had no ground truth to test against, so an INFERRED claim is a hypothesis that happens
to fit the evidence, not a fact.

## Quick reference

```text
connect       ws://<ip>/ws          WebSocket subprotocol "arduino"; JSON, one object per frame
              (no authentication of any kind)

read          {"title":"GET_USRDTA"|"GET_CLOCK"|"GET_DYCL"|"GET_DSCRPTN"|"GET_MOON"|
                        "GET_CLOUD"|"GET_ACCL"|"GET_NET_ST"|"GET_NET_AP","to":"MASTER","from":"USER"}
              {"title":"REQ_CCV","to":"MASTER","from":"USER"}              -> CCV
keepalive     {"title":"GET_MESH_NETWORK","to":"MASTER","from":"USER"}     every ~3 s

mode          {"title":"MAN_MODE"|"DAYCL_MODE","to":"ALL-LIGHTS","from":"USER"}
set channels  {"title":"CCV-SL","to":"ALL-LIGHTS","from":"USER","currentValues":[W,B,R]}
set schedule  {"title":"DYCL","to":"ALL-LIGHTS","from":"USER",
               "configuration":[[0,w,b,r],...,[1440,w,b,r]]}
```

Channels are fixed at three, in this order: **0 = White, 1 = Blue, 2 = Red**. Values are integer
percent 0–100; the SC20 applies no gamma and no scaling, so the wire value is the UI value.

Writes are **never acknowledged**. After any write, re-read with the matching `GET_*` to confirm.

## Wire-encoding traps

These will silently corrupt settings if a caller forgets them. In this codebase they are
confined to `custom_components/daytime_sc20/api/protocol.py` and must not be duplicated
elsewhere.

- **`CLOCK.month` is 1-based on the wire.** Python's `datetime.month` is also 1-based, so no
  conversion is needed here — but the JavaScript UI does `month + 1` on send because JS `Date`
  months are 0-based. Do not copy that offset.
- **`CLOUD` durations are seconds on the wire**, minutes in the UI.
- **`CLOUD` intensities are inverted *and* swapped**: `wire.minIntensity = 100 - ui.maxIntensity`
  and `wire.maxIntensity = 100 - ui.minIntensity`. The wire value expresses the *remaining light
  level* during a cloud; the UI slider expresses cloud *strength*.
- `tankconfig` and `power` may arrive as JSON-**stringified** arrays on multi-lamp installations.

## How the analysis was done (to reproduce or extend it)

The `ws-protocol.md` and `lighting-model.md` citations reference two working files that are not
committed, because they are derived artefacts:

- `allJSFiles.js` — the device's own web-app bundle, 1,167,683 bytes, minified onto one line.
  Fetch with `curl --compressed http://<ip>/lib/allJSFiles.js`. Byte offsets (`@NNN`) index
  into this file. Vendor libraries occupy roughly the first 780,000 bytes; the application code
  runs from there to the end.
- `app.beauty.js` — a beautified copy of the application region, used for the `L:NNN` line
  citations. Regenerate with:

  ```bash
  python3 -c "
  import re
  d = open('allJSFiles.js', errors='replace').read()
  open('app.beauty.js','w').write(re.sub(r'([;{}])', r'\1\n', d[780000:]))
  "
  ```

  Note this is a naive line-breaker that is not string-aware, so it corrupts a few literals. It
  is a reading aid, not runnable code.

The firmware image is **not committed**: it is 1.5 MB and its header carries an aquaLEDs.de UG
confidentiality notice. Obtain it from the vendor and split it yourself:

```text
http://data.daytime.de/update  ->  http://data.daytime.de/Firmware-Files/Firmware-Combined/firmware_daytime.sc20

container layout (1,531,829 bytes as published 2025-03-25):
  bytes 0..6      ASCII "503664\n"      length of the application image
  bytes 7..68     ASCII copyright line
  bytes 69..      ESP8266 application image, magic 0xE9, 503,664 bytes   (.daytime_ino)
  remainder       SPIFFS filesystem image, 1,028,096 bytes               (.daytime_spiffs)
```

The SPIFFS image holds the web app, including `/lib/allJSFiles.js.gz` — so the bundle can be
recovered from the firmware alone, without touching a device.

## Confirmed against real hardware

Recorded 2026-08-14 by `tools/sc20_probe.py` against an SC20 on firmware revision `[23, 15]`
(webserver 02.3, website 01.5). Every packet and field name predicted by the static analysis
appeared exactly as described. These points go beyond that:

- **The device pushes its whole state on connect, unprompted, as two JSON arrays** —
  `[USRDTA, CCV, CLOCK, MOON, CLOUD, ACCLIMATE]` then
  `[DSCRPTN, DYCL, NET_ST, NET_AP, MESH_NETWORK]`. A client can therefore populate itself by
  connecting and listening; the `GET_*` sweep is only needed as a fallback and for refreshes.
- **Frames arrive either as a single object or as an array of objects.** A reader that assumes
  an object will crash on the connect burst. Handle both.
- **Multiple WebSocket clients are supported simultaneously, and the device broadcasts every
  frame to all of them** — including frames sent *by* other clients. Frames carrying
  `"from":"USER"` are another client's command echoed back, never device state, and must be
  dropped. (Observed: another client's 3-second `GET_MESH_NETWORK` heartbeats arriving on our
  own connection.) Home Assistant and the phone web UI can safely be connected at once.
- **`CCV.currentValues` has exactly 3 elements** on this hardware.
- **The device does not pad its `DYCL` table** — it returned exactly the 6 rows it holds.
- **`GET_CCMODE` replies with a frame titled `CCMODE`**, not with a bare `MAN_MODE` /
  `DAYCL_MODE` frame as the static analysis suggested: `{"title":"CCMODE","mode":"MAN_MODE"}`.
  It is addressed to `ALL-LIGHTS`, not `MASTER`. `CLOCK.mode` carries the same value and
  arrives in the connect burst, so either works as a mode source.
- **`MAN_MODE` / `CCV-SL` / `DAYCL_MODE` all behave as described.** Sending `MAN_MODE` flips
  `CLOCK.mode` to `"MAN_MODE"` at once; a `CCV-SL` write reads back verbatim through `REQ_CCV`;
  `DAYCL_MODE` restores scheduled operation.
- **`USRDTA.power` arrives as a *string*** (`"17"`), not an integer.
- **`revision` is `[webserver, website]`**, displayed by the UI as `0X.Y` — so `[23, 15]` means
  webserver 02.3 and website 01.5.
- **`CCV` reports the true live output, including effect modulation — not the programmed
  value.** With `cloudActive:1` and the schedule calling for 90 %, `CCV` was observed at 64,
  then 60, then drifting to 59 over 30 seconds. A brightness reading taken from `CCV` therefore
  moves continuously while clouds are active; the *programmed* level has to be derived by
  interpolating `DYCL` instead.
- The `moonStart` / `moonEnd` window **wraps past midnight** — this unit holds
  `moonStart:1320, moonEnd:360` (22:00 → 06:00), so `moonStart > moonEnd` is normal and valid.

**INFERRED from the cloud observation, not proven:** effects appear to modulate the programmed
level *multiplicatively*, with the wire `minIntensity`/`maxIntensity` acting as the remaining
fraction — 59 observed against a programmed 90 is a factor of ~66 %, inside the configured
`[60, 100]` band. A single observation cannot distinguish this from several other curves.

## Cross-checked against the vendor app

Screenshots of the vendor web app on the same device, taken while it held the configuration
captured above, confirm the decoding field for field. See
[`../screenshots/`](../screenshots/).

**Cloud simulation** — this settles both wire quirks, which were the highest-risk part of
the decoding:

| Vendor app shows | On the wire | Decoded to |
|---|---|---|
| Wolken: on | `cloudActive: 1` | `active=True` |
| Max. Anzahl an Wolken pro Tag: **150** (of 1500) | `maxAmount: 150` | `max_per_day=150` |
| Ø Dauer pro Wolke: **10 min – 25 min** | `minDuration: 600`, `maxDuration: 1500` | `min_duration_minutes=10`, `max_duration_minutes=25` |
| Wolkenintensität: **0 % – 40 %** | `minIntensity: 60`, `maxIntensity: 100` | `min_intensity=0`, `max_intensity=40` |
| Wahrscheinlichkeit: **65 %** | `probability: 65` | `probability=65` |

The duration row proves the seconds conversion; the intensity row proves both the inversion
*and* the min/max exchange — the app's 0–40 % maps to the wire's 60–100 %, with the ends
crossed over. Neither could have been settled by the wire values alone.

**Daycycle easy mode** — confirms the `DSCRPTN` grammar:

| Vendor app shows | `DSCRPTN` field |
|---|---|
| Day from **06:00** to **22:00** | `start:360`, `end:1320` |
| Sunrise **02h 00min**, sunset **02h 00min** | `sunrise:120`, `sunset:120` |
| Helligkeit **90 %** | `intensity:90` |
| "Helligkeit je Lichtfarbe" unticked | `individual:false` |

And the corresponding `DYCL` is exactly what those settings imply —
`[[0,0,0,0],[360,0,0,0],[480,90,90,90],[1200,90,90,90],[1320,0,0,0],[1440,0,0,0]]`: dark
until 06:00, full at 06:00 + 2 h = 08:00 (minute 480), holding until 22:00 − 2 h = 20:00
(minute 1200), dark again at 22:00. The app's graph draws those ramps as straight lines,
which is further support for linear interpolation — though still the app's rendering rather
than proof of what the firmware does.

Note that `intensity:90` and `intensities:85,85,85` disagree here. With `individual:false`
the per-channel list is ignored and `intensity` is what took effect — the `DYCL` holds 90.

## Still open

1. Whether `MAN_MODE` survives a reboot, and whether anything ever auto-reverts to `DAYCL_MODE`.
   (Not tested — it would require power-cycling a live aquarium controller.)
2. Whether the firmware distinguishes `CCV-SL` from `CCV-SW`, or normalises both to `CCV`.
   `CCV-SL` is confirmed to work; `CCV-SW` was not sent.
3. The `CLOUD.mode` numeric mapping — 0/1/2 = synchronous/delayed/individual is inferred from
   the order of the UI's translation strings only. Only matters for multi-lamp installations.
4. How the firmware interpolates between daycycle setpoints (linear is inferred from the UI
   graph), and what it does at midnight when row(0) and row(1440) differ.
5. The exact function by which `MOON` / `CLOUD` / `ACCLIMATE` modulate the `DYCL` output.

## Device HTTP endpoints

Alongside the WebSocket, the device serves plain HTTP. Only the read-only ones are used by this
integration.

| Path | Method | Notes |
|---|---|---|
| `/`, `/index.htm` | GET | The AngularJS web app |
| `/connectioncheck?r=<rand>` | GET | JSONP reachability probe, returns `angular.callbacks._0({"id":1})` |
| `/heap` | GET | Free heap in bytes, as plain text |
| `/serverLog` | GET | HTML: uptime, heap, operating hours, firmware and webapp version |
| `/website.revision` | GET | Web-app revision number |
| `/update` | GET / **POST** | OTA firmware upload form. **Never POST to this.** |
| `/formateeprom`, `/formatspiffs` | GET | **Destructive.** A plain GET wipes settings or the web app. Never request these. |
| `/login?<CMD>` | GET | Mesh-internal API used between master and client nodes, not by the app |
