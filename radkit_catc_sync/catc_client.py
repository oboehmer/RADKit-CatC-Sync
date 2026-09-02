"""Catalyst Center HTTP client."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests
import urllib3
from radkit_common.utils import b64encode  # type: ignore[attr-defined]

from .models import CatCDevice

logger = logging.getLogger(__name__)

_CATC_TOKEN_PATH = "/dna/system/api/v1/auth/token"
_CATC_INVENTORY_PATH = "/api/v1/network-device"


@dataclass
class CatCClient:
    """HTTP client for Catalyst Center API."""

    base_url: str
    username: str
    password: str
    verify_tls: bool = True
    session: requests.Session = field(default_factory=requests.Session)
    _token: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.session.verify = self.verify_tls
        if not self.verify_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    @property
    def hostname(self) -> str:
        """Extract hostname from base_url."""
        return urlparse(self.base_url).hostname or self.base_url

    def authenticate(self) -> None:
        """Obtain API bearer token via Basic Auth."""
        credentials = b64encode(f"{self.username}:{self.password}".encode())
        url = self.base_url.rstrip("/") + _CATC_TOKEN_PATH
        logger.debug("Authenticating to CatC %s", self.hostname)
        resp = self.session.post(
            url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/json",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"CatC auth failed for {self.hostname}: HTTP {resp.status_code}")
        token = resp.json().get("Token")
        if not token:
            raise RuntimeError(f"CatC auth response for {self.hostname} contained no Token")
        self._token = token
        self.session.headers.update({"x-auth-token": self._token})
        logger.debug("Authenticated to CatC %s", self.hostname)

    def get_devices(self) -> list[CatCDevice]:
        """Fetch full paginated device inventory."""
        devices: list[CatCDevice] = []
        offset = 1
        while True:
            url = self.base_url.rstrip("/") + f"{_CATC_INVENTORY_PATH}?offset={offset}"
            logger.debug("Fetching inventory from %s offset=%d", self.hostname, offset)
            resp = self.session.get(url, timeout=60)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Inventory fetch failed for {self.hostname}: HTTP {resp.status_code}"
                )
            raw = resp.json().get("response", [])
            if not raw:
                break
            page_devices = [CatCDevice.from_dict(d) for d in raw]
            devices.extend(page_devices)
            offset += len(page_devices)
            logger.debug("Got %d devices (total so far: %d)", len(page_devices), len(devices))
        return devices
