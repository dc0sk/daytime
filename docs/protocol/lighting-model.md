# daytime SC20 ("Smart Control SC20" / aquaLEDs "Light-Symphony") — Lighting Data-Model Reference

Source: static analysis of the device web UI bundle `allJSFiles.js` (1,167,683 bytes,
AngularJS app; app code ≈ offsets 780,000–end) and vendor `scen/*.scen` files.
Every claim is labelled **CONFIRMED (code)**, **INFERRED**, or **UNKNOWN**.
Byte offsets are into `allJSFiles.js` so quotes can be re-checked with e.g.
`python3 -c "d=open('allJSFiles.js').read(); print(d[850057:850160])"`.

## 0. Transport & packet envelope

- **CONFIRMED (code @1102556):** `new WebSocket("ws://"+e+"/ws",["arduino"])` — endpoint is
  `ws://<host>/ws`, WebSocket subprotocol `arduino` (classic ESP8266 arduinoWebSockets stack).
  Reachability probe before connecting: `GET http://<host>/connectioncheck?r=<rand>` (JSONP).
- **CONFIRMED:** frames are plain JSON text, one object per frame (`t.onmessage=... JSON.parse(e.data)`);
  the receive path also tolerates an *array* of packet objects.
- **CONFIRMED:** envelope fields on every packet: `title` (packet type), `to`, `from`.
  UI always stamps `from:"USER"` when sending; device packets carry `from:"<MAC>"`, `to:"USER"`.
  Addressing values seen: a client MAC/BSSID, `"ALL-LIGHTS"` (broadcast to lamps), `"MASTER"`,
  `"ALL"`. UI ignores any received packet with `from:"USER"` (echo suppression).
- **CONFIRMED (@1104603):** request/response pairs
  `[["USRDTA","GET_USRDTA"],["CCV","REQ_CCV"],["CLOCK","GET_CLOCK"],["MOON","GET_MOON"],["CLOUD","GET_CLOUD"],["ACCLIMATE","GET_ACCL"],["DSCRPTN","GET_DSCRPTN"],["DYCL","GET_DYCL"],["NET_ST","GET_NET_ST"],["NET_AP","GET_NET_AP"],["MESH_NETWORK","GET_MESH_NETWORK"]]`.
  Requests are `{title:"GET_…"|"REQ_CCV", to:"MASTER", from:"USER"}`; the UI re-requests any
  missing packet every 2 s until all arrive (`timerReRequestMissingPackets:2e3` @1104886).
- **CONFIRMED (@1101035):** heartbeat = `GET_MESH_NETWORK` every 3 s; missing 3 heartbeats
  (9 s) triggers reconnect. `MESH_NETWORK` response: `{clientList:["<MAC>",...]}`, last entry = master.
- **CONFIRMED:** outgoing frames are queued and paced 40 ms apart (`sendinterval:40`).
- **CONFIRMED (@813916):** daytime build uses host name `sc20` (mDNS `sc20.local`); AP SSID
  default "Smart Control SC20"; daytime device BSSIDs start `A0:B1:BD` (@831077).
- Full packet-type inventory seen in code: `USRDTA/GET_USRDTA`, `MAX_CHANNEL_VALUES`,
  `CCV/CCV-SL/CCV-SW/CCV-Hari/REQ_CCV`, `CLOCK/GET_CLOCK`, `MAN_MODE`, `DAYCL_MODE`, `GET_CCMODE`,
  `MOON/GET_MOON`, `CLOUD/GET_CLOUD`, `ACCLIMATE/GET_ACCL`, `PAUSE_ACCLIMATION`,
  `DSCRPTN/GET_DSCRPTN`, `DYCL/GET_DYCL`, `PREV-PNT`, `PREV-CRV`, `PREV`, `NET_ST`, `NET_AP`,
  `SCANNED_NETWORKS_{SSID,BSSID,STATUS}`, `MESH_NETWORK/GET_MESH_NETWORK`, `FINISH_AP`,
  `DISCONNECT_CLIENT`, `SET_WIFI_TURN_OFF`, `START_FOTA`, `RESET_TO_DEFAULT`.

## 1. DYCL — the daycycle / scenario format

