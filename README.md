# Ultimate VOD Service

> Background service addon for Kodi that automatically synchronizes video library content from a VOD Crawler API, keeping your Kodi library up to date with minimal user intervention.

## Features

- 🔄 Automatic library synchronization
- 📈 Incremental updates (only changed content is synced)
- 🔒 Cross-process file locking for safe concurrent execution
- 💾 Atomic file operations to prevent corrupted `.strm` files
- ⚡ Manual sync trigger
- ⏰ Configurable sync interval
- 🔔 Kodi notifications
- 🔄 Lightweight background service
- 🌍 Cross-platform support (Linux, macOS, Windows, Android, Raspberry Pi)

---

# Requirements

| Component              | Version             |
|------------------------|---------------------|
| Kodi                   | v18 (Leia) or newer |
| Python                 | 3.0+                |
| script.module.requests | 2.31.0+             |

---

# Installation

## From Repository

1. Add the repository source in Kodi.
2. Navigate to **Add-ons → Install from Repository**.
3. Locate **Ultimate VOD Service** under **Services**.
4. Click **Install**.

## Manual Installation

1. Download the latest release.
2. In Kodi select **Add-ons → Install from ZIP file**.
3. Select the downloaded ZIP.
4. Configure the addon.

---

# Configuration

After installation:

```
Settings
 └── Add-ons
      └── My Add-ons
           └── Services
                └── Ultimate VOD Service
```

## Settings

| Setting              | Description                      | Default                                                   |
|----------------------|----------------------------------|-----------------------------------------------------------|
| Crawler API URL      | URL of the VOD crawler           | http://localhost:7888                                     |
| Enable Library Sync  | Enable automatic synchronization | false                                                     |
| Auto Sync on Startup | Sync when Kodi launches          | true                                                      |
| Sync Interval        | Sync interval in seconds         | 3600                                                      |
| Library Cache Path   | Storage location for STRM files  | `special://profile/addon_data/service.ultimate-vod/cache` |

---

# Architecture

## Required API Endpoints

| Endpoint              | Method | Description            |
|-----------------------|--------|------------------------|
| `/health`             | GET    | Health check           |
| `/api/library/export` | GET    | Export library         |
| `/api/status`         | GET    | Status information     |
| `/api/sync`           | POST   | Manual synchronization |

---

## Export Format

```json
{
  "type": "full",
  "timestamp": "2024-01-01T00:00:00Z",
  "movies": [],
  "episodes": [],
  "deleted": []
}
```

---

# File Structure

```
cache/
├── .sync.lock
├── Movies/
│   └── {provider}/
│       └── {Title} ({Year}) ({ID}).strm
└── TV Shows/
    └── {Show Title}/
        └── Season {NN}/
            └── S{NN}E{NN} - {Title} ({ID}).strm
```

## Naming Convention

Example movie:

```
Movies/Netflix/Inception (2010) (movie_123).strm
```

Example TV show:

```
TV Shows/Stranger Things/Season 01/S01E01 - Chapter One (ep_456).strm
```

---

# Usage

## Automatic Sync

When Kodi starts:

1. Waits 5 seconds.
2. Performs initial synchronization.
3. Continues syncing at configured intervals.
4. Detects configuration changes automatically.

---

## Manual Sync

### Kodi

```python
xbmc.executebuiltin(
    "RunScript(special://home/addons/service.ultimate-vod, sync)"
)
```

### Command Line

```bash
kodi-send --action="RunScript(special://home/addons/service.ultimate-vod, sync)"
```

### Another Addon

```python
import xbmc

xbmc.executebuiltin("RunScript(service.ultimate-vod, sync)")
```

### JSON-RPC

```json
{
  "jsonrpc": "2.0",
  "method": "Addons.ExecuteAddon",
  "params": {
    "addonid": "service.ultimate-vod",
    "params": ["sync"]
  },
  "id": 1
}
```

---

# Notifications

| Notification       | Description            |
|--------------------|------------------------|
| ✅ New Content     | Added items            |
| 🗑 Removed Content | Deleted items          |
| ⚠ Sync Errors     | Completed with errors  |
| ❌ Sync Failure    | Synchronization failed |

---

# Technical Details

## Concurrency

The addon prevents concurrent synchronization using:

- Cross-process file locking
- Thread synchronization

## Atomic Writes

STRM files are written safely:

1. Write to temporary file
2. Atomic rename using `os.replace()`
3. Cleanup on failure

---

## Settings Changes

No restart required.

Changes detected automatically every few seconds.

Supported:

- Crawler URL
- Sync interval
- Library path

---

## Error Recovery

- Failed items are skipped.
- Successful items continue.
- Failed entries are retried during the next sync.
- Service remains running.

---

# Project Structure

```
service.ultimate-vod/
├── addon.xml
├── settings.xml
├── strings.po
├── service.py
├── resources/
│   └── lib/
│       ├── monitor.py
│       ├── settings.py
│       ├── crawler_client.py
│       ├── library_manager.py
│       └── lock_utils.py
└── LICENSE
```

---

# Testing

Recommended tests:

- Initial synchronization
- Incremental synchronization
- Manual sync during automatic sync
- Network failures
- Invalid crawler data
- Kodi shutdown
- Settings changes

---

# Troubleshooting

## Crawler unavailable

Check:

- Crawler is running
- URL is correct
- Firewall
- Network connectivity

Example:

```bash
curl http://localhost:7888/health
```

---

## Sync not starting

Check:

- Lock file
- Library Sync enabled
- Kodi logs
- Restart Kodi if necessary

---

## STRM files missing

Verify:

- Correct `.strm` extension
- Valid stream URL
- File permissions
- Kodi library scan

---

## High CPU Usage

Large libraries may require:

- Longer sync intervals
- Incremental updates
- Batch processing

---

# Debug Logging

Enable:

```
Settings
→ System
→ Logging
→ Enable Debug Logging
```

Log levels:

- LOGERROR
- LOGWARNING
- LOGINFO
- LOGDEBUG

---

# Performance

| Library Size  | Expected Sync Time     |
|---------------|------------------------|
| < 1,000 items | Seconds                |
| 1,000–10,000  | 1–5 minutes            |
| >10,000       | Increase sync interval |

---

# Security

- Path traversal protection
- Input validation
- HTTPS support
- Safe file permissions

---

# Roadmap

- SQLite database
- Batch processing
- Smarter retries
- Web dashboard
- Cron-style scheduling
- Multiple crawler support

---

# Contributing

1. Fork the repository.
2. Create a feature branch.
3. Add tests.
4. Submit a Pull Request.

Please:

- Follow PEP8.
- Write docstrings.
- Update documentation.
- Test before submitting.

---

# License

MIT License

See `LICENSE`.

---

# Acknowledgments

- Kodi Team
- Community contributors
- Requests library

---

# Support

- GitHub Wiki
- GitHub Issues
- Kodi Forum
- Community Discord

---

# Changelog

## v1.0.0

### Features

- Initial release
- Movie & TV synchronization
- Incremental updates
- Cross-process locking
- Atomic writes
- Manual sync
- Configurable interval
- Kodi notifications
- Live settings updates

### Platforms

- Linux
- macOS
- Windows
- Android
- Raspberry Pi

### API

- Health endpoint
- Export endpoint
- Status endpoint
- Manual sync
- Retry logic
- HTTP session reuse

### Bug Fixes

- Fixed race conditions
- Fixed path traversal vulnerability
- Fixed memory leak
- Fixed sync interval updates