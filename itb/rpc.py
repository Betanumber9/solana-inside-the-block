"""Small async JSON-RPC client with pacing and retries, plus a websocket slot clock."""
import asyncio
import json
import time

import httpx
import websockets


class RpcError(Exception):
    pass


class Rpc:
    def __init__(self, url, interval=0.12, timeout=30.0, pool=16, heavy_interval=None):
        self.url = url
        self.interval = interval
        # getBlock is rate limited separately and much harder on public endpoints
        self.heavy_interval = interval if heavy_interval is None else heavy_interval
        self._last = 0.0
        self._last_heavy = 0.0
        self._lock = asyncio.Lock()
        self._heavy_lock = asyncio.Lock()
        self._id = 0
        limits = httpx.Limits(max_connections=pool, max_keepalive_connections=pool)
        self.http = httpx.AsyncClient(timeout=timeout, limits=limits, headers={"Content-Type": "application/json"})

    async def close(self):
        await self.http.aclose()

    async def _pace(self):
        async with self._lock:
            wait = self._last + self.interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    async def _pace_heavy(self):
        async with self._heavy_lock:
            wait = self._last_heavy + self.heavy_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_heavy = time.monotonic()

    async def call(self, method, params=None, retries=10, paced=True):
        for attempt in range(retries):
            if paced:
                if method == "getBlock":
                    await self._pace_heavy()
                await self._pace()
            self._id += 1
            body = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}
            try:
                r = await self.http.post(self.url, content=json.dumps(body))
            except httpx.HTTPError as e:
                err = "http: %s" % e
            else:
                if r.status_code == 429 or r.status_code >= 500:
                    err = "status %d" % r.status_code
                else:
                    j = r.json()
                    if "error" not in j:
                        return j["result"]
                    e = j["error"]
                    # -32004/-32007/-32009: block not available yet / skipped / missing in storage
                    if e.get("code") in (-32004, -32014) and attempt < retries - 1:
                        err = e.get("message")
                    else:
                        raise RpcError("%s: %s" % (method, e))
            await asyncio.sleep(min(15.0, 0.5 * 2 ** attempt))
        raise RpcError("%s: gave up (%s)" % (method, err))

    async def send_raw(self, tx_b64, max_retries=0):
        """sendTransaction without pacing: the experiment controls timing. Returns (result, error)."""
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": "sendTransaction",
                "params": [tx_b64, {"encoding": "base64", "skipPreflight": True, "maxRetries": max_retries}]}
        try:
            r = await self.http.post(self.url, content=json.dumps(body))
            j = r.json()
        except Exception as e:  # noqa: BLE001 - recorded, not raised
            return None, "http: %s" % e
        return j.get("result"), j.get("error")


class SlotClock:
    """Latest slot from slotSubscribe, with the local time it arrived. Falls back to polling getSlot."""

    def __init__(self, ws_url, rpc):
        self.ws_url = ws_url
        self.rpc = rpc
        self.slot = 0
        self.seen_at = 0.0  # time.time() when self.slot was first seen
        self.source = "none"
        self._event = asyncio.Event()
        self._task = None

    def _set(self, slot, source):
        if slot > self.slot:
            self.slot = slot
            self.seen_at = time.time()
            self.source = source
            self._event.set()
            self._event = asyncio.Event()

    async def _ws_loop(self):
        while True:
            try:
                async with websockets.connect(self.ws_url, ping_interval=20, max_size=2 ** 22) as ws:
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "slotSubscribe"}))
                    async for msg in ws:
                        j = json.loads(msg)
                        res = j.get("params", {}).get("result")
                        if res and "slot" in res:
                            self._set(res["slot"], "ws")
            except Exception:  # noqa: BLE001 - reconnect, poll meanwhile
                await self._poll_for(5.0)

    async def _poll_for(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                self._set(await self.rpc.call("getSlot", [{"commitment": "processed"}]), "poll")
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.2)

    async def start(self):
        self._task = asyncio.create_task(self._ws_loop())
        self._set(await self.rpc.call("getSlot", [{"commitment": "processed"}]), "poll")

    async def stop(self):
        if self._task:
            self._task.cancel()

    async def wait_until(self, slot, timeout=120.0):
        end = time.monotonic() + timeout
        while self.slot < slot:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError("slot %d not reached (at %d)" % (slot, self.slot))
            try:
                await asyncio.wait_for(self._event.wait(), timeout=min(left, 2.0))
            except asyncio.TimeoutError:
                pass
        return self.slot
