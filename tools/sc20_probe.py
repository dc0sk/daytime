#!/usr/bin/env python3
"""Probe a daytime SC20 controller and record its WebSocket traffic.

This is a throwaway diagnostic tool, deliberately independent of the integration and of
Home Assistant, so that a capture can be taken before any of the integration code exists
and re-taken later to check the protocol model has not drifted.

Two phases:

  Phase A (default, read-only)
      Connect, issue every GET_*/REQ_CCV request, log every frame received, and write a
      backup of the device's current configuration. This is exactly what the device's own
      web UI does when you open it, so it changes nothing.

  Phase B (--allow-writes)
      Send a small set of write commands to settle the questions static analysis could not
      answer. Every write is printed and confirmed interactively before it is sent, and the
      original state is restored afterwards from the Phase A snapshot.

Usage:
    python tools/sc20_probe.py --host <ip-of-your-sc20>
    python tools/sc20_probe.py --host <ip-of-your-sc20> --allow-writes

See docs/protocol/README.md for what the recovered protocol looks like.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp

# The device's WebSocket demands this subprotocol (ESP8266 arduinoWebSockets).
SUBPROTOCOL = "arduino"

# Request title -> the response title it should produce.
READ_REQUESTS: list[tuple[str, str]] = [
    ("GET_USRDTA", "USRDTA"),
    ("GET_CLOCK", "CLOCK"),
    ("REQ_CCV", "CCV"),
    ("GET_DYCL", "DYCL"),
    ("GET_DSCRPTN", "DSCRPTN"),
    ("GET_MOON", "MOON"),
    ("GET_CLOUD", "CLOUD"),
    ("GET_ACCL", "ACCLIMATE"),
    ("GET_NET_ST", "NET_ST"),
    ("GET_NET_AP", "NET_AP"),
    ("GET_MESH_NETWORK", "MESH_NETWORK"),
]

# Configuration packets worth snapshotting before any write happens.
BACKUP_TITLES = ["DYCL", "DSCRPTN", "MOON", "CLOUD", "ACCLIMATE", "CCV", "USRDTA", "CLOCK"]

# The web UI paces its sends this far apart; the device is an ESP8266 with ~27 KB of heap.
SEND_INTERVAL = 0.04

CAPTURE_DIR = Path(__file__).resolve().parent.parent / "docs" / "protocol" / "capture"

# Captures get committed to the repository, so identifying and network details are scrubbed
# unless the operator explicitly opts out. Field name -> placeholder.
REDACT_FIELDS = {
    "stSSID": "<redacted-wifi-ssid>",
    "stPW": "<redacted>",
    "apSSID": "<redacted-ap-ssid>",
    "apPW": "<redacted>",
    "ssid": "<redacted>",
    "bssid": "<redacted>",
    "emailAddr": "<redacted>",
    "usrName": "<redacted>",
    "aqName": "<redacted>",
    "rUID": "<redacted>",
    "ip": "<redacted>",
    "gateway": "<redacted>",
    "subaddress": "<redacted>",
}

MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")
SERIAL_RE = re.compile(r"\bSC20_\d+\b")
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def redact(value: Any) -> Any:
    """Scrub identifying details from a frame, recursively.

    MAC addresses are replaced consistently so the addressing structure stays readable:
    a reader can still see that `from` and `clientList[0]` are the same device.
    """
    if isinstance(value, dict):
        return {
            key: REDACT_FIELDS[key] if key in REDACT_FIELDS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = MAC_RE.sub("AA:BB:CC:DD:EE:FF", value)
        value = SERIAL_RE.sub("SC20_0000000", value)
        value = IPV4_RE.sub("0.0.0.0", value)
    return value


class Probe:
    """A single WebSocket session against one controller."""

    def __init__(self, host: str, session: aiohttp.ClientSession) -> None:
        self.host = host
        self._session = session
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader: asyncio.Task[None] | None = None
        # Every frame seen, in order, for the capture log.
        self.frames: list[dict[str, Any]] = []
        # Latest frame per title, for the snapshot.
        self.latest: dict[str, dict[str, Any]] = {}
        # Frames the device broadcast back to us that originated at some client, not the device.
        self.echoes: list[dict[str, Any]] = []
        self._waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}

    async def __aenter__(self) -> Probe:
        url = f"http://{self.host}/ws"
        self._ws = await self._session.ws_connect(url, protocols=(SUBPROTOCOL,))
        self._reader = asyncio.create_task(self._read_loop())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def _read_loop(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type is not aiohttp.WSMsgType.TEXT:
                continue
            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                print(f"  !! non-JSON frame: {msg.data[:200]!r}")
                continue
            # The receive path tolerates either a single object or an array of them.
            for frame in payload if isinstance(payload, list) else [payload]:
                self._handle(frame)

    def _handle(self, frame: dict[str, Any]) -> None:
        title = frame.get("title", "<no title>")
        stamped = {"ts": datetime.now().isoformat(timespec="milliseconds"), **frame}
        # The device broadcasts every frame to all connected WebSocket clients, so a frame
        # marked from:"USER" is another client's command (or our own) coming back at us —
        # never device state. The web UI drops these, and so must the integration.
        if frame.get("from") == "USER":
            stamped["_echo"] = True
            self.frames.append(stamped)
            self.echoes.append(frame)
            return
        self.frames.append(stamped)
        self.latest[title] = frame
        waiter = self._waiters.pop(title, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(frame)

    async def send(self, frame: dict[str, Any]) -> None:
        assert self._ws is not None
        outgoing = {**frame, "from": "USER"}
        self.frames.append(
            {"ts": datetime.now().isoformat(timespec="milliseconds"), "_sent": True, **outgoing}
        )
        await self._ws.send_str(json.dumps(outgoing))
        await asyncio.sleep(SEND_INTERVAL)

    async def request(
        self, title: str, expect: str, *, to: str = "MASTER", timeout: float = 5.0
    ) -> dict[str, Any] | None:
        """Send a request and wait for the frame it should produce."""
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._waiters[expect] = waiter
        await self.send({"title": title, "to": to})
        try:
            return await asyncio.wait_for(waiter, timeout)
        except TimeoutError:
            self._waiters.pop(expect, None)
            return None


async def phase_a(probe: Probe) -> dict[str, Any]:
    """Read-only sweep. Returns the configuration snapshot."""
    print("\n=== Phase A: read-only sweep ===")
    for request, expect in READ_REQUESTS:
        reply = await probe.request(request, expect)
        if reply is None:
            print(f"  {request:20s} -> TIMEOUT (no {expect} within 5 s)")
        else:
            summary = {k: v for k, v in reply.items() if k not in ("title", "to", "from")}
            rendered = json.dumps(summary)
            if len(rendered) > 160:
                rendered = rendered[:157] + "..."
            print(f"  {request:20s} -> {expect}: {rendered}")

    # Listen a little longer to separate genuine device pushes from broadcast echoes.
    print("\n  listening 10 s for unsolicited frames...")
    before = len(probe.frames)
    await asyncio.sleep(10)
    tail = [f for f in probe.frames[before:] if not f.get("_sent")]
    pushes = [f for f in tail if not f.get("_echo")]
    echoes = [f for f in tail if f.get("_echo")]
    for frame in pushes:
        print(f"    device push: {frame.get('title')}")
    for frame in echoes:
        print(f"    echo (from another client): {frame.get('title')} to={frame.get('to')}")
    if not tail:
        print("    (none) -> the device never pushes state; it must be polled")

    return {title: probe.latest[title] for title in BACKUP_TITLES if title in probe.latest}


def report_open_questions(probe: Probe, snapshot: dict[str, Any]) -> None:
    """Answer what the capture can answer about the documented unknowns."""
    print("\n=== What this capture settles ===")

    ccv = snapshot.get("CCV")
    if ccv is not None and isinstance(ccv.get("currentValues"), list):
        values = ccv["currentValues"]
        print(f"  Q2 CCV.currentValues length: {len(values)}  -> {values}")
    else:
        print("  Q2 CCV.currentValues: NOT ANSWERED (no CCV frame received)")

    dycl = snapshot.get("DYCL")
    if dycl is not None and isinstance(dycl.get("configuration"), list):
        rows = dycl["configuration"]
        padding = [i for i, row in enumerate(rows) if i > 0 and row and row[0] == 0]
        print(f"  Q4 DYCL rows: {len(rows)}, width {len(rows[0]) if rows else 0}")
        if padding:
            print(f"     zero-minute padding rows at indices {padding} -> device DOES pad")
        else:
            print("     no zero-minute padding rows -> device does NOT pad")
    else:
        print("  Q4 DYCL padding: NOT ANSWERED (no DYCL frame received)")

    clock = snapshot.get("CLOCK")
    if clock is not None:
        print(f"  current mode (CLOCK.mode): {clock.get('mode')!r}")
        print(f"  CLOCK frame: {json.dumps({k: v for k, v in clock.items() if k != 'title'})}")

    # The device rebroadcasts every client frame to every connected client. An echo whose
    # timing does not line up with our own sends therefore proves a second client was
    # connected at the same time as us.
    foreign = [f for f in probe.echoes if f.get("title") == "GET_MESH_NETWORK"]
    if len(foreign) > 1:
        print(
            f"  Q1 concurrent clients: YES -- saw {len(foreign)} heartbeat echoes from another"
            "\n     client while connected, so the device accepts simultaneous clients"
            "\n     and broadcasts every frame to all of them."
        )
    else:
        print(
            "  Q1 concurrent clients: inconclusive from this run. Re-run with the phone web UI"
            "\n     open and watch for GET_MESH_NETWORK echoes."
        )


async def phase_b(probe: Probe, snapshot: dict[str, Any], assume_yes: bool) -> None:
    """Supervised writes, each reverted afterwards."""
    print("\n=== Phase B: supervised writes ===")

    ccv = snapshot.get("CCV")
    if ccv is None or not isinstance(ccv.get("currentValues"), list):
        print("  ABORT: no CCV snapshot captured, cannot guarantee a safe revert.")
        return
    original_values = list(ccv["currentValues"])
    original_mode = (snapshot.get("CLOCK") or {}).get("mode")
    print(f"  snapshot: mode={original_mode!r} currentValues={original_values}")

    def confirm(description: str, frame: dict[str, Any]) -> bool:
        print(f"\n  About to send: {description}")
        print(f"    {json.dumps({**frame, 'from': 'USER'})}")
        if assume_yes:
            return True
        return input("    send? [y/N] ").strip().lower() == "y"

    # 1. Enter manual mode and observe what the device reports back.
    frame = {"title": "MAN_MODE", "to": "ALL-LIGHTS"}
    if not confirm("switch to manual mode", frame):
        print("  skipped -- nothing was sent, device untouched.")
        return

    # Past this point the lamp is under manual control, so the restore runs no matter what
    # happens next: an exception, a timeout, or Ctrl-C must not leave the tank stranded.
    try:
        await probe.send(frame)
        await asyncio.sleep(1)
        reply = await probe.request("GET_CLOCK", "CLOCK")
        print(f"    CLOCK.mode is now {(reply or {}).get('mode')!r}")
        mode_reply = await probe.request("GET_CCMODE", "MAN_MODE", to="ALL-LIGHTS", timeout=3)
        print(f"    GET_CCMODE replied: {mode_reply}")

        # 2. Re-assert the values the device already had. A real write down the CCV path
        #    that cannot change what the tank looks like.
        frame = {"title": "CCV-SL", "to": "ALL-LIGHTS", "currentValues": original_values}
        if confirm(f"re-send the current values {original_values} (no visible change)", frame):
            await probe.send(frame)
            await asyncio.sleep(1)
            reply = await probe.request("REQ_CCV", "CCV")
            print(f"    CCV now reports: {(reply or {}).get('currentValues')}")
    finally:
        # 3. Always return to the scheduled programme.
        print("\n  Restoring scheduled mode (not optional, runs even on failure).")
        await probe.send({"title": "DAYCL_MODE", "to": "ALL-LIGHTS"})
        await asyncio.sleep(1)
        reply = await probe.request("GET_CLOCK", "CLOCK")
        restored = (reply or {}).get("mode")
        print(f"    CLOCK.mode is now {restored!r}")
        if original_mode is not None and restored != original_mode:
            print(
                f"    !! WARNING: mode is {restored!r}, was {original_mode!r} before this run."
                "\n    !! Send DAYCL_MODE manually, or press 'Automatic' in the device web UI."
            )
        else:
            print("    restored to the state captured in Phase A.")


async def fetch_server_log(session: aiohttp.ClientSession, host: str) -> dict[str, Any]:
    """Scrape /serverLog, the only source for uptime, heap and operating hours."""
    try:
        url = f"http://{host}/serverLog"
        async with session.get(url, timeout=aiohttp.ClientTimeout(10)) as response:
            text = await response.text()
    except (TimeoutError, aiohttp.ClientError) as err:
        return {"error": str(err)}

    plain = re.sub(r"<[^>]+>", "\n", text)
    fields = {
        "uptime_minutes": r"Server Uptime\s*:\s*(\d+)",
        "free_heap": r"Server Heap\s*:\s*(\d+)",
        "operating_hours": r"Operating Hours\s*=\s*(\d+)",
        "firmware_version": r"Firmware Version:\s*(\d+)",
        "webapp_version": r"Webapp Version:\s*(\d+)",
    }
    result: dict[str, Any] = {}
    for key, pattern in fields.items():
        match = re.search(pattern, plain)
        if match:
            result[key] = int(match.group(1))
    return result


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="SC20 IP address or hostname")
    parser.add_argument(
        "--allow-writes",
        action="store_true",
        help="run Phase B, which sends write commands to the device",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the per-write confirmation prompts in Phase B",
    )
    parser.add_argument(
        "--out", type=Path, default=CAPTURE_DIR, help="capture output directory"
    )
    parser.add_argument(
        "--no-redact",
        action="store_true",
        help="write SSIDs, MACs and serials to the capture files verbatim (they are scrubbed"
        " by default because captures are committed to the repository)",
    )
    args = parser.parse_args()
    scrub: Any = (lambda value: value) if args.no_redact else redact

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    async with aiohttp.ClientSession() as session:
        print(f"Probing {args.host}")
        server_log = await fetch_server_log(session, args.host)
        print(f"  /serverLog: {json.dumps(server_log)}")

        async with Probe(args.host, session) as probe:
            snapshot = await phase_a(probe)

            # The backup is what a restore would be driven from, so it is written twice:
            # a redacted copy for the repository, and — unless writes are disabled — the
            # verbatim copy outside it, because a scrubbed backup cannot restore anything.
            backup_path = args.out / f"backup-{stamp}.json"
            backup = {"host": args.host, "serverLog": server_log, "snapshot": snapshot}
            backup_path.write_text(json.dumps(scrub(backup), indent=2))
            print(f"\n  configuration backup -> {backup_path}")
            if not args.no_redact:
                verbatim_path = args.out.parent / f".backup-verbatim-{stamp}.json"
                verbatim_path.write_text(json.dumps(backup, indent=2))
                print(f"  verbatim backup (git-ignored) -> {verbatim_path}")

            report_open_questions(probe, snapshot)

            if args.allow_writes:
                await phase_b(probe, snapshot, args.yes)

            capture_path = args.out / f"capture-{stamp}.jsonl"
            with capture_path.open("w") as handle:
                for frame in probe.frames:
                    handle.write(json.dumps(scrub(frame)) + "\n")
            print(f"\n  frame log ({len(probe.frames)} frames) -> {capture_path}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
