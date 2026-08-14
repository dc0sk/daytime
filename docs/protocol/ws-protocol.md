
# daytime SC20 / aquaLEDs "Light-Symphony" — WebSocket protocol reference

Reverse-engineered from the device web UI only. NO hardware was contacted.
Sources (both in the scratchpad):
- `allJSFiles.js` — minified bundle (byte offsets cited as `@NNN`).
- `app.beauty.js` — beautified application code (line numbers cited as `L:NNN`).
Evidence labels: **CONFIRMED** (quoted code), **INFERRED** (reasoning), **UNKNOWN**.

Note on demo data: the UI has an offline-debug mode with a hard-coded mock frame set
(`allJSFiles.js @846000–848200`, guarded by `debug_controllerWithoutLampVersion==4`). It
literally lists one example of every inbound title. These examples are CONFIRMED to exist
in code but are DEMO VALUES, not live device output — treat concrete numbers as "shape and
plausible value", not as guaranteed device behaviour.

---

## 1. Transport, handshake, authentication

**CONFIRMED** WebSocket URL and subprotocol (`@1102581`, L:~ startWebsocket):
```
var t=new WebSocket("ws://"+e+"/ws",["arduino"])
```
- Scheme `ws://` (plaintext), path `/ws`, subprotocol `"arduino"` (ESP arduinoWebSockets).
- `e` is the host (IP or mDNS name). Default hostnames by product: `n.HOST="sc20"` for the
  daytime build, `"eheimledcontrolplus"` for eheim, `"lighting"` otherwise (`@... L:631`).

**CONFIRMED** onmessage handler (`@1102... startWebsocket`):
```
t.onmessage=function(e){var t=JSON.parse(e.data);n.rootScope.receiveData(t)}
```
Every inbound frame is a JSON text frame parsed and passed to `receiveData`.

**Authentication / session / handshake: CONFIRMED ABSENT on the WebSocket.**
- There is no token, login, nonce, or challenge anywhere in the connect path. `onopen`
  immediately registers the socket (`setWebsock`) and the app starts requesting data.
- The only pre-WS check is an HTTP `GET /connectioncheck?r=<rand>` via JSONP
  (`connectionCheck`, connection service) and an HTTP `GET /connectioncheck` liveness probe.
  These are reachability probes, not auth. (**CONFIRMED**, connection service slice.)
- A separate *cloud* transport exists (Heroku relay, see §7) which is keyed by `mac`+`uid`
  in the URL query string — that is the closest thing to a credential, and it is only used
  for the cloud path, never for the LAN WebSocket. (**CONFIRMED** `@815222`,
  `"https://aqualed.herokuapp.com/device/aquaria/"+this.mac+".json?uid="+this.uid`.)

**INFERRED**: A Home Assistant integration can open `ws://<host>/ws` with subprotocol
`arduino` and immediately send/receive JSON with no auth step.

---

## 2. Message envelope & addressing model

### 2.1 Envelope

Every message is a flat JSON object with a `title` string. **CONFIRMED**.

Outbound (UI→device): `connection.sendData(e)` sets `e.from="USER"`, JSON-stringifies, and
pushes to a send buffer. **CONFIRMED** (`@814563` template; connection service):
```
sendData:function(e){e.from="USER";var t=JSON.stringify(e); ... this.buffer.push(t) ...}
```
So *every* outbound frame carries `from:"USER"`. Most also carry `to` and `title`, set by
the caller. Some carry payload fields.

Inbound frames carry `from` = the originating client's BSSID (a MAC-like string, e.g.
`"5C:CF:7F:74:75:70"`). **CONFIRMED** (every mock frame, `@846000+`). The dispatcher
ignores anything whose `from` is `"USER"` (i.e. it never processes its own echoes):
```
switchPackets=function(e){ if("USER"!=e.from){ ... } else printLLog("From other user:"...) }
```
(**CONFIRMED** L:1448-1489.)

Inbound can be a single object OR an array of objects; arrays are iterated and each element
dispatched. **CONFIRMED** L:1444-1447:
```
receiveData=function(e){ ... if(e instanceof Array) for(...) n.switchPackets(e[r]); else n.switchPackets(e)}
```

### 2.2 `to` address vocabulary — CONFIRMED (all observed literal values)

