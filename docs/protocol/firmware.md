# daytime SC20 firmware protocol — recovered contract

## 0. Method / provenance (READ THIS FIRST)
- No network contact was made. Only local files were read.
- **Major new evidence source**: the SPIFFS image (`rest.bin`) is genuine SPIFFS (256-byte
  pages, 8192-byte blocks, 32 pages/block, page-0-per-block object-lookup). I reassembled two
  gzip objects from their data pages (5-byte SPIFFS page header, 251-byte payload, span order)
  and decompressed them:
  - `/lib/allJSFiles.js.gz` → **1,170,043 bytes of JS** (the whole AngularJS webapp bundle).
  - `/lib/allCSSFiles.css.gz` → 454,185 bytes CSS (not protocol-relevant).
  Reassembled files written to `scratchpad/fw/allJSFiles.js.gz.hdr5.out` (and `.css`).
  This bundle is the **client half of the exact same protocol**, so most claims below are now
  CONFIRMED from client source, not just string adjacency. Evidence is cited as
  `JS:` (decompressed bundle) or `strings:<line>` (app.strings.txt) or `app.bin@<offset>`.

Confidence labels: CONFIRMED / INFERRED / UNKNOWN as required.

---

## 1. The daycycle "description" string (confId:...;intensities:...)

**This string is the `description` field of the `DSCRPTN` message, NOT the daycycle points.**
CONFIRMED (JS: `description:"confId:"+...easyMode.confId+";expMode:"+...`). It is aquaLEDs'
"easy mode" daycycle summary. The actual per-minute point curve is a SEPARATE message, `DYCL`,
whose `configuration` is `[[minute,ch1,ch2,ch3],...]` (see below).

### Grammar (CONFIRMED from serializer + parser in JS)
Serializer (JS): fixed key order, `;`-separated, `key:value`:
```
confId:<int>;expMode:<bool>;start:<int>;end:<int>;sunrise:<int>;sunset:<int>;intensity:<int>;individual:<bool>;intensities:<int>[,<int>...]
```
Parser (JS, `substring/indexOf` chain — order-sensitive, keys must appear in exactly this order):
| key | type | parse | meaning (INFERRED from names/UI) | range |
|---|---|---|---|---|
| confId | int | raw substring | configuration id / preset slot | UNKNOWN (seen 0 and 4) |
| expMode | bool | `"true"==...` | expert mode flag (point-curve vs easy) | true/false |
| start | int | parseInt | daycycle start, **minutes since midnight** | 0–1439 (INFERRED; ex 300,375,420) |
| end | int | parseInt | daycycle end, minutes since midnight | 0–1439 (INFERRED; ex 1200,1215,1320) |
| sunrise | int | parseInt | sunrise ramp **duration in minutes** | UNKNOWN clamp (ex 120) |
| sunset | int | parseInt | sunset ramp duration in minutes | UNKNOWN clamp (ex 120) |
| intensity | int | parseInt | master light intensity **percent** | 0–100 (INFERRED; ex 100) |
| individual | bool | `"true"==...` | per-channel intensities used vs single | true/false |
| intensities | int list | split(",") map parseInt | per-channel intensity **percent** list | 0–100 each (INFERRED) |

- Number of `intensities` entries = channel count of the model. SC20/`VERSION_DAYTIME`
  default = **3** (`100,100,100`) — CONFIRMED (app.bin@445829 default string; JS DAYTIME default).
  A 7-channel model default `20,20,20,20,20,20,20` also exists in JS (VERSION_HC family).
- **expMode does NOT change the string encoding.** CONFIRMED: the serializer always emits the
  same 9 keys regardless of expMode; expMode is just a flag. When expMode/expert curve is in
  play, the point list travels in the `DYCL` message's `configuration` array, not in this string.
- Default (firmware-embedded, app.bin@445829, strings:6129):
  `confId:4;expMode:false;start:375;end:1215;sunrise:120;sunset:120;intensity:100;individual:false;intensities:100,100,100`
- Other real examples (JS fixtures):
  `confId:0;...;start:300;end:1320;...;intensities:100,100,100`
  `confId:4;...;start:420;end:1200;...;intensities:20,20,20,20,20,20,20`

