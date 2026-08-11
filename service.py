#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ultimate VOD Service
Background service for library sync
"""

import sys
import os
import time
import threading
from datetime import datetime

# Add lib to path
ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ADDON_DIR, "resources", "lib"))

import xbmc
import xbmcaddon
import xbmcgui

from library_manager import LibraryManager
from settings import get_setting, get_setting_bool, get_setting_int, set_setting
from monitor import UltimateMonitor

LOG_PREFIX = "[Ultimate VOD Service]"


class UltimateService:
    """Main service class"""

    def __init__(self):
        self.addon = xbmcaddon.Addon()
        self.monitor = UltimateMonitor()

        # Get settings
        self.crawler_url = get_setting('crawler_url')
        self.enabled = get_setting_bool('crawler_enabled')
        self.auto_sync = get_setting_bool('auto_sync')

        # Track raw interval (in minutes) for change detection, but use
        # floored interval (in seconds) for scheduling.
        self._raw_sync_interval = get_setting_int('sync_interval', 60)
        interval_seconds = self._raw_sync_interval * 60
        self.sync_interval = max(60, interval_seconds) if interval_seconds > 0 else 3600

        # State
        self.is_syncing = False
        self.last_sync = get_setting('last_sync_time') or None

        # Library manager. If construction fails (bad path, permissions,
        # etc.) we log and keep running with library_manager=None rather
        # than letting the exception propagate out of __init__ and killing
        # the service before the main loop even starts - a bad setting is
        # something the user can fix via _check_settings_changed(), but
        # only if the service is still alive to notice the fix.
        self.library_manager = self._safe_create_library_manager()

        xbmc.log(f"{LOG_PREFIX} Initialized", xbmc.LOGINFO)

    @staticmethod
    def _safe_create_library_manager():
        try:
            return LibraryManager()
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Failed to create LibraryManager: {e}", xbmc.LOGERROR)
            return None

    def run(self):
        """Main service loop"""
        xbmc.log(f"{LOG_PREFIX} Starting service", xbmc.LOGINFO)

        # Automatically ensure the library source is added to Kodi on startup.
        if self.library_manager:
            self.library_manager.add_source_to_library()

        # Perform initial sync on startup
        if self.enabled and self.auto_sync:
            self._schedule_sync(delay=5)  # Wait 5 seconds for Kodi to fully start

        # Main loop
        next_sync_time = time.time() + self.sync_interval

        while not self.monitor.abortRequested():
            try:
                # Check if it's time to sync
                if self.enabled and self.auto_sync and time.time() >= next_sync_time:
                    self._schedule_sync()
                    next_sync_time = time.time() + self.sync_interval

                # Check for settings changes (crawler URL, enabled, etc.)
                interval_changed = self._check_settings_changed()
                if interval_changed:
                    # Re-anchor the schedule to the new interval immediately
                    # rather than waiting for the previously-scheduled sync
                    # to fire on the old cadence first.
                    next_sync_time = time.time() + self.sync_interval

            except Exception as e:
                xbmc.log(f"{LOG_PREFIX} Unexpected error in service loop: {e}", xbmc.LOGERROR)

            # Sleep a bit
            if self.monitor.waitForAbort(5):
                break

        xbmc.log(f"{LOG_PREFIX} Service stopped", xbmc.LOGINFO)

    def _schedule_sync(self, delay=0):
        """Schedule a sync in a background thread"""
        if self.is_syncing:
            xbmc.log(f"{LOG_PREFIX} Sync already in progress, skipping", xbmc.LOGDEBUG)
            return

        if self.library_manager is None:
            xbmc.log(f"{LOG_PREFIX} No library manager available, skipping sync", xbmc.LOGWARNING)
            return

        def do_sync():
            self.is_syncing = True
            try:
                if delay > 0:
                    xbmc.sleep(delay * 1000)

                xbmc.log(f"{LOG_PREFIX} Starting scheduled sync", xbmc.LOGINFO)

                if not self.library_manager.is_enabled():
                    xbmc.log(f"{LOG_PREFIX} Crawler not available, skipping sync", xbmc.LOGWARNING)
                    return

                success, message, stats = self.library_manager.sync(show_progress=False)

                if success:
                    self.last_sync = datetime.now().isoformat()
                    set_setting('last_sync_time', self.last_sync)
                    xbmc.log(f"{LOG_PREFIX} Sync complete: {stats}", xbmc.LOGINFO)

                    if stats.get('added', 0) > 0:
                        self._show_notification(
                            f"Added {stats['added']} items to library",
                            xbmcgui.NOTIFICATION_INFO
                        )
                    elif stats.get('deleted', 0) > 0:
                        self._show_notification(
                            f"Removed {stats['deleted']} items from library",
                            xbmcgui.NOTIFICATION_INFO
                        )

                    if stats.get('errors', 0) > 0:
                        self._show_notification(
                            f"Sync finished with {stats['errors']} item error(s), see log",
                            xbmcgui.NOTIFICATION_WARNING
                        )
                else:
                    xbmc.log(f"{LOG_PREFIX} Sync failed: {message}", xbmc.LOGWARNING)

            except Exception as e:
                xbmc.log(f"{LOG_PREFIX} Sync error: {e}", xbmc.LOGERROR)
            finally:
                self.is_syncing = False

        thread = threading.Thread(target=do_sync, daemon=True)
        thread.start()

    @staticmethod
    def _show_notification(message, icon=xbmcgui.NOTIFICATION_INFO):
        try:
            xbmcgui.Dialog().notification("Ultimate VOD", message, icon, 4000)
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Failed to show notification: {e}", xbmc.LOGDEBUG)

    def _check_settings_changed(self) -> bool:
        """
        Check if settings have changed.
        Returns True if sync_interval changed, so the caller can re-anchor its schedule.
        """
        new_enabled = get_setting_bool('crawler_enabled')
        new_url = get_setting('crawler_url')
        new_interval = get_setting_int('sync_interval', 60)

        if new_enabled != self.enabled:
            xbmc.log(f"{LOG_PREFIX} Crawler enabled changed: {new_enabled}", xbmc.LOGINFO)
            self.enabled = new_enabled

        if new_url != self.crawler_url:
            xbmc.log(f"{LOG_PREFIX} Crawler URL changed: {new_url}", xbmc.LOGINFO)
            self.crawler_url = new_url

            if not self.is_syncing:
                self.library_manager = self._safe_create_library_manager()
            else:
                xbmc.log(f"{LOG_PREFIX} Deferring LibraryManager recreation until "
                         f"current sync finishes", xbmc.LOGDEBUG)

        # Compare against the raw setting (in minutes) to detect actual user changes
        interval_changed = new_interval != self._raw_sync_interval
        if interval_changed:
            xbmc.log(f"{LOG_PREFIX} Sync interval changed: {new_interval} min", xbmc.LOGINFO)
            self._raw_sync_interval = new_interval
            # Convert minutes to seconds and apply floor validation
            interval_seconds = new_interval * 60
            self.sync_interval = max(60, interval_seconds) if interval_seconds > 0 else 3600

        return interval_changed


def main():
    """Entry point - handles both service and manual sync modes"""

    if len(sys.argv) > 1:
        if sys.argv[1] == 'sync':
            xbmc.log(f"{LOG_PREFIX} Manual sync triggered via script", xbmc.LOGINFO)

            try:
                manager = LibraryManager()
            except Exception as e:
                xbmc.log(f"{LOG_PREFIX} Failed to create LibraryManager for manual sync: {e}",
                         xbmc.LOGERROR)
                xbmcgui.Dialog().notification(
                    "Ultimate VOD", "Sync failed: could not initialize", xbmcgui.NOTIFICATION_ERROR, 4000
                )
                return

            success, message, stats = manager.sync(show_progress=True, force_full=True)

            if success:
                xbmcgui.Dialog().notification(
                    "Ultimate VOD",
                    f"Sync complete: {stats.get('added', 0)} added",
                    xbmcgui.NOTIFICATION_INFO,
                    4000
                )
            else:
                xbmcgui.Dialog().notification(
                    "Ultimate VOD",
                    f"Sync failed: {message}",
                    xbmcgui.NOTIFICATION_ERROR,
                    4000
                )
            return

    try:
        service = UltimateService()
        service.run()
    except Exception as e:
        xbmc.log(f"{LOG_PREFIX} Fatal error, service exiting: {e}", xbmc.LOGERROR)


if __name__ == "__main__":
    main()