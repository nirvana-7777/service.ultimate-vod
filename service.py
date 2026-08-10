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
        self.sync_interval = get_setting_int('sync_interval', 3600)  # Default 1 hour

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
                # A single bad iteration (e.g. a setting change that fails
                # to reconstruct LibraryManager) must not kill the service
                # loop - there is no supervisor to restart it until the
                # next full Kodi restart.
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

                # Check if crawler is available
                if not self.library_manager.is_enabled():
                    xbmc.log(f"{LOG_PREFIX} Crawler not available, skipping sync", xbmc.LOGWARNING)
                    return

                # Perform sync
                success, message, stats = self.library_manager.sync(show_progress=False)

                if success:
                    self.last_sync = datetime.now().isoformat()
                    set_setting('last_sync_time', self.last_sync)
                    xbmc.log(f"{LOG_PREFIX} Sync complete: {stats}", xbmc.LOGINFO)

                    # Show notification if content was added
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

        # Run in thread to not block
        thread = threading.Thread(target=do_sync, daemon=True)
        thread.start()

    @staticmethod
    def _show_notification(message, icon=xbmcgui.NOTIFICATION_INFO):
        """Show a Kodi notification"""
        try:
            xbmcgui.Dialog().notification("Ultimate VOD", message, icon, 4000)
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Failed to show notification: {e}", xbmc.LOGDEBUG)

    def _check_settings_changed(self) -> bool:
        """
        Check if settings have changed.

        Returns True if sync_interval changed, so the caller can re-anchor
        its schedule.
        """
        new_enabled = get_setting_bool('crawler_enabled')
        new_url = get_setting('crawler_url')
        new_interval = get_setting_int('sync_interval', 3600)

        if new_enabled != self.enabled:
            xbmc.log(f"{LOG_PREFIX} Crawler enabled changed: {new_enabled}", xbmc.LOGINFO)
            self.enabled = new_enabled

        if new_url != self.crawler_url:
            xbmc.log(f"{LOG_PREFIX} Crawler URL changed: {new_url}", xbmc.LOGINFO)
            self.crawler_url = new_url

            # Don't tear down/replace the library manager while a sync is
            # in flight - the old LibraryManager instance may still be
            # mid-write against the cache directory, and swapping it out
            # here would let two instances touch the same files
            # concurrently. Defer the recreation until the current sync
            # (if any) finishes; it will pick up the new URL on the next
            # settings check.
            if not self.is_syncing:
                self.library_manager = self._safe_create_library_manager()
            else:
                xbmc.log(f"{LOG_PREFIX} Deferring LibraryManager recreation until "
                         f"current sync finishes", xbmc.LOGDEBUG)

        interval_changed = new_interval != self.sync_interval
        if interval_changed:
            xbmc.log(f"{LOG_PREFIX} Sync interval changed: {new_interval}s", xbmc.LOGINFO)
            self.sync_interval = new_interval

        return interval_changed


def main():
    """Entry point - handles both service and manual sync modes"""

    # Check if we were called with arguments (e.g., from RunScript)
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

            success, message, stats = manager.sync(show_progress=True)

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
            return  # Exit immediately, do not start the loop!

        elif sys.argv[1] == 'addsource':
            xbmc.log(f"{LOG_PREFIX} Add source triggered via script", xbmc.LOGINFO)
            try:
                manager = LibraryManager()
                success, error_msg = manager.add_source_to_library()

                if success and error_msg == "already_exists":
                    xbmcgui.Dialog().notification(
                        "Ultimate VOD", "Source is already added!", xbmcgui.NOTIFICATION_INFO, 4000
                    )
                elif success:
                    xbmcgui.Dialog().ok(
                        "Ultimate VOD",
                        "Library source added successfully!\n\n"
                        "You can now find 'Ultimate VOD' in your Kodi Video sources. "
                        "You do not need to run this again."
                    )
                    # Trigger a library scan right after adding
                    xbmc.executebuiltin("UpdateLibrary(video)")
                else:
                    # Display the EXACT error Kodi returned
                    xbmcgui.Dialog().ok(
                        "Ultimate VOD",
                        f"Failed to add source automatically.\n\n"
                        f"Kodi reported: {error_msg}\n\n"
                        "Please add it manually:\n"
                        "1. Go to Videos > Files > Add Videos...\n"
                        "2. Browse to your Ultimate VOD cache folder."
                    )
            except Exception as e:
                xbmc.log(f"{LOG_PREFIX} Failed to add source: {e}", xbmc.LOGERROR)
                xbmcgui.Dialog().notification(
                    "Ultimate VOD", "Failed to add source", xbmcgui.NOTIFICATION_ERROR, 4000
                )
            return  # Exit immediately, do not start the loop!

    # Normal startup - start the service. A failure here is caught and
    # logged rather than left to crash the interpreter, since an unhandled
    # exception in service startup silently disables the addon for the
    # rest of the Kodi session with only a traceback in kodi.log.
    try:
        service = UltimateService()
        service.run()
    except Exception as e:
        xbmc.log(f"{LOG_PREFIX} Fatal error, service exiting: {e}", xbmc.LOGERROR)


if __name__ == "__main__":
    main()