### The point curve (DYCL.configuration) — CONFIRMED (JS fixture)
`{"title":"DYCL","from":"<mac>","configuration":[[0,0,0,0],[300,0,0,0],[360,0,50,50],[380,29,80,80],[420,95,100,100],[1200,95,100,...],...]}`
Each element = `[minuteOfDay, ch1%, ch2%, ch3%]` (first element is minute, rest are per-channel
percent). CONFIRMED count/order from JS graph builder that pushes `currentValues[i].value` per
point. `compareDaycycle` does `JSON.stringify` equality on `daycycle.configuration`.

UNKNOWN: exact clamp ranges enforced by firmware (no clamp literal seen); meaning of confId
numbering; whether `moonlight` is one of the channels or separate (moon is its own MOON message).

---

## 2. Per-message JSON schemas

Envelope (CONFIRMED): every message is a JSON object with `title` (string), routing fields
`to` and/or `from`. Routing value domain (CONFIRMED JS/strings): `"USER"`, `"MASTER"`, `"ALL"`,
`"ALL-LIGHTS"`, or a device id (BSSID/MAC string like `"5C:CF:7F:74:75:70"`, or `""`).

Directions below: **C→S** = webapp/client → device server; **S→C** = device → client.
Key sets are CONFIRMED where drawn from JS message literals; where only firmware string
adjacency supports them they are marked (adj) with confidence.

