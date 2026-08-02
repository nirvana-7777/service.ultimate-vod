#!/usr/bin/env python3
"""
Client for the VOD Crawler API
"""

import time
import requests
from typing import Optional, Dict

import xbmc

from settings import get_setting


class CrawlerClient:
    """Client for the VOD Crawler API"""

    def __init__(self, base_url: str = None):
        if base_url is None:
            base_url = get_setting('crawler_url', '')
        self.base_url = base_url.rstrip('/') if base_url else None
        self.session = requests.Session()
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
            response = self.session.get(
                f"{self.base_url}/health",
                timeout=5,
                headers={"User-Agent": "UltimateVOD-Kodi/1.0"}
            )
            self._available = response.status_code == 200
            if self._available:
                xbmc.log("[Ultimate VOD Service] Crawler is available", xbmc.LOGINFO)
        except Exception as e:
            xbmc.log(f"[Ultimate VOD Service] Crawler not available: {e}", xbmc.LOGDEBUG)
            self._available = False

        self._last_check = time.time()
        return self._available

    def get_export(self, since: Optional[str] = None) -> Optional[Dict]:
        """Get library export"""
        if not self.is_available():
            return None

        url = f"{self.base_url}/api/library/export"
        if since:
            url += f"?since={since}"

        try:
            response = self.session.get(url, timeout=30)
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            xbmc.log(f"[Ultimate VOD Service] Export request failed: {e}", xbmc.LOGWARNING)
            return None

    def get_status(self) -> Optional[Dict]:
        """Get crawler status"""
        if not self.is_available():
            return None

        try:
            response = self.session.get(f"{self.base_url}/api/status", timeout=10)
            return response.json() if response.status_code == 200 else None
        except:
            return None

    def trigger_sync(self, provider: Optional[str] = None) -> Optional[Dict]:
        """Trigger a manual sync on the crawler"""
        if not self.is_available():
            return None

        data = {}
        if provider:
            data['provider'] = provider

        try:
            response = self.session.post(
                f"{self.base_url}/api/sync",
                json=data,
                timeout=5
            )
            return response.json() if response.status_code == 200 else None
        except:
            return None