| `to` value | Meaning | Evidence |
|---|---|---|
| `"MASTER"` | the mesh master node (routes requests) | heartbeat & all GET_* requests, `@1101...`, L:928,1020 |
| `"ALL-LIGHTS"` | broadcast to every light in the mesh | L:851,857,865,885,911,942,959,965,986,992,1010,1014,1038,1053,1057,1072... |
| `"ALL"` | broadcast incl. non-light? (used for wifi-off, disconnect) | L:1014 `SET_WIFI_TURN_OFF to:"ALL"`; L:1331 `DISCONNECT_CLIENT to:"ALL"` |
| `<bssid>` | a specific client, e.g. `"5C:CF:7F:74:75:70"` | `getClientData` sets `to`=id; `demonstrateLight` `to:this.bssid` |
| `"USER"` | used as `to` in a few UI-internal templates (DSCRPTN) | L:917,921 (these are UI-side seed objects, not necessarily sent) |
| `"USER"` (as `from`) | added to every outbound frame | §2.1 |

**INFERRED**: The device is a **mesh**. One node is MASTER; requests to `"MASTER"` are
answered and/or routed; state-changing commands are usually broadcast `"ALL-LIGHTS"` so
every node applies them; per-node reads (`GET_USRDTA`) are addressed to a specific bssid.

### 2.3 Client identity — CONFIRMED

- A "client" is a physical light node keyed by its **BSSID** (MAC). `getClientId()` returns
  `this.bssid`. **CONFIRMED** `@... "getClientId=function(){return this.bssid}"`.
- The mesh roster arrives as `MESH_NETWORK.clientList` (array of bssid strings). The UI
  treats the **last** entry as the master: `n.masterClientId=e.clientList[e.clientList.length-1]`
  (**CONFIRMED** L:1467). For each bssid in the list not yet known, the UI sends
  `GET_USRDTA` addressed to that bssid (`setMeshNetwork`→`getClientData`, **CONFIRMED**
  `@1086...`).

### 2.4 Virtual clients (tankconfig/power arrays) — CONFIRMED

When a single physical node drives multiple lamps (eheim use), `usrdta.tankconfig` and
`usrdta.power` are **arrays** rather than scalars; the UI splits one real client into N
"virtual clients", each carrying one `tankconfig[i]`/`power[i]`. **CONFIRMED**
(`getVirtualClients`/`addVirtualClient`/`removeVirtualClient`, L:4042 region):
```
getVirtualClients: if(usrdta.tankconfig instanceof Array && usrdta.power instanceof Array){
  for(n=0..tankconfig.length){ o=angular.copy(e); o.usrdta.tankconfig=tankconfig[n];
  o.usrdta.power=power[n]; o.parent=e; o.id=n; ...} return t } return [e]
```
- Over the wire, when arrays are present they are transported as **JSON strings**: on
  receive, if `tankconfig[0]==='['` and `power[0]==='['` the UI does
  `JSON.parse(e.tankconfig)` / `JSON.parse(e.power)` (**CONFIRMED** L:1303). On send,
  `saveClientData` does `t.tankconfig=JSON.stringify(t.tankconfig)` /
  `t.power=JSON.stringify(t.power)` before sending (**CONFIRMED** L:1306-1308 &
  `@... n.prototype.saveClientData`). So an array-valued `tankconfig`/`power` field is a
  **stringified JSON array**, while a scalar is a bare value.
- For the **daytime** build the UI overrides `power` to the scalar `17` and forces
  RGB channels on after receiving USRDTA (`n.daytimeUse&&(t.usrdta.power=17,...)`,
  **CONFIRMED** L:1303) — so on SC20, virtual clients are effectively not used. **INFERRED**.

---

## 3. Inbound dispatch — every `title` handled by `receiveData`/`switchPackets`

Source: `switchPackets` switch, **CONFIRMED** L:1448-1489 (minified `@849631`, cases
`@849849`+). Any title not listed hits `default:` → logs "No JSON match found" (ignored).

Legend for the switch (verbatim handler calls):