| title | dir | keys (besides title/to/from) | evidence |
|---|---|---|---|
| GET_USRDTA / GET_CLOCK / GET_MOON / GET_CLOUD / GET_ACCL / GET_DSCRPTN / GET_DYCL / GET_NET_ST / GET_NET_AP / GET_MESH_NETWORK / GET_CCMODE | C→S | (none; `to` target) | JS getter pairs; strings:6079-6113 |
| REQ_CCV | C→S | (none; `to:"MASTER"`) polled every 2500 ms | JS `send req_ccv` |
| REQ_SCANNED_NETWORKS / GET_SCANNED_NETWORKS | C→S | (none) | JS; strings:6034 |
| USRDTA | S→C (& C→S to set) | name, aqName, language, tID, timezone, dst, tankconfig, power, netmode, host, groupID, meshing, firstStart, moduleTemp, version, remote, rUID, revision[2], firmwareAvailable, latestAvailableRevision, liveTime, emailAddr, usrName, description, **mode** (MAN_MODE/DAYCL_MODE) | JS `title:"USRDTA"` fixture; strings:5890-5911, 5755/5792 |
| CLOCK | S→C / C→S | year, month, day, hour, min, sec, **mode** ("DAYCL_MODE"/"MAN_MODE") | JS `title:"CLOCK"` fixture; strings:5957-5966 |
| DSCRPTN | S→C / C→S | description (the confId:… string, §1) | JS; strings:5919 |
| DYCL | S→C / C→S | configuration ([[min,ch..],…]) | JS; strings:5953-5955 |
| MOON | S→C / C→S | maxmoonlight, minmoonlight, moonlightActive, moonlightCycle, color, moonStart, moonEnd | JS fixture; strings:5945-5952 |
| CLOUD | S→C / C→S | probability, maxAmount, minIntensity, minDuration, maxDuration, cloudActive, mode, (also seen: maxIntensity, minMoon…) | JS fixture; strings:5933-5942 |
| STORE_CLOUD_PARA | C→S | shifting, cMRIntens, duration, endT, startT | strings:5922-5931 (adj, high) |
| ACCLIMATE | S→C / C→S | duration, intensityReduction, currentAcclDay, acclActive, pause | JS fixture; strings:5782-5787 |
| PAUSE_ACCLIMATION | C→S | (none; `to`) | JS `title:"PAUSE_ACCLIMATION"` |
| NET_ST | S→C / C→S | dhcp, ip, gateway, subaddress, stSSID, stPW (bssidST on S→C) | JS fixture; strings:5850-5866 |
| NET_AP | S→C / C→S | apSSID, apPW (bssidST/bssidAP on S→C) | JS fixture; strings:5834-5847 |
| MAX_CHANNEL_VALUES | S→C | maxPercValue[] (per-channel max %, e.g. [100,100,100]) | JS `title:"MAX_CHANNEL_VALUES"` fixture |
| CCV / CCV-SW / CCV-SL / CCV-Hari | both | currentValues[] (array of ints, per-channel %) | JS; strings:5718-5721,6122-6123 |
| MAN_MODE | both | (mode-switch; `to`) | JS `getManualMode`; strings:5792,5882,5957 |
| DAYCL_MODE | both | (mode-switch; `to`) | JS `getDaycycleMode`; strings:5755,5884,5959 |
| CCMODE | S→C | (color-correction mode; see §3) | strings:5797, GET_CCMODE |
| PREV-CRV | C→S | speedFactor, startTime, endTime | JS |
| PREV-PNT | C→S | currentValues[] | JS |
| PREV-FAIR | C→S? | UNKNOWN keys — token exists in firmware only, NOT emitted by this webapp | strings:6119 |
| PREV | S→C | time (JSON-encoded array, `prev=JSON.parse(e.time)`) | JS; strings:5817-5818, app.bin `{"title":"PREV","to":"USER","time":"[` |
| MESH_NETWORK | S→C | clientList | strings:5799-5802 |
| MESH-SETUP | S→C | posNetNo, yourApIp, bssidST, bssidAP, nextBSSID | strings:5820-5826 (adj, med) |
| SERVER_LOG | S→C | logData | strings:5804-5805 |
| SCANNED_NETWORKS_SSID / _BSSID / _STATUS | S→C | ssid / bssid / status | strings:5807-5814 |
| EMAIL_ADDR | S→C | emailAddr | strings:5913-5916 |
| SET_EMAIL_ADDR | C→S | (email) | strings:6114 |
| RESOLVED_BSSID | S→C | name | strings:5922-5924 |
| ADD_CLIENT | C→S | ssid, password (`to:"MASTER"`) | JS |
| IDENTIFY_CLIENT_MESH / IDENTIFY_CLIENT_NET | C→S | ssid, password (`to:"MASTER"`) | JS |
| DISCONNECT_CLIENT / WS_DISCONNECT / SET_WIFI_TURN_OFF | C→S | (control) | JS/strings |
| START_FOTA / START_REMOTE_UPDATE / RESET_TO_DEFAULT / RST / START_OWN_AP / START_CLOUD_CONNECTION / STOP_CLOUD_CONNECTION / STORE_GROUPID / FINISH_ST / FINISH_AP | C→S | (control) | JS/strings |
| KEEP_ALIVE / REQ_KEEP_ALIVE / ACK_KEEP_ALIVE | both | (`to`) heartbeat; `{"title":"REQ_KEEP_ALIVE","to":"ALL"}` literal | app.bin; strings:5788-5791 |
| UPDATE_STATUS | S→C(USER) | currentlyUpdated, totalNumberOfLights | app.bin literal |
| UPDATE_STATUS_CLIENT_PUSH/_POLL | →MASTER | status | app.bin literal |
| REQ_UPDATE_STATUS | →ALL | (none) | app.bin literal |
| FEED_ST | →USER | feedback | app.bin literal |
| SEND_EMAIL | →MASTER | deviceName, type | app.bin literal |

**Request→Response pairing (CONFIRMED, JS array `Receive`):**
`USRDTA↔GET_USRDTA, CCV↔REQ_CCV, CLOCK↔GET_CLOCK, MOON↔GET_MOON, CLOUD↔GET_CLOUD,
ACCLIMATE↔GET_ACCL, DSCRPTN↔GET_DSCRPTN, DYCL↔GET_DYCL, NET_ST↔GET_NET_ST, NET_AP↔GET_NET_AP,
MESH_NETWORK↔GET_MESH_NETWORK`.

**Client inbound dispatch (CONFIRMED, JS `switch(title)`):** USRDTA, MAX_CHANNEL_VALUES,
CCV/CCV-SL/CCV-SW, MAN_MODE, DAYCL_MODE, MOON, CLOUD, CLOCK, DSCRPTN, DYCL, ACCLIMATE, NET_ST,
NET_AP, MESH_NETWORK, PREV, SCANNED_NETWORKS_{SSID,BSSID,STATUS}.

---