### Row format — hypothesis CONFIRMED, with channel-count nuance
- **CONFIRMED:** each row is `[minute_of_day, ch0, ch1, …]`, minutes `0…1440`, channel values
  integer percent `0…100`. Evidence:
  - Expert editor imports rows as `minutes` + per-channel `{value}` objects, sliders 0–100,
    `Math.round` everywhere; time translated via `time` filter on the minute number.
  - `1440==r&&(r=1439)` (@978722): a stored 1440 is clamped to 1439 for editing; on save the
    last row is forced back: `t[t.length-1][0]=1440` (@980855).
- **CONFIRMED:** channel count is **variable by product version, not fixed 3**:
  - HC / HC+ / HC+ integrated: 6 dimm channels (+ a 7th moonlight column in library/demo data).
  - EHEIM and **DAYTIME (SC20): 3 channels**. The parser explicitly drops columns beyond 3:
    `l>3)||…version!=…VERSION_EHEIM&&…version!=…VERSION_DAYTIME` (@978870), and the daytime
    debug fixture DYCL is 4-column: `configuration:[[0,0,0,0],[300,0,0,0],[360,0,50,50],…]` (@847196).
  - The three vendor `.scen` files (4 columns = minute + 3 channels) are therefore consistent
    with the SC20 3-channel model, but the `.scen` files alone would not have settled this —
    the code does.
- **CONFIRMED:** channel column order for SC20 = `[White, Blue, Red]` (see §3).

### Setpoint rules
- **CONFIRMED (@980004):** max **30** setpoints — `alert("Max Light Points: 30!!")`.
- **CONFIRMED:** first row pinned at minute 0, last row pinned at 1440; the editor disables
  time editing for the first and last point (`diabletimechange`), but their channel *values*
  are editable. Editor keeps rows sorted ascending by minute (re-insertion sort on time change).
- **CONFIRMED (@825790):** on receive the UI drops every non-first row whose minute is 0:
  `0!=i&&0==e.configuration[i][0]||t.push(...)` — strong hint the firmware pads its DYCL table
  with `[0,…]` rows up to a fixed size. Firmware table size: **UNKNOWN** (30 is a UI limit).

### On-wire encoding
- **CONFIRMED (@980884):** upload is the same nested JSON array, wrapped in the envelope:
  `{title:"DYCL", from:"USER", to:"ALL-LIGHTS", configuration:[[min,w,b,r],…]}`
  (built at @980884, broadcast by `saveDaycycle` — `e.to="ALL-LIGHTS";t.sendData(e)`).
- **CONFIRMED:** download/response: `{title:"DYCL", from:"<MAC>", configuration:[[…],…]}`;
  request is `{title:"GET_DYCL", to:"MASTER"}`. A `description` string field appears in some
  built-in objects ("Beautiful Reef") but is never required; whether firmware stores it: **UNKNOWN**.

### .scen file format
- **CONFIRMED (@986161):** export = `JSON.stringify(Daycycle.configuration)` downloaded as
  `daycycle.scen` — i.e. the bare nested array, exactly what the vendor files contain.
  Import = `FileReader` + `JSON.parse(...)` straight into `Daycycle.configuration`
  (no validation, no version/channel-count check).

### Interpolation & midnight wrap
- **INFERRED (linear):** the firmware interpolation is not in this bundle, but (a) the UI graph
  (dygraphs) draws straight lines between setpoints and is presented as the authoritative
  preview, (b) the easy-mode generator places intermediate points by *linear* time remapping
  (`s(e,t,n,i,r)` = classic lerp, quoted in easy-mode section), and (c) `PREV-CRV` replays the
  device's own curve which visually matches. Firmware-side confirmation: **UNKNOWN**.
- **Between 1440 and 0:** both endpoints exist as separate rows. All vendor and built-in
  scenarios keep row(0) == row(1440) so the question never arises in practice. What firmware
  does if they differ (jump at midnight vs. wrap-interpolate): **UNKNOWN**.

### DSCRPTN — easy-mode metadata sidecar
- **CONFIRMED (@1093817 serializer, @841504-area parser):** `{title:"DSCRPTN", description:"<string>"}`
  where the string is `confId:<int>;expMode:<true|false>;start:<min>;end:<min>;sunrise:<durMin>;sunset:<durMin>;intensity:<pct>;individual:<true|false>;intensities:<csv pct per channel>`
  (3 CSV values on SC20). It is pure UI metadata (which preset/sliders produced the DYCL),
  stored opaquely by the device and echoed back on `GET_DSCRPTN`. Saving in *either* mode
  always sends **both** DSCRPTN and a full DYCL — the device only ever executes DYCL.
  `confId` = id of the preset curve template (daytime default template id 10: trapezoid
  `[[0,0..],[420,0..],[540,100..],[1080,100..],[1200,0..],[1440,0..]]`); `expMode:true` marks
  a hand-edited expert curve.