| Inbound `title` | Handler | Payload fields consumed | Notes |
|---|---|---|---|
| `CLOCK` | `setClockByNumber(year,month,day,hour,min,sec)` then `setMode(mode)` | `year,month,day,hour,min,sec` (ints), `mode` (string) | month is 1-based on the wire; UI does `new Date(y,month-1,...)`. **CONFIRMED** L:1453,1114. |
| `USRDTA` | `putUSRDATA(e)` | see §5 (whole object) | per-client config/state. **CONFIRMED** L:1455,1300. |
| `MAX_CHANNEL_VALUES` | `putMaxChannelValues(e)` | `maxPercValue` (int[], per-channel %), `wattageAt100` (num[]) | caps + wattage model. **CONFIRMED** L:1457,1309; mock `@846...`. |
| `CCV` / `CCV-SL` / `CCV-SW` | `setCurrentValues(e.currentValues)` | `currentValues` (int[] per channel) | current channel values (live intensities). **CONFIRMED** L:1459. |
| `MAN_MODE` | `setMode("MAN_MODE")` | (title only) | node is in manual mode. **CONFIRMED** L:1461. |
| `DAYCL_MODE` | `setMode("DAYCL_MODE")` | (title only) | node is in daycycle/auto mode. **CONFIRMED** L:1461. |
| `MOON` | `setMoonlight(maxmoonlight,minmoonlight,moonlightActive,moonlightCycle,color,moonStart,moonEnd)` | `maxmoonlight,minmoonlight` (int %), `moonlightActive,moonlightCycle` (0/1), `color` (string of chars r/b/w), `moonStart,moonEnd` (minutes 0..1439) | **CONFIRMED** L:1463; mock `@846...` `color:"br"`. |
| `CLOUD` | `setClouds(cloudActive,maxAmount,100-maxIntensity,100-minIntensity,probability,mode,minDuration/60,maxDuration/60)` | `cloudActive`(0/1), `maxAmount`(int), `minIntensity,maxIntensity`(int, INVERTED by UI: displayed=100−wire), `probability`(int %), `mode`(int), `minDuration,maxDuration`(seconds on wire; UI÷60→minutes) | **CONFIRMED** L:1465; mock `minDuration:600,maxDuration:1500`. |
| `MESH_NETWORK` | `receivedHeartbeat()`, `setMeshNetwork(e)`, `masterClientId=clientList[last]` | `clientList` (bssid string[]) | heartbeat ACK + roster; last = master. **CONFIRMED** L:1467; mock `@847...`. |
| `PREV` | `setPrev(e)` | `time` (JSON-string of `[a,b]`) | preview curve point; `n.prev=JSON.parse(e.time)`. **CONFIRMED** L:1469,961. |
| `NET_ST` | `setNET_ST(e)` | `dhcp`(0/1), `ip,gateway,subaddress`(int[4]), `stSSID,stPW`(string) | station (client-WiFi) settings. **CONFIRMED** L:1471,1077; mock `@847...`. |
| `NET_AP` | `setNET_AP(e)` | `apSSID,apPW`(string) [`asMaster` on send] | access-point settings. **CONFIRMED** L:1473,1044; mock `apSSID:"#aquaLEDs_Lighting"`. |
| `SCANNED_NETWORKS_SSID` | `setScannedNetworks_SSID(e)` | `ssid` (string[]) | WiFi scan result names. **CONFIRMED** L:1475,1060. |
| `SCANNED_NETWORKS_BSSID` | `setScannedNetworks_BSSID(e)` | `bssid` (string[][], each `[mac]`) | scan BSSIDs; UI flags aquaLEDs APs by `A0:B1:CD`/`A0:B1:BD`(daytime)/`A0:B1:DD`(eheim) prefix. **CONFIRMED** L:1477,1063-1070. |
| `SCANNED_NETWORKS_STATUS` | `setScannedNetworks_STATUS(e)` | `status` (int[][], each `[rssi,channel]`) | mock `[[-80,8],[-80,7]]`. **CONFIRMED** L:1479,1072. |
| `DYCL` | `setDaycycle(e)` | `configuration` (int[][]) | daycycle curve; each row `[minuteOfDay, ch0, ch1, ch2, ...]`. **CONFIRMED** L:1481,930; mock rows `[300,0,0,0]…[420,95,100,100]…`. |
| `DSCRPTN` | `setDaycycleDescription(e)` | `description` (string; `;`-separated key:val, see §4.4) | daycycle description/params. **CONFIRMED** L:1483,923; mock `@847...`. |
| `ACCLIMATE` | `setAcclimatization(duration,intensityReduction,currentAcclDay,acclActive,pause)` | `duration`(days,int), `intensityReduction`(int %), `currentAcclDay`(int), `acclActive`(0/1), `pause`(0/1) | **CONFIRMED** L:1485,979; mock `duration:30,intensityReduction:50,currentAcclDay:1`. |

**Also referenced but NOT in the switch** (so inbound handling UNKNOWN/none): `PREV-PNT`,
`PREV-CRV`, `GET_CCMODE`. These are only ever *sent*. `MAX_CHANNEL_VALUES` has no `GET_`
request in `allPacketsToReceive` — **INFERRED** it is pushed with/after `USRDTA`.