## 3. MAN_MODE vs DAYCL_MODE vs CCMODE vs CCV  ← highest-value

**Conclusion (mostly CONFIRMED from JS):**

- **MAN_MODE and DAYCL_MODE are the two mutually-exclusive operating modes of a light.**
  CONFIRMED: `setMode(e){ selectedMode = ("DAYCL_MODE"===e)?0:1; ... client.mode = selectedMode==0 ? getDaycycleMode() : getManualMode() }`. A client object carries exactly one
  `mode` whose `title` is either `"MAN_MODE"` or `"DAYCL_MODE"`.
  - `DAYCL_MODE` (selectedMode 0) = follow the scheduled daycycle programme.
  - `MAN_MODE` (selectedMode 1) = manual override; brightness driven by CCV.
- **To set brightness "now" (the HA use case):** CONFIRMED sequence in
  `setManualModeClients(ids, values)`:
  1. If the light's current `mode.title` != `MAN_MODE`, send `{"title":"MAN_MODE","to":"ALL-LIGHTS"}` (or `to:<id>`) to switch it to manual.
  2. Send `{"title":"CCV-SW","to":"ALL-LIGHTS","currentValues":[v0,v1,v2]}` with the target
     per-channel percentages. (`currentValues` = array of ints, 0–100, one per channel.)
- **To "return to schedule":** CONFIRMED — send `{"title":"DAYCL_MODE","to":"ALL-LIGHTS"}`
  (this is literally what `stopPreview()` does).
- **Reading current mode:** both `CLOCK` and `USRDTA` responses carry a `mode` field equal to
  `"DAYCL_MODE"`/`"MAN_MODE"` (CONFIRMED CLOCK fixture `mode:"DAYCL_MODE"`; strings show
  USRDTA block also carries MAN_MODE/DAYCL_MODE). So after (re)connect the client learns the
  mode from CLOCK/USRDTA.
- **Persistence across reboot:** INFERRED (medium-high) that manual mode persists: the device
  reports `mode` inside its persisted USRDTA/CLOCK payloads and the `/login?CLOCK` mesh API
  echoes it; there is no "reset to daycycle on boot" literal. NOT independently CONFIRMED (no
  EEPROM-write disassembly). Treat as: assume it persists, but verify on a test unit.
- **CCMODE** is a SEPARATE axis = **colour-correction mode**, not the manual/scheduled switch.
  It is fetched via `GET_CCMODE`. It relates to CCV/`MAX_CHANNEL_VALUES` (channel scaling/white
  balance) rather than to on/off scheduling. Exact value domain UNKNOWN (no enum literal found).

**Relationship of CCV to the modes (CONFIRMED):** CCV = "currentValues" = the live per-channel
output levels. Server pushes CCV in response to REQ_CCV (polled 2.5 s). Writing CCV only has a
lasting visible effect while in MAN_MODE; in DAYCL_MODE the schedule keeps overwriting output.
`MAX_CHANNEL_VALUES.maxPercValue[]` caps each channel; UI values are scaled by it.

---

## 4. CCV payload + variant meanings

- **Payload:** `{"title":"CCV"|"CCV-SW"|"CCV-SL"|"CCV-Hari","to":...,"currentValues":[int,…]}`.
  CONFIRMED. `currentValues` is an array of **integers 0–100** (percent), one per dimming
  channel (SC20/DAYTIME = 3; generic default array shown as 7 `[1,1,1,1,1,1,1]`). On receive:
  `{on: value!=0, value}` per channel. CONFIRMED (`setCurrentValues`).
- **Variant semantics (CONFIRMED behaviourally):**
  - The transport normalises all of `CCV-SW`, `CCV-SL`, `CCV-Hari` to plain `CCV` before the
    server processes them: `"CCV-Hari"!=a&&"CCV-SW"!=a&&"CCV-SL"!=a||(o.title="CCV")`. So the
    variant suffix is a **hint to the sender/light about HOW to apply**, collapsed server-side.
  - `CCV-SL` = default write from slider drag; `CCV-SW` = used when the `changeSW` flag is set,
    which is set by the "complete light on/off" action (`o.rootScope.changeSW=!0`). INFERRED
    naming: **SL = "slide/smooth fade", SW = "switch/immediate step"**. Confidence medium.
  - `CCV-Hari` = third variant; **purpose UNKNOWN** (no distinct code path beyond normalisation;
    likely a specific lamp family / legacy). 
- **PREV-* (live preview, C→S):**
  - `PREV-CRV` = curve preview: `{speedFactor,startTime,endTime}` — fast-forward simulate the
    daycycle (CONFIRMED).
  - `PREV-PNT` = point preview: `{currentValues:[…]}` — momentarily show one light point
    (CONFIRMED, `demonstrateLight`).
  - `PREV-FAIR` = token exists in firmware token table (strings:6119) but is **never emitted by
    this webapp**; keys/purpose UNKNOWN. (Guess: "fair-weather"/cloud-free preview — unverified.)
  - Server replies with `PREV` `{time:"[…]"}` (a JSON-stringified array the client `JSON.parse`s
    into `prev`), CONFIRMED.

---

## 5. SPIFFS image (rest.bin) — CONFIRMED SPIFFS, file list recovered

- CONFIRMED SPIFFS: 256-byte log pages, 8192-byte blocks (32 pages/block), object-lookup page
  = page 0 of each block, data page header = 5 bytes `{obj_id u16, span_ix u16, flags u8}` then
  251-byte payload, object-index-header page has top bit of obj_id set (0x8000) and stores
  `size u32` + `type u8` + `name[]`. Verified by reassembling & decompressing two gzip objects.
- **211 distinct name strings** recovered. The webapp is AngularJS + Angular-Material. Served
  content is **minified & gzip-bundled**; the individual `com/**/**.ctrl.js` names are only
  `<script src>` references inside `index.htm` (dev layout), the real code is in the two bundles.
  Actual stored objects of interest:
  - `/index.htm` (256 B index header; templates below are stored uncompressed as SPIFFS objects)
  - `/lib/allJSFiles.js.gz`  (obj_id 0x0001, stored size 329,721 B; **decompresses to 1,170,043 B JS** — the entire protocol client) ← recovered
  - `/lib/allCSSFiles.css.gz` (obj_id 0x0002, stored 50,871 B → 454,185 B CSS)
  - `/website.revision`, `/favicon.ico`, `/favicon.png`
  - HTML templates (plaintext in image): `/com/dc/t/{daycycle,dceasy,prevDialog,saveas}.tpl.html`,
    `/com/home/t/{home,manual,automatic,addDevice,removeDevice}.html`,
    `/com/effects/t/{accl,clouds,moon,effects}.html`,
    `/com/is/t/{daycyclesetup,dctimes,acclsetup,cloudssetup,moonsetup,lampcon,lightcolor,lightsTable,network,networkis,networksetup,language,setupfinish}.html`,
    `/com/set/t/{settings,nwsettings,statusCtrl,websiteupdate}.html`,
    `/com/index/t/{navbar,endbar,graph,moonGraph,initsetupnav}.html`,
    `/com/sliders/t/lampselect.html`, `/com/finish/t/finishup.html`
  - many `/i/*.svg`, `/images/*.svg|png` assets.
- **Non-minified source that reveals protocol?** The bundled JS, once decompressed, is the
  authoritative protocol source (used throughout this report). No separate cleartext config file
  was found; there is no server-side secret/default file in SPIFFS. `/website.revision` holds the
  webapp revision string only.
- Reassembly was easy for the two gzip bundles; a full FS rebuild was NOT needed and NOT done.

---

## 6. Authentication / tokens / passwords

- **WebSocket API is UNAUTHENTICATED.** CONFIRMED: client opens `new WebSocket("ws://<host>/ws",
  ["arduino"])` with no token/cookie/credential; no auth challenge title exists; the only
  "password" fields anywhere are **WiFi SSID passwords** (apPW/stPW/ADD_CLIENT.password). No
  per-message auth, nonce, or signature. (There IS a `KEEP_ALIVE` handshake but it is liveness,
  not auth.)
- **`/login?<COMMAND>` mesh HTTP API is also effectively open** — `Login OK - Welcome in Mesh!`
  is returned; the "login" is a mesh-join concept, not a credential gate. No password compared.
  (No password literal found on this path.) Confidence high but mark INFERRED for "no check".
