"""Descubre receptores Tidal Connect en la red local (mDNS, _tidalconnect._tcp)."""
import asyncio
import logging
from typing import Callable, Optional

from zeroconf import ServiceStateChange
from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

log = logging.getLogger(__name__)

SERVICE = "_tidalconnect._tcp.local."


class Discovery:
    def __init__(self, on_change: Optional[Callable[[], None]] = None):
        self.devices: dict[str, dict] = {}
        self._names: dict[str, str] = {}  # nombre mDNS -> id
        self._on_change = on_change
        self._zc: Optional[AsyncZeroconf] = None
        self._browser: Optional[AsyncServiceBrowser] = None

    async def start(self) -> None:
        self._zc = AsyncZeroconf()
        self._browser = AsyncServiceBrowser(self._zc.zeroconf, [SERVICE], handlers=[self._handler])

    async def stop(self) -> None:
        if self._browser:
            await self._browser.async_cancel()
        if self._zc:
            await self._zc.async_close()

    def _handler(self, zeroconf, service_type, name, state_change) -> None:
        if state_change is ServiceStateChange.Removed:
            dev_id = self._names.pop(name, None)
            if dev_id and self.devices.pop(dev_id, None) is not None and self._on_change:
                self._on_change()
            return
        asyncio.ensure_future(self._resolve(service_type, name))

    async def _resolve(self, service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(self._zc.zeroconf, 3000):
            return
        addrs = info.parsed_addresses()
        v4 = [a for a in addrs if ":" not in a]
        if not (v4 or addrs):
            return
        props = {k.decode(errors="replace"): (v.decode(errors="replace") if v else "")
                 for k, v in (info.properties or {}).items()}
        dev_id = props.get("id") or name
        dev = {
            "id": dev_id,
            "name": props.get("fn") or props.get("mn") or name.split(".")[0],
            "model": props.get("mn"),
            "host": (v4 or addrs)[0],
            "port": info.port,
        }
        changed = self.devices.get(dev_id) != dev
        self.devices[dev_id] = dev
        self._names[name] = dev_id
        if changed:
            log.info("Receptor: %s (%s:%s)", dev["name"], dev["host"], dev["port"])
            if self._on_change:
                self._on_change()