---

## 4. Outbound messages — every `sendData(...)` call

All go through `connection.sendData`, which adds `from:"USER"`. Enumerated from every
`sendData(` call site (grep of `app.beauty.js`). Fields listed are those set by the caller.

### 4.1 Polling / requests (device→answers with the paired inbound title)

Request/response table (**CONFIRMED** `@1104580`, connection `allPacketsToReceive`):
`[["USRDTA","GET_USRDTA"],["CCV","REQ_CCV"],["CLOCK","GET_CLOCK"],["MOON","GET_MOON"],
["CLOUD","GET_CLOUD"],["ACCLIMATE","GET_ACCL"],["DSCRPTN","GET_DSCRPTN"],["DYCL","GET_DYCL"],
["NET_ST","GET_NET_ST"],["NET_AP","GET_NET_AP"],["MESH_NETWORK","GET_MESH_NETWORK"]]`

| Outbound `title` | Fields | Purpose / trigger | Evidence |
|---|---|---|---|
| `GET_MESH_NETWORK` | `to:"MASTER"` | heartbeat every 3000 ms; returns `MESH_NETWORK` | **CONFIRMED** `@1101035` startHeartbeat |
| `GET_USRDTA` | `to:<bssid>` | per-client config read; sent for each new bssid in clientList | **CONFIRMED** `@1086...` getClientData |
| `REQ_CCV` | `to:"MASTER"` | poll live current values every 2500 ms while in auto mode | **CONFIRMED** `@1024...` L:comment "send req_ccv" |
| `GET_CLOCK` | `to:"MASTER"` | request clock | **CONFIRMED** `@1104676` |
| `GET_MOON` | `to:"MASTER"` | request moonlight | **CONFIRMED** L:? `@1104697` |
| `GET_CLOUD` | `to:"MASTER"` | request cloud config | **CONFIRMED** L:1019 |
| `GET_ACCL` | `to:"MASTER"` | request acclimatization | **CONFIRMED** `@1104744` |
| `GET_DSCRPTN` | `to:"MASTER"` | request daycycle description | **CONFIRMED** L:927 |
| `GET_DYCL` | `to:"MASTER"` | request daycycle curve | **CONFIRMED** `@1104790` |
| `GET_NET_ST` | `to:"MASTER"` | request station net cfg | **CONFIRMED** `@1104812` |
| `GET_NET_AP` | `to:"MASTER"` | request AP net cfg | **CONFIRMED** `@1104836` |
| `GET_CCMODE` | `to:"ALL-LIGHTS"` (code sets `to=e[n]` then overwrites to `"ALL-LIGHTS"`) | request each client's mode | **CONFIRMED** L:866-871 |
| `GET_SCANNED_NETWORKS` | `to:"MASTER"` | trigger WiFi scan; returns SCANNED_NETWORKS_* | **CONFIRMED** `@1053452` L:? |

The initial full read is driven by `checkAllPacketsAreReceived` (connection service): once
the first inbound packet arrives, a 2000 ms timer (`timerReRequestMissingPackets:2e3`) walks
`allPacketsToReceive` and (re)sends the `GET_*` request for any response title not yet in
`receivedPackets`, repeating until all are present. **CONFIRMED** (connection `t(e)` fn).

### 4.2 State-changing commands