- **`Authorization: Basic` string** (app.bin, in the block with `x-ESP8266-*`, `User-Agent`,
  `ESP8266-http-Update`) belongs to the **outbound `ESP8266HTTPUpdate`/`HTTPClient`** that fetches
  firmware from `data.daytime.de`, **NOT** to the device's own server. CONFIRMED by surrounding
  strings (all standard ESP8266HTTPClient header tokens). No embedded credential value was seen
  (the header name is present; the value is built at runtime and none is hard-coded in strings).

---

## 7. Revision scheme + update URLs

- **Revision is a 2-int array** `revision:[a,b]` in USRDTA (`revision:[21,15]` in JS fixture);
  compared element-wise against `latestAvailableRevision:[…]`; `firmwareAvailable` (0/1) flag.
  INFERRED: `[0]` and `[1]` are two independent counters (server/firmware vs webapp). The webapp
  computes an integer form by `value%10` decomposition. Exact meaning of the two slots UNKNOWN.
- Local endpoints: `GET /website.revision` (webapp revision string on device), page shows
  `Webapp Version:` and `Firmware Version:` (served by `/` and `/serverLog`).
- **Update URLs (CONFIRMED, app.bin literals):**
  - Combined OTA container: `http://data.daytime.de/Firmware-Files/Firmware-Combined/firmware_daytime.sc20`
  - Firmware image: `http://data.daytime.de/Firmware-Files/Firmware-Server/main.daytime_ino.bin`
  - Webapp image: `http://data.daytime.de/Firmware-Files/Firmware-Website/main.daytime_spiffs.bin`
  - Revision manifests: `http://data.daytime.de/Revision-Status/Revision-Status-Server/revisionServer.js`
    and `.../Revision-Status-Website/revisionWebsite.js`
  - OTA upload paths on device: `POST /update` (multipart, field `update`); filename suffix
    `.daytime_ino` = firmware, `.daytime_spiffs` = webapp. `/formateeprom`, `/formatspiffs`.

---

## 8. HTTP surface (device server) — recap/verify
`/`, `/index.htm`, `/heap`, `/update` (GET form + POST OTA), `/formateeprom`, `/formatspiffs`,
`/serverLog`, `/connectioncheck`, `/website.revision`, `/setup`, `/login`,
`/kindle-wifi/wifistub.html`, `/ws` (WebSocket, subprotocol `arduino`). All CONFIRMED (app.bin).
`/login?IDENTIFY|CONNECT_TO|CCV|NET_AP|NET_ST|USRDTA|ACCLIMATION|CLOUD_SIM|MOON|DYCL|CLOCK|
GET_NODE|ERASE_NODE` mesh sub-API CONFIRMED (app.bin), responses `text/json`/`text/plain`.

---

## 9. What I could NOT determine (open questions)
1. Firmware-side clamp/validation ranges for every daycycle field (start/end/sunrise/sunset/
   intensity) — no clamp literals in binary; ranges above are INFERRED from examples.
2. `confId` numbering semantics (why 0 vs 4; is it a preset table index?).
3. `CCMODE` value domain and the exact CCV colour-correction maths (relation to maxPercValue).
4. `CCV-Hari` purpose; `PREV-FAIR` keys/purpose (firmware-only tokens).
5. Definitive proof MAN_MODE persists across reboot (strongly implied, not disassembled).
6. Whether SC20 has a dedicated moonlight channel beyond the 3 dimming channels (MOON is its own
   message; channel-to-hardware mapping not in strings).
7. Meaning of the two `revision[]` slots and the `tID`/`version`(=4=DAYTIME) coupling.
8. Absolute confirmation that `/login` and `/ws` perform NO credential check (inferred from
   absence of any password-compare literal on those paths).

## PLAN (execution step, pending approval)
Write the content of this file to
`/tmp/claude-1000/-home-dc0sk-git-daytime/d983524a-8a7f-49e9-a401-2ade84194cb1/scratchpad/PROTO-firmware.md`
(minus this plan note / header). No other changes. The reassembled bundle
`scratchpad/fw/allJSFiles.js.gz.hdr5.out` is left in place as supporting evidence.