- Easy-mode generation (CONFIRMED @798xxx region): the preset template's setpoint times are
  linearly remapped so that sunrise starts at `start`, full brightness at `start+sunrise`,
  dim-down begins at `end-sunset`, off at `end`; intensities scaled by `intensity` (or
  per-channel `intensities` when `individual`), `Math.round`ed. Channels whose colour string
  is not connected are forced to 0 (`0==…wattageAt100Percent[l-1]&&(o[r][l]=0)`).

## 2. CCV — current colour values / manual control

- **CONFIRMED (@846416 fixture, @850057 handler):** device→UI report:
  `{title:"CCV", from:"<MAC>", currentValues:[50,50,50]}` — one integer percent 0–100 per
  channel, order `[White, Blue, Red]` on SC20. Handler: `case"CCV":case"CCV-SL":case"CCV-SW":
  n.setCurrentValues(e.currentValues)`; a value of 0 renders the channel "off"
  (`t[i]={on:0!=e[i],value:e[i]}` @822329). For EHEIM/DAYTIME any elements beyond index 2 are
  ignored (`i>=3||…` @822329) — the device may well send more than 3; UI tolerates it.
- **CONFIRMED (@1024155, @1024089):** the home screen polls `{title:"REQ_CCV",to:"MASTER"}`
  every 2.5 s **while in auto mode** to animate the sliders with the running programme.
- **CONFIRMED:** UI→device *set* uses two title variants with identical payload
  `{title:"CCV-SL"|"CCV-SW", to:"ALL-LIGHTS", from:"USER", currentValues:[w,b,r]}`:
  - `CCV-SL` = **SL**ider drag (default, @823033 `D.title="CCV-SL"`),
  - `CCV-SW` = **SW**itch, i.e. a channel/master on-off toggle (@823050
    `n.changeSW&&(D.title="CCV-SW"…)`; the master light toggle sets `changeSW=true` and sends
    all-0 or all-100).
  Both are sent raw over the WebSocket; only the cloud relay renames them to plain `CCV`
  (`"CCV-Hari"!=a&&"CCV-SW"!=a&&"CCV-SL"!=a||(o.title="CCV")` @1099793). Whether firmware
  distinguishes SL/SW: **UNKNOWN** (receive dispatch treats all three identically).
- **CCV-Hari:** appears only in the cloud rename filter (@1099793) and the cloud-init ignore
  filter (@815547) — the web UI never *constructs* it. **INFERRED:** it is emitted by some
  other producer (firmware itself or a hardware remote/rotary controller) as another CCV
  flavour. Meaning of "Hari": **UNKNOWN**.
- **Manual vs. daycycle mode — CONFIRMED:**
  - Mode packets: `{title:"MAN_MODE", to:"ALL-LIGHTS"}` and `{title:"DAYCL_MODE", to:"ALL-LIGHTS"}`
    (@822761, @826536). Mode query: `{title:"GET_CCMODE", to:"ALL-LIGHTS"}` (@823571); the
    reply is a packet titled `MAN_MODE`/`DAYCL_MODE`, and the current mode is *also* reported
    in the `CLOCK` packet's `mode` field (`mode:"DAYCL_MODE"` in fixture @846122-area).
  - Setting a manual value: UI first sends `MAN_MODE` (only if not already manual), then
    `CCV-SW`/`CCV-SL` with the target values (`setManualModeClients` @823140-area).
  - "Back to auto" = send `DAYCL_MODE`; the lamp resumes the programme.
  - **No timeout / auto-revert found anywhere in the UI.** Firmware-side revert behaviour
    (e.g. at next setpoint or reboot): **UNKNOWN**.
- **Preview packets (do not change mode) — CONFIRMED:**
  - `{title:"PREV-PNT", to:"ALL-LIGHTS"|"<MAC>", currentValues:[…]}` — momentary "show me this
    light now"; the UI streams it at ~1 Hz while a settings screen previews changes. On SC20
    a **4th element is moonlight intensity** (`setPreviewPointClients(...,[0,0,0,e])` @1009252;
    HC uses 7 elements with index 6 = moonlight). `demonstrateLight` sends 7 zeros even on
    daytime (@1087384) — extra elements are clearly harmless.
  - `{title:"PREV-CRV", to:"ALL-LIGHTS", speedFactor:<n>, startTime:<min>, endTime:<min>}`
    (@826414) — fast-forward replay of the programmed curve (`speedFactor:100` = full-day demo);
    device streams `{title:"PREV", time:"[h,m]"}` progress; stop by sending `DAYCL_MODE`.

