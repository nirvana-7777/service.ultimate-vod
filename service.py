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
from datetime import datetime, timedelta

# Add lib to path
ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ADDON_DIR, "resources", "lib"))

import xbmc
import xbmcaddon
import xbmcvfs
import xbmcgui

from crawler_client import CrawlerClient
from library_manager import LibraryManager
from settings import get_setting, get_setting_bool, get_setting_int, set_setting
from monitor import UltimateMonitor


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
        
        # Library manager
        self.library_manager = LibraryManager()
        
        xbmc.log("[Ultimate VOD Service] Initialized", xbmc.LOGINFO)

    def run(self):
        """Main service loop"""
        xbmc.log("[Ultimate VOD Service] Starting service", xbmc.LOGINFO)
        
        # Perform initial sync on startup
        if self.enabled and self.auto_sync:
            self._schedule_sync(delay=5)  # Wait 5 seconds for Kodi to fully start
        
        # Main loop
        next_sync_time = time.time() + self.sync_interval
        
        while not self.monitor.abortRequested():
            # Check if it's time to sync
            if self.enabled and self.auto_sync and time.time() >= next_sync_time:
                self._schedule_sync()
                next_sync_time = time.time() + self.sync_interval
            
            # Check for settings changes (crawler URL, enabled, etc.)
            self._check_settings_changed()
            
            # Sleep a bit
            if self.monitor.waitForAbort(5):
                break
        
        xbmc.log("[Ultimate VOD Service] Service stopped", xbmc.LOGINFO)

    def _schedule_sync(self, delay=0):
        """Schedule a sync in a background thread"""
        if self.is_syncing:
            xbmc.log("[Ultimate VOD Service] Sync already in progress, skipping", xbmc.LOGDEBUG)
            return
        
        def do_sync():
            self.is_syncing = True
            try:
                if delay > 0:
                    xbmc.sleep(delay * 1000)
                
                xbmc.log("[Ultimate VOD Service] Starting scheduled sync", xbmc.LOGINFO)
                
                # Check if crawler is available
                if not self.library_manager.is_enabled():
                    xbmc.log("[Ultimate VOD Service] Crawler not available, skipping sync", xbmc.LOGWARNING)
                    return
                
                # Perform sync
                success, message, stats = self.library_manager.sync(show_progress=False)
                
                if success:
                    self.last_sync = datetime.now().isoformat()
                    set_setting('last_sync_time', self.last_sync)
                    xbmc.log(f"[Ultimate VOD Service] Sync complete: {stats}", xbmc.LOGINFO)
                    
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
                else:
                    xbmc.log(f"[Ultimate VOD Service] Sync failed: {message}", xbmc.LOGWARNING)
                    
            except Exception as e:
                xbmc.log(f"[Ultimate VOD Service] Sync error: {e}", xbmc.LOGERROR)
            finally:
                self.is_syncing = False
        
        # Run in thread to not block
        thread = threading.Thread(target=do_sync, daemon=True)
        thread.start()

    def _show_notification(self, message, icon=xbmcgui.NOTIFICATION_INFO):
        """Show a Kodi notification"""
        try:
            xbmcgui.Dialog().notification(
                "Ultimate VOD",
                message,
                icon,
                4000
            )
        except:
            pass

    def _check_settings_changed(self):
        """Check if settings have changed"""
        new_enabled = get_setting_bool('crawler_enabled')
        new_url = get_setting('crawler_url')
        new_interval = get_setting_int('sync_interval', 3600)
        
        if new_enabled != self.enabled:
            xbmc.log(f"[Ultimate VOD Service] Crawler enabled changed: {new_enabled}", xbmc.LOGINFO)
            self.enabled = new_enabled
        
        if new_url != self.crawler_url:
            xbmc.log(f"[Ultimate VOD Service] Crawler URL changed: {new_url}", xbmc.LOGINFO)
            self.crawler_url = new_url
            # Recreate library manager
            self.library_manager = LibraryManager()

        # Update interval if changed
        if new_interval != self.sync_interval:
            xbmc.log(f"[Ultimate VOD Service] Sync interval changed: {new_interval}s", xbmc.LOGINFO)
            self.sync_interval = new_interval


def main():
    """Entry point - handles both service and manual sync modes"""
    
    # Check if we were called with arguments (e.g., from RunScript)
    if len(sys.argv) > 1 and sys.argv[1] == 'sync':
        xbmc.log("[Ultimate VOD Service] Manual sync triggered via script", xbmc.LOGINFO)
        
        # Perform a one-off sync
        manager = LibraryManager()
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

    # Normal startup - start the service
    service = UltimateService()
    service.run()


if __name__ == "__main__":
    main()