| Outbound `title` | Full field set | Units / ranges | Evidence |
|---|---|---|---|
| `CCV-SL` / `CCV-SW` | `to`(bssid/`"ALL-LIGHTS"`), `currentValues`(int[]) | per-channel intensity %; UI clamps via `calculateFreeCapacity`; **range UNKNOWN exactly** (see §6). `CCV-SW` when `changeSW` set, else `CCV-SL` | **CONFIRMED** L:832-851,911 |
| `CCV-Hari` | (referenced only in cloud collapse) | — | **CONFIRMED** cloud path `@... "CCV-Hari"!=a`; never sent from LAN code path in reviewed slices |
| `MAN_MODE` | `to` | (title only) — set node to manual | **CONFIRMED** L:838,896,911 |
| `DAYCL_MODE` | `to` | (title only) — set node to auto/daycycle; also used to stop preview | **CONFIRMED** L:901,942,959 |
| `CLOCK` | `to:<clientId>`, `year,month,day,hour,min,sec` | **month is sent +1** (`t.month=t.month+1`), i.e. 1-based on wire | **CONFIRMED** L:853-855 saveClock |
| `MOON` (moonlight save) | `to`, `maxmoonlight,minmoonlight`(int %), `moonlightActive,moonlightCycle`(0/1), `color`(string r/b/w), `moonStart,moonEnd`(minutes 0..1439) | client `.moonlight` object serialized | **CONFIRMED** L:1005-1010; template `@... this.moonlight` |
| `CLOUD` (cloud save) | `to`, `cloudActive`(0/1), `maxAmount`(int), `minIntensity,maxIntensity`(int — SENT INVERTED: `minIntensity=100-maxIntensity`, `maxIntensity=100-minIntensity`), `probability`(int %), `mode`(int), `minDuration,maxDuration`(SECONDS — UI ×60 before send), `from` | inversion + minutes→seconds on send | **CONFIRMED** L:1031-1038 sendClientsClouds |
| `ACCLIMATE` | `to:"ALL-LIGHTS"`, `duration`(int days), `intensityReduction`(int %), `currentAcclDay`(int), `acclActive`(0/1), `pause`(0/1) | all parseInt/0-1 coerced | **CONFIRMED** L:983-986 sendAcclimatization |
| `PAUSE_ACCLIMATION` | `to:"ALL-LIGHTS"`, `pause`(0/1) | **CONFIRMED** L:989-992 |
| `USRDTA` (config write) | whole `usrdta` object (see §5); `to`=client id, `from:"USER"`; if arrays, `tankconfig`/`power` stringified | write-back of user data | **CONFIRMED** L:1306-1308 saveClientData & `@... prototype.saveClientData` |
| `DYCL` (daycycle write) | `to`, `configuration`(int[][]) | **INFERRED**: daycycle is written via the daycycle object; setter uses `h.setDaycycle`. Exact send site: UNKNOWN in reviewed slices — see §8 |
| `DSCRPTN` (description) | `to`, `description`(string) | seed templates exist (L:917,921); explicit send site UNKNOWN — see §8 |
| `NET_AP` (write) | `to`, `apSSID`,`apPW`,`asMaster:true`, `from` | AP config write | **CONFIRMED** L:1048-1053 setClientsNetAp |
| `FINISH_AP` | `to:"ALL-LIGHTS"` | commit AP setup | **CONFIRMED** L:1055-1057 |
| `NET_ST` (write) | `to`, `dhcp`(0/1), `ip,gateway,subaddress`(int[4], split from dotted string), `stSSID,stPW` | station config write | **CONFIRMED** L:1084-1091 setClientsNetSt |
| `FINISH_ST` | `to:"ALL-LIGHTS"` | commit station setup | **CONFIRMED** L:1093-1095 |
| `ADD_CLIENT` | `to:"MASTER"`, `ssid`, `password` | tell master to adopt a new client by its AP creds | **CONFIRMED** L:1097-1101 |
| `IDENTIFY_CLIENT_NET` | `to:"MASTER"`, `ssid`, `password` | identify a client over WiFi | **CONFIRMED** L:1102-1106 |
| `IDENTIFY_CLIENT_MESH` | `to:<bssid>` | blink/identify a client in the mesh | **CONFIRMED** L:1107-1111 |
| `DISCONNECT_CLIENT` | `to:"ALL"`, `bssid` | remove/disconnect a client | **CONFIRMED** L:1329-1333 |
| `SET_WIFI_TURN_OFF` | `to:"ALL"`, `turnOff:true` | turn off WiFi radio | **CONFIRMED** L:1012-1015 |
| `START_FOTA` | `to:"ALL-LIGHTS"` | start firmware OTA update | **CONFIRMED** L:1370-1373 (`@845125`) |
| `PREV-PNT` | `to:"ALL-LIGHTS"` or `to:<bssid>`, `currentValues`(int[]) | preview a single set of channel values (light demo) | **CONFIRMED** L:841-845, `demonstrateLight` `to:this.bssid` |
| `PREV-CRV` | `to:"ALL-LIGHTS"`, `speedFactor`(num), `startTime,endTime`(minutes) | run a time-lapse preview of the daycycle | **CONFIRMED** L:949-956,963-966; `simulateDaycycle` uses `speedFactor:100,startTime:0,endTime:1440` |

### 4.3 `demonstrateLight` sequence — CONFIRMED L:856-865
Sends `MAN_MODE`, then `CCV-SW to:"ALL-LIGHTS"` with all-zero `currentValues` (length =
`maxPercentValue`), then `CCV-SW to:<e>` with all values = 20. Shows the ordering that a
"demo/identify light" performs.