## 3. The channel model of the SC20

- **CONFIRMED (@813916 area):** daytime build = `VERSION_DAYTIME = 4`, `TANKCONF` default `SALT`,
  `HOST "sc20"`.
- **CONFIRMED (@817861):** `dimmChannelsWithoutMoonlight_DAYTIME = [`
  `{id:0,name:"White",color:"#b9e2fa"}, {id:1,name:"Blue",color:"#2fa8e0"}, {id:2,name:"Red",color:"#e40131"}]`
  — 3 channels; **no moonlight channel is appended for DAYTIME** (unlike HC+, which appends
  id 6 "Moonlight"). Moonlight on SC20 exists only as the virtual 4th value in `PREV-PNT`
  and via the MOON packet.
- **CONFIRMED (@1128984):** localized names/colours: `dimmRB.40`="Weiß/White" (UI colour
  `#000000` in the translation table), `dimmRB.41`="Blau/Blue" `#2fa8e0`, `dimmRB.42`="Rot/Red"
  `#e40131`; variants `dimmRB.4n0/4n1/4n2` = "nicht belegt / not used" `#cccccc` used when no
  connected lamp has that colour (`getEheimLampAppendix` @814696: for DAYTIME returns `"n"`
  for a channel index unless some client has that colour flag; index 0↔white, 1↔blue, 2↔red —
  this **fixes the column order: 0=White, 1=Blue, 2=Red**).
  `lightColor` is `"dimmCW"` for FRESH* tankconfigs and `"dimmRB"` otherwise (@844400-area
  watchGroup) — it only selects which translation-key family (colour theme) is used.
- **CONFIRMED (@846122 fixture):** `USRDTA` fields:
  `name, language, tID, timezone, dst, tankconfig, power, netmode, host, groupID, meshing,
  firstStart, moduleTemp, version, remote, rUID, revision:[webserver,website], firmwareAvailable, liveTime`.
  Daytime fixture: `tankconfig:"DAYTIME", power:21`. `tankconfig`/`power` may arrive as
  JSON-*stringified arrays* (multi-lamp EHEIM case; detected by leading `"["` @839700-area and
  re-stringified on save). Known tankconfig values: `FRESH`, `FRESH_DAYLIGHT`, `FRESH_PLANTS`,
  `SALT`, `MARINE_ACTINIC`, `MARINE_HYBRID`, `DAYTIME`.
- **CONFIRMED (@1069146):** for daytime, `power` is a *code* enumerating which LED colour
  strings are attached (not watts): `9="w"`, `13="r,b"`, `17="r,b,w"`, `21="b"`, `25="b,w"`,
  `29="r"`, `33="r,w"`, each with presence mask `maxWattageAt100Percent=[white,blue,red]`
  (e.g. `name:"r,b" → [0,1,1]`). Non-present channels are zeroed in generated daycycles and
  greyed out in the UI.
- **CONFIRMED (@839847):** the current UI *hard-overrides* this on every USRDTA:
  `n.daytimeUse&&(t.usrdta.power=17, t.color.red=!0, t.color.blue=!0, t.color.white=!0)` —
  i.e. shipping SC20 firmware/UI treats all three channels as always connected.
- **CONFIRMED (@846359):** `MAX_CHANNEL_VALUES {maxPercValue:[100,100,100], wattageAt100:[1,1,1]}`
  exists but is **ignored** for DAYTIME — `maxPercentValue` is pinned to `[100,100,100]`
  (`putMaxChannelValues` @843300-area).

## 4. Effects

### MOON (`GET_MOON` → `MOON`)
- **CONFIRMED (@846616 fixture, @850207 handler, @828349 setter):** fields
  `{maxmoonlight:<0-100>, minmoonlight:<0-100>, moonlightActive:0|1, moonlightCycle:0|1,
    color:"<subset of r,b,w concatenated>", moonStart:<0-1439>, moonEnd:<0-1439>}`
  e.g. `maxmoonlight:30,minmoonlight:2,moonlightActive:1,moonlightCycle:1,color:"br",moonStart:0,moonEnd:1439`.
  Set = same packet with `to:"ALL-LIGHTS"`; sliders: intensities 0–100 step 1,
  moonStart/moonEnd 0–1439 step 10 (@992846). Colour string built in order r→b→w
  (@1012940), so values like `"rbw"`, `"br"`… at least one colour is forced on. `color`
  only exists on daytime (HC has a dedicated moonlight channel instead).
