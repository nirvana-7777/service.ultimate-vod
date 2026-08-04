#!/usr/bin/env python3
"""
Settings helper for service addon
"""

import xbmc
import xbmcaddon

ADDON = xbmcaddon.Addon()


def get_setting(key, default=""):
    try:
        value = ADDON.getSetting(key)
        return value if value is not None else default
    except Exception as e:
        xbmc.log(f"[Ultimate VOD Service] getSetting('{key}') failed: {e}", xbmc.LOGWARNING)
        return default


def get_setting_bool(key, default=False):
    try:
        value = ADDON.getSetting(key)
        if value == "":
            return default
        return value.lower() == "true"
    except Exception as e:
        xbmc.log(f"[Ultimate VOD Service] getSettingBool('{key}') failed: {e}", xbmc.LOGWARNING)
        return default


def get_setting_int(key, default=0):
    try:
        value = ADDON.getSetting(key)
        if value == "":
            return default
        return int(value)
    except (ValueError, TypeError) as e:
        xbmc.log(f"[Ultimate VOD Service] getSettingInt('{key}') invalid value: {e}", xbmc.LOGWARNING)
        return default
    except Exception as e:
        xbmc.log(f"[Ultimate VOD Service] getSettingInt('{key}') failed: {e}", xbmc.LOGWARNING)
        return default


def set_setting(key, value):
    try:
        ADDON.setSetting(key, str(value))
    except Exception as e:
        xbmc.log(f"[Ultimate VOD Service] setSetting('{key}') failed: {e}", xbmc.LOGWARNING)


def get_addon_path():
    """Get the addon's install path"""
    try:
        return xbmcaddon.Addon().getAddonInfo("path")
    except Exception as e:
        xbmc.log(f"[Ultimate VOD Service] getAddonInfo('path') failed: {e}", xbmc.LOGWARNING)
        return ""