### 4.4 `DSCRPTN.description` grammar — CONFIRMED (L:917,921; mock `@847...`)
Semicolon-separated `key:value` string. Keys observed:
```
confId:<int>; expMode:<bool>; start:<min>; end:<min>; sunrise:<min>; sunset:<min>;
intensity:<0-100>; individual:<bool>; intensities:<csv of per-channel ints>
```
Example (HC 7 channels): `confId:4;expMode:false;start:420;end:1200;sunrise:120;sunset:120;intensity:100;individual:false;intensities:20,20,20,20,20,20,20`.
daytime/eheim use 3 channels → `intensities:20,20,20`. `start`/`end`/`sunrise`/`sunset` are
minutes. Parsed by `setDaycycleDescriptionEasyMode` (L:4042: `substring(...indexOf("intensities:")+12)...split(",")...parseInt`).

---

## 5. `usrdta` — full field inventory

Two independent sources agree:

(a) **Live-shape mock** (`@846060`, `debug_controllerWithoutLampVersion==4`) — **CONFIRMED**
a literal `USRDTA` frame:
```
{title:"USRDTA", from:"5C:CF:7F:74:75:70", name:"Light_7632240", language:"DE",
 tID:30, timezone:60, dst:1, tankconfig:"DAYTIME", power:21, netmode:"AP",
 host:"lighting", groupID:0, meshing:1, firstStart:0, moduleTemp:0, version:4,
 remote:0, rUID:"0", revision:[21,15], firmwareAvailable:0, liveTime:100}
```

(b) **`putUSRDATA` consumption** (**CONFIRMED** L:1300-1303) reads these fields:
`from`, `tankconfig`, `power` (scalar or stringified-array), `language`, `dst`, `tID`,
`timezone`, `version`, `moduleTemp` (only when version==HC_PLUS), `remote`,
`firmwareAvailable` (→ `setFota`), `firstStart` (→ `setFirstStart`), `liveTime`,
`revision`, `latestAvailableRevision`.

Field reference:

| Field | Type | Meaning / range | Evidence |
|---|---|---|---|
| `title` | string | `"USRDTA"` | mock |
| `from` | string | client BSSID (source id) | mock; L:1302 `findClient(e.from)` |
| `name` | string | user-set light name (`setClientName`) | mock; `@... prototype.setClientName` |
| `language` | string | UI language, e.g. `"DE"` | mock; L:1303 `n.language=usrdta.language` |
| `tID` | int | timezone list id (index into 66-entry table §5.1) | mock `30`; L:1303 `n.timezone=[tID,timezone]` |
| `timezone` | int | UTC offset in **minutes** (e.g. 60 = GMT+1) | mock; timezone table values |
| `dst` | 0/1 | daylight saving active | mock; L:1303 `n.dst=1==usrdta.dst` |
| `tankconfig` | string OR stringified-int-array | tank/light config code, e.g. `"DAYTIME"`,`"SALT"`,`"FRESH"`,`"FRESH_DAYLIGHT"`,`"FRESH_PLANTS"`,`"MARINE_ACTINIC"` | mock; L:1303,1335 |
| `power` | int OR stringified-int-array | wattage of the lamp; SC20 forced to `17` in UI | mock `21`; L:1303 |
| `netmode` | string | `"AP"` or station | mock `"AP"`; also `n.mode="AP"` default |
| `host` | string | mDNS host, e.g. `"lighting"` | mock |
| `groupID` | int | mesh group id | mock `0` |
| `meshing` | int(0/1) | mesh enabled | mock `1`; default `n.MESH=1` |
| `firstStart` | 0/1 | true on factory-fresh; routes UI to `/setup` | mock `0`; L:1368 setFirstStart |
| `moduleTemp` | int | module temperature (°C, **INFERRED unit**); used only for HC_PLUS | mock `0`; L:1303 |
| `version` | int | lamp platform: 0=UNDEFINED,1=HC,2=HC_PLUS,3=EHEIM,4=DAYTIME,100=HC_PLUS_INTEGRATED | mock `4`; L:631 constants |
| `remote` | int(0/1) | remote/cloud control flag; UI force-sets 1 when cloud+websocket | mock `0`; L:1303, setRemote |
| `rUID` | string | remote UID (cloud), e.g. `"0"` | mock |
| `revision` | int[2] | `[webserverRev, websiteRev]`; displayed as `0X.Y` via `getWebserverVersion`/`getWebsiteVersion` (`rev[0]`=webserver, `rev[1]`=website) | mock `[21,15]`; L:4042 |
| `latestAvailableRevision` | int[2] | newest available fw; `[-1,-1]` ⇒ update server unreachable | L:1304-1305 checkForUpdateVersions |
| `firmwareAvailable` | int(0/1) | fota available flag (→ setFota) | mock `0`; L:1303 |
| `liveTime` | int | uptime in **minutes** (UI does `/60` to hours) | mock `100`; L:1303 |
| `to` | string | set by UI on write-back = client id | L:1303 `t.usrdta.to=e.from` |
| `from` (write) | `"USER"` | set on send | L:1303 |
| `uid` / `mac` | (top-level app, cloud) | device MAC + cloud uid; NOT part of the usrdta frame per se — used to key cloud URL | `@815222`; L:634-636 |

