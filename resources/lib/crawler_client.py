#!/usr/bin/env python3
"""
Client for the VOD Crawler API
"""

import time
from typing import Optional, Dict

import requests
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover - older bundled urllib3 fallback
    from requests.packages.urllib3.util.retry import Retry

import xbmc

from settings import get_setting

LOG_PREFIX = "[Ultimate VOD Service]"


class CrawlerClient:
    """Client for the VOD Crawler API"""

    def __init__(self, base_url: str = None):
        if base_url is None:
            base_url = get_setting('crawler_url', '')
        self.base_url = base_url.rstrip('/') if base_url else None

        self.session = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=("GET", "POST"),
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({"User-Agent": "UltimateVOD-Kodi/1.0"})

        self._available = None
        self._last_check = 0

    def is_available(self, force_check: bool = False) -> bool:
        """Check if crawler API is reachable"""
        if not self.base_url:
            return False

        now = time.time()
        if not force_check and self._available is not None and (now - self._last_check) < 60:
            return self._available

        try:
            response = self.session.get(f"{self.base_url}/health", timeout=5)
            self._available = response.status_code == 200
            if self._available:
                xbmc.log(f"{LOG_PREFIX} Crawler is available", xbmc.LOGINFO)
            else:
                xbmc.log(f"{LOG_PREFIX} Crawler health check returned {response.status_code}",
                          xbmc.LOGDEBUG)
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Crawler not available: {e}", xbmc.LOGDEBUG)
            self._available = False

        self._last_check = time.time()
        return self._available

    @staticmethod
    def _safe_json(response, context: str) -> Optional[Dict]:
        """Parse a response body as JSON, logging (rather than silently
        swallowing) any decode failure so bad responses are visible in
        kodi.log instead of just looking like a None result."""
        try:
            return response.json()
        except ValueError as e:
            xbmc.log(f"{LOG_PREFIX} {context}: invalid JSON response ({e})", xbmc.LOGWARNING)
            return None

    def get_export(self, since: Optional[str] = None) -> Optional[Dict]:
        """Get library export"""
        if not self.is_available():
            return None

        url = f"{self.base_url}/api/library/export"
        params = {"since": since} if since else None

        try:
            response = self.session.get(url, params=params, timeout=30)
            if response.status_code != 200:
                xbmc.log(f"{LOG_PREFIX} Export request returned {response.status_code}",
                          xbmc.LOGWARNING)
                return None
            return self._safe_json(response, "Export request")
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Export request failed: {e}", xbmc.LOGWARNING)
            return None

    def get_status(self) -> Optional[Dict]:
        """Get crawler status"""
        if not self.is_available():
            return None

        try:
            response = self.session.get(f"{self.base_url}/api/status", timeout=10)
            if response.status_code != 200:
                xbmc.log(f"{LOG_PREFIX} Status request returned {response.status_code}",
                          xbmc.LOGWARNING)
                return None
            return self._safe_json(response, "Status request")
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Status request failed: {e}", xbmc.LOGWARNING)
            return None

    def trigger_sync(self, provider: Optional[str] = None) -> Optional[Dict]:
        """Trigger a manual sync on the crawler"""
        if not self.is_available():
            return None

        data = {}
        if provider:
            data['provider'] = provider

        try:
            response = self.session.post(f"{self.base_url}/api/sync", json=data, timeout=5)
            if response.status_code != 200:
                xbmc.log(f"{LOG_PREFIX} Sync trigger returned {response.status_code}",
                          xbmc.LOGWARNING)
                return None
            return self._safe_json(response, "Sync trigger")
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Sync trigger failed: {e}", xbmc.LOGWARNING)
            return None