- **Semantics:** `maxmoonlight` = full-moon night intensity, `minmoonlight` = new-moon
  intensity, `moonlightCycle` = simulate lunar cycle. **CONFIRMED (UI model @798572):**
  the moon graph draws a **30-day triangular cycle**: intensity `min + (max-min)/15 * day`
  for day 0–15, mirrored for day 15–30 (`o<15?r*o:r*(30-o)`); with `moonlightCycle:0` every
  night uses `maxmoonlight`. `moonStart`/`moonEnd` = minute-of-day window in which moonlight
  is emitted (on the colour channels named in `color`). How firmware mixes moonlight with the
  daycycle channels (max? add?) and how it tracks the lunar date: **UNKNOWN**.

### CLOUD (`GET_CLOUD` → `CLOUD`)
- **CONFIRMED (@846759 fixture, @850335 handler, @829912 send transform):** wire fields
  `{cloudActive:0|1, maxAmount:<int>, minIntensity:<0-100>, maxIntensity:<0-100>,
    probability:<0-100>, mode:0|1|2, minDuration:<seconds>, maxDuration:<seconds>}`.
- **CRITICAL wire↔UI transforms — CONFIRMED:**
  - Durations: UI is minutes, wire is **seconds** (`minDuration=60*ui` on send @829912;
    `/60` on receive @850335). Daytime UI range 0–30 min, step 1.
  - Intensities are **inverted and swapped**: send: `wire.minIntensity = 100 - ui.maxIntensity`,
    `wire.maxIntensity = 100 - ui.minIntensity`; receive mirrors it
    (`setClouds(e.cloudActive, e.maxAmount, 100-e.maxIntensity, 100-e.minIntensity, …)`).
    So the wire values are the *remaining light level* during a cloud, while the UI slider
    ("Wolkenintensität", 0–100 step 5) expresses cloud *strength*.
- **Semantics (CONFIRMED from translation texts @1162536 area):** random cloud simulation:
  `maxAmount` = maximum clouds per day (daytime UI 0–1500 step 10; HC 0–300);
  `probability` = chance each scheduled cloud actually appears (0–100 %);
  `min/maxDuration` = random duration bounds per cloud; `mode` = multi-lamp transition:
  texts list "synchronous / linked with delay / individual". **INFERRED:** 0=synchronous,
  1=delayed, 2=individual (order of the texts; fixture default 0). No thunderstorm/lightning
  feature exists in this UI.

### ACCLIMATE (`GET_ACCL` → `ACCLIMATE`)
- **CONFIRMED (@846903 fixture, @850980 handler, @827549 sender):** fields
  `{duration:<days>, intensityReduction:<0-100 %>, currentAcclDay:<int>, acclActive:0|1, pause:0|1}`.
  Sliders 0–100 (days / percent) @1013971; defaults offered: freshwater 30 d / 50 %,
  saltwater 60 d / 30 % (@1014669). `currentAcclDay` is maintained by the device (UI only
  displays it and zeroes it when duration changes). Pause/resume via
  `{title:"PAUSE_ACCLIMATION", to:"ALL-LIGHTS", pause:0|1}` (@827836); stop = resend
  ACCLIMATE with `acclActive:0`.
- **Semantics — INFERRED:** new-tank acclimatisation ramp: light output globally reduced by
  `intensityReduction`% at day 0, easing to 0 % reduction over `duration` days. The exact
  per-day curve is firmware-side: **UNKNOWN**.

## 5. Brightness scaling / gamma / power limiting

- **CONFIRMED:** on the SC20 path there is **no gamma and no scaling**: UI percent goes on the
  wire unchanged (`maxPercentValue=[100,100,100]` fixed; `normalizeValue = Math.round(e/t*100)`
  is a no-op at t=100). The elaborate `convertPercentToDisplay` / `oversteer` / 1000-step
  slider machinery only activates for `VERSION_HC_PLUS` where `maxPercValue` can exceed 100.
