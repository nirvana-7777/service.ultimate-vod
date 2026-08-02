#!/usr/bin/env python3
"""
Settings helper for service addon
"""

import xbmcaddon

ADDON = xbmcaddon.Addon()


def get_setting(key, default=""):
    try:
        return ADDON.getSetting(key)
    except:
        return default


def get_setting_bool(key, default=False):
    try:
        return ADDON.getSetting(key).lower() == "true"
    except:
        return default


def get_setting_int(key, default=0):
    try:
        return int(ADDON.getSetting(key))
    except (ValueError, TypeError):
        return default


def set_setting(key, value):
    try:
        ADDON.setSetting(key, str(value))
    except:
        pass


def get_addon_path():
    """Get the addon's install path"""
    return xbmcaddon.Addon().getAddonInfo("path")