### 5.1 Timezone table — CONFIRMED L:1121-? (66 entries, id 0..65)
`n.timezones=[{id,name,value}]` where `value` = UTC offset **minutes**. Examples: id 30 =
"(GMT+01:00) Amsterdam, Berlin, Bern, Rome, Stockholm, Vienna", value 60. This is the
lookup for `usrdta.tID`/`usrdta.timezone`. (default `n.TZONE=1`, `n.dst=true` L:631.)

---

## 6. Channel model, caps, and value ranges

- `MAX_CHANNEL_VALUES.maxPercValue` is a per-channel array of percentage caps (mock
  `[100,100,100]`), and `wattageAt100` a per-channel wattage-at-full array (mock `[1,1,1]`).
  **CONFIRMED** mock `@846...`, consumed L:1309-1320.
- daytime/eheim have **3 channels** (R,B,W); HC/HC_PLUS have **7**. Seen in
  `intensities:20,20,20` (3) vs `20,20,20,20,20,20,20` (7). **CONFIRMED** L:917 vs 921.
- Channel values in `CCV*`/`currentValues` and daycycle rows are integers; mock shows
  `[50,50,50]` and daycycle amplitudes up to `100`. **INFERRED** range 0..100 (percent),
  but a hard clamp was NOT located — `calculateFreeCapacity` (L:824) and `maxPercValue`
  gate them but the exact min/max is **UNKNOWN** (see §8). Do NOT assume 0..100 as a proven
  bound.
- Daycycle `configuration` rows: `[minuteOfDay(0..1440), ch0, ch1, ch2, ...]`. First column
  is minute-of-day (mock rows `0,300,360,...,1440`). **CONFIRMED** mock `@847...`. The UI
  compresses rows (drops leading zero-time duplicates) on receive (L:930-935).

---

## 7. Cloud path (context; not the LAN protocol)

**CONFIRMED** (connection `dataSendFunction`, `establishCloudConnection`, `@815222`).
An alternative transport relays the *same JSON frames* through Heroku:
- `PUT https://aqualed.herokuapp.com/device/aquaria/<mac>.json?uid=<uid>` with body
  `{from_controller:<0|1>, settings:[<frame>]}`. `from_controller=1` if a websocket is also
  connected. **CONFIRMED**.
- On the cloud path the CCV title variants collapse: `"CCV-Hari"|"CCV-SW"|"CCV-SL"` →
  `"CCV"` before sending. **CONFIRMED** `@... dataSendFunction` and mock note.
- Cloud send pacing uses `sendIntervalToCloud:500` ms instead of the LAN `sendinterval:40`.
- A HA integration should ignore the cloud path entirely and speak LAN WebSocket.

---

## 8. Pacing, ordering, ACK behaviour

- **Send pacing — CONFIRMED** (`@1098947`): `sendinterval:40` (ms). `sendData` buffers
  frames; `dataSendFunction` sends `buffer[0]` then `setTimeout(shift+recurse, 40)`. So
  **outbound frames are serialized ≥40 ms apart** on LAN (500 ms on cloud). A HA integration
  should rate-limit writes to ≤1 per ~40 ms.
- **Heartbeat — CONFIRMED** (`@1101035`): every `heartbeatInterval:3e3` ms send
  `{title:"GET_MESH_NETWORK",to:"MASTER"}`. A `heartbeatTimeout` of `3×3000 = 9000` ms
  without a `MESH_NETWORK` reply triggers `closeWebsock()`+`reconnect()`.
  `receivedHeartbeat()` (called on any `MESH_NETWORK`) clears the timeout. So **any
  `MESH_NETWORK` frame is the liveness ACK**; there is no per-message ACK.