- **CONFIRMED:** `calculateFreeCapacity` (@822400-area) computes per-channel `maxValue` from a
  shared wattage budget — a real limiter for HC lamps; for DAYTIME `wattageAt100Percent`
  degenerates to the colour-presence mask (`[i,r,o]` booleans @844311), so it effectively
  never limits an SC20 below 100 %.
- Wattage tables (`wattageAt100Percent_*`) feed only the power-consumption display.
- Firmware PWM curve / dithering behind the percent value: **UNKNOWN** (not in this bundle).

## 6. What the three vendor .scen files are

**CONFIRMED:** plain `JSON.stringify` of a DYCL `configuration` for a 3-channel SC20:
rows `[minute, White%, Blue%, Red%]`, day pinned by `[0,…]` and `[1440,…]` rows.
(The filenames "SETUP-Süßwasser…" mark them as freshwater presets.) E.g. `2daycycle`'s
`[430,0,0,80]` = 07:10, White 0 %, Blue 0 %, Red 80 % (dawn red phase).

## 7. Explicitly NOT determined (open questions)

1. Firmware interpolation between setpoints (linear is INFERRED only) and sub-minute resolution.
2. Behaviour across midnight if row(0) ≠ row(1440); firmware DYCL table size / padding rows
   (UI strips zero-minute padding and caps at 30 points, but firmware limits are unverified).
3. Whether firmware treats `CCV`, `CCV-SL`, `CCV-SW`, `CCV-Hari` differently, how many
   elements it sends in `CCV.currentValues` (≥3; UI ignores extras), and what "Hari" means
   (never constructed by this UI — external producer suspected).
4. Manual-mode persistence: no timeout/auto-revert exists in the UI; reboot/next-day firmware
   behaviour unknown.
5. Exact mixing of MOON/CLOUD/ACCLIMATE modulation with the DYCL output (multiplicative?
   clamped? which takes precedence), and the lunar-cycle epoch.
6. CLOUD `mode` numeric mapping (0/1/2 order inferred from UI text order only).
7. Acclimatisation per-day ramp shape.
8. Whether the DYCL `description` field is stored by the device.
9. Whether writes (`DYCL`, `MOON`, …) are ACKed — no ACK handling exists in the UI; it relies
   on re-reading via `GET_*`.
10. Everything firmware-side (PWM/gamma, safety limits) — the bundle is UI-only.

## Appendix: minimal Home-Assistant-relevant packet cheat-sheet

```text
connect:      ws://<ip>/ws  (subprotocol "arduino"); JSON objects; from:"USER" on all sends
poll state:   {"title":"GET_USRDTA"|"GET_CLOCK"|"GET_DYCL"|"GET_DSCRPTN"|"GET_MOON"|
               "GET_CLOUD"|"GET_ACCL"|"GET_NET_ST"|"GET_NET_AP","to":"MASTER","from":"USER"}
              {"title":"REQ_CCV","to":"MASTER","from":"USER"}          -> CCV
keepalive:    {"title":"GET_MESH_NETWORK","to":"MASTER","from":"USER"} every ~3 s
mode:         {"title":"MAN_MODE"|"DAYCL_MODE","to":"ALL-LIGHTS","from":"USER"}
set channels: {"title":"CCV-SL","to":"ALL-LIGHTS","from":"USER","currentValues":[W,B,R]}  (manual mode)
set program:  {"title":"DYCL","to":"ALL-LIGHTS","from":"USER",
               "configuration":[[0,w,b,r],...,[1440,w,b,r]]}   (<=30 rows, sorted, %)
moon:         {"title":"MOON","to":"ALL-LIGHTS","from":"USER","maxmoonlight":30,"minmoonlight":2,
               "moonlightActive":1,"moonlightCycle":1,"color":"rbw","moonStart":0,"moonEnd":1439}
cloud:        {"title":"CLOUD","to":"ALL-LIGHTS","from":"USER","cloudActive":1,"maxAmount":40,
               "minIntensity":100-uiMax,"maxIntensity":100-uiMin,"probability":80,"mode":0,
               "minDuration":sec,"maxDuration":sec}
acclimate:    {"title":"ACCLIMATE","to":"ALL-LIGHTS","from":"USER","duration":30,
               "intensityReduction":50,"currentAcclDay":0,"acclActive":1,"pause":0}
CAUTION: this is a live aquarium controller. Reads are harmless; every write changes
the light over a tank of animals. Use tools/sc20_probe.py, which reverts what it sends.
```
