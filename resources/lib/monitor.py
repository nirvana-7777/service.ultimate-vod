#!/usr/bin/env python3
"""
Kodi monitor for service lifecycle
"""

import xbmc
import xbmcaddon


class UltimateMonitor(xbmc.Monitor):
    """Monitor Kodi lifecycle events"""

    def __init__(self):
        xbmc.Monitor.__init__(self)
        self.addon = xbmcaddon.Addon()
        self.is_aborted = False

    def onSettingsChanged(self):
        """Called when addon settings change"""
        xbmc.log("[Ultimate VOD Service] Settings changed", xbmc.LOGDEBUG)
        # The service will check settings on the next loop iteration
        # via _check_settings_changed()

    def onAbortRequested(self):
        """Called when Kodi is shutting down"""
        xbmc.log("[Ultimate VOD Service] Abort requested", xbmc.LOGINFO)
        self.is_aborted = True