- **Re-request loop — CONFIRMED**: `timerReRequestMissingPackets:2e3` — after the first
  packet, every 2 s it re-sends `GET_*` for any of the 11 tracked responses not yet seen,
  until all present. This is the app's "initial sync". An integration should send all
  `GET_*` once, then reconcile.
- **Ordering constraints — INFERRED / mostly none**: The buffer preserves FIFO order.
  Setup flows imply ordering: `NET_AP`(write)→`FINISH_AP`; `NET_ST`(write)→`FINISH_ST`.
  Mode+values: some setters send `MAN_MODE`/`DAYCL_MODE` *before* the `CCV*` (L:911,942).
  No explicit per-command ACK or sequence number exists. **CONFIRMED absence of seq/ack.**
- **Reconnect — CONFIRMED**: on timeout, `reconnect()` re-probes `/connectioncheck`, and
  after `maxReconnectCount:2` failures may fall back to cloud; a page reload timer
  (`timerReloadText:15000`, `timerReloadPage:60000`) reloads the UI if fully offline.

---

## 9. What could NOT be determined (open questions)

1. **Exact numeric ranges/clamps** for channel intensities (`currentValues`, daycycle
   amplitudes). 0..100 is strongly implied by percentages but **no hard clamp was located**.
2. **Explicit send sites for `DYCL` and `DSCRPTN` writes** were not found in the reviewed
   slices (only the seed templates and receive handlers). The daycycle is edited via a
   `daycycle` object + `setDaycycle`; the actual frame that writes a new curve to the device
   (title, whether `DYCL` or a `SET_*`) is **UNKNOWN** — needs a search of the daycycle
   editor controller (grep `configuration:` senders / the graph editor save button).
3. **`CCV-Hari` semantics** — appears only in the cloud-collapse list; when/whether it is
   emitted on LAN is UNKNOWN. Likely a legacy/hardware variant name.
4. **`MAX_CHANNEL_VALUES` request** — there is no `GET_` for it; how the device is prompted
   to send it (piggybacked on USRDTA? unsolicited?) is INFERRED, not confirmed.
5. **`groupID`, `meshing`, `netmode`, `rUID`, `host`** write semantics — read confirmed;
   whether the UI ever writes them back and what the device does with changes is UNKNOWN.
6. **`moduleTemp` unit** (assumed °C) and **`liveTime` unit** (minutes, from `/60`) — the
   latter is INFERRED from a divide-by-60; the former is UNKNOWN.
7. **Server-side validation / error frames** — no error/NACK title was observed. Whether the
   device replies on malformed input is UNKNOWN (and untestable here per the safety rule).
8. **Whether `to:"ALL"` vs `to:"ALL-LIGHTS"` differ on the device** — only inferred from
   which commands use which.

---

## 10. Quick-start for the Home Assistant integration (derived, INFERRED)

1. Open `ws://<host>/ws`, subprotocol `arduino`. No auth.
2. Send `{"title":"GET_MESH_NETWORK","to":"MASTER"}` every 3 s; treat any `MESH_NETWORK`
   as alive; 9 s silence ⇒ reconnect.
3. On first `MESH_NETWORK`, for each bssid send `{"title":"GET_USRDTA","to":"<bssid>"}`,
   and send `GET_CLOCK/GET_MOON/GET_CLOUD/GET_ACCL/GET_DSCRPTN/GET_DYCL/GET_NET_ST/GET_NET_AP`
   (all `to:"MASTER"`) and `REQ_CCV` (`to:"MASTER"`). Re-send any missing after 2 s.
4. For live intensity, poll `REQ_CCV` (to MASTER) ~2.5 s while in auto mode.
5. Rate-limit all writes to ≥40 ms apart; add `from:"USER"` to every frame.
6. To set manual levels: send `MAN_MODE` (to target) then `CCV-SW`/`CCV-SL` with
   `currentValues` (to `ALL-LIGHTS` or a bssid). To return to schedule: `DAYCL_MODE`.
7. Remember wire encodings: CLOCK month is +1; CLOUD intensities inverted & durations in
   seconds; array `tankconfig`/`power` are stringified JSON.

=== END PROTO-ws.md CONTENT ===

## Next action (needs approval to leave plan mode)
Copy the block between the `=== PROTO-ws.md CONTENT ===` markers to
`scratchpad/PROTO-ws.md`. No device contact, no other file changes.
