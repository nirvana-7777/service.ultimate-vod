Ultimate VOD Service
KodiPythonLicense

Background service addon for Kodi that automatically syncs video library content from a VOD Crawler API, keeping your Kodi library up-to-date with minimal user intervention.

Features
🔄 Automatic Library Sync: Periodically syncs movies and TV shows from your VOD crawler
📈 Incremental Updates: Only syncs changed content to minimize bandwidth and processing time
🔒 Cross-Process Safety: File locking prevents concurrent syncs (important for manual triggers)
💾 Atomic File Operations: Uses temporary files + atomic rename to prevent corrupted .strm files
⚡ Manual Sync Trigger: Run sync on-demand via script argument
⏰ Configurable Schedule: Adjustable sync interval (default: 1 hour)
🔔 Kodi Notifications: Alerts for sync completion, errors, and new content
🔄 Background Operation: Runs as a Kodi service with minimal performance impact
🌍 Cross-Platform: Works on Linux, macOS, Windows, Android, and Raspberry Pi
Requirements
Requirement	Version
Kodi	v18+ (Leia) or higher
Python	3.0+
script.module.requests	2.31.0+
Installation
From Repository
Add the repository source in Kodi
Navigate to Add-ons > Install from repository
Find Ultimate VOD Service under Services
Click Install
Manual Installation
Download the latest release from the releases page
In Kodi, go to Add-ons > Install from zip file
Select the downloaded .zip file
Configure the addon settings
Configuration
After installation, configure the addon:

Go to Settings > Add-ons > My add-ons > Services
Find Ultimate VOD Service
Click Configure
Settings Overview
Setting	Description	Default
Crawler API URL	URL of your VOD Crawler service (e.g., http://192.168.1.100:7888)	http://localhost:7888
Enable Library Sync	Master toggle for automatic sync	false
Auto-sync on Kodi startup	Perform initial sync when Kodi launches	true
Sync interval	How often to sync (in seconds)	3600 (1 hour)
Library Cache Path	Where to store .strm files	special://profile/addon_data/service.ultimate-vod/cache
Architecture
API Endpoints
The addon expects the crawler API to provide these endpoints:

Endpoint	Method	Description
/health	GET	Health check (returns 200 OK if healthy)
/api/library/export	GET	Export library with optional since parameter for incremental syncs
/api/status	GET	Status information about the crawler
/api/sync	POST	Trigger a manual sync (optional provider parameter)
Export Format
The library export endpoint must return JSON in this format:

{  "type": "full|incremental",  "timestamp": "2024-01-01T00:00:00Z",  "movies": [    {      "id": "movie_123",      "title": "Movie Title",      "release_year": 2024,      "stream_url": "https://example.com/stream.m3u8",      "provider": "provider_name"    }  ],  "episodes": [    {      "id": "ep_456",      "title": "Episode Title",      "show_title": "Show Name",      "season_number": 1,      "episode_number": 1,      "stream_url": "https://example.com/stream.m3u8"    }  ],  "deleted": [    {      "id": "movie_123",      "type": "movie"    }  ]}
File Structure
The addon stores library files in the configured cache directory:

text

cache/
├── .sync.lock                    # Cross-process lock file
├── Movies/
│   └── {provider}/
│       └── {Title} ({Year}) ({ID}).strm
└── TV Shows/
    └── {Show Title}/
        └── Season {NN}/
            └── S{NN}E{NN} - {Title} ({ID}).strm
Naming Convention
The ID is embedded in parentheses () for compatibility:

Kodi scrapers ignore text in parentheses for titles
The ID provides a unique identifier for the file
Enables glob-based searching and matching
Examples:

Movies/Netflix/Inception (2010) (movie_123).strm
TV Shows/Stranger Things/Season 01/S01E01 - Chapter One (ep_456).strm
Usage
Automatic Sync
The service starts automatically with Kodi and:

Waits 5 seconds for Kodi to fully initialize
Performs an initial sync if auto-sync is enabled
Continues syncing at the configured interval
Handles settings changes without restart
Manual Sync
From Kodi UI
Add a menu item or button that calls:

python

xbmc.executebuiltin("RunScript(special://home/addons/service.ultimate-vod, sync)")
From Command Line

bash

kodi-send --action="RunScript(special://home/addons/service.ultimate-vod, sync)"
From Another Addon

python

import xbmc
xbmc.executebuiltin("RunScript(service.ultimate-vod, sync)")
Using JSON-RPC

json

{
  "jsonrpc": "2.0",
  "method": "Addons.ExecuteAddon",
  "params": {
    "addonid": "service.ultimate-vod",
    "params": ["sync"]
  },
  "id": 1
}
Notifications
The addon shows notifications for:

✅ New content added - "Added X items to library"
🗑️ Content removed - "Removed X items from library"
⚠️ Sync errors - "Sync finished with X item error(s), see log"
❌ Sync failure - "Sync failed: [reason]"
Technical Details
Concurrency Model
The service uses two levels of concurrency control:

Cross-Process File Locking (FileLock class):

Prevents sync overlap between the background service and manual triggers
Uses fcntl on POSIX systems, msvcrt on Windows
Configurable timeout (default: 30 seconds)
Thread Synchronization (is_syncing flag):

Prevents multiple sync threads within the same process
Deferred recreation of LibraryManager during active sync
Atomic File Operations
To prevent corrupted .strm files:

Write content to a temporary file in the same directory
os.replace() atomically moves it to the final location
On failure, cleanup the temporary file
This ensures Kodi never sees a partially-written file

Settings Change Handling
The service monitors for settings changes without restart:

Changes to crawler_url trigger recreation of LibraryManager
Changes to sync_interval take effect on the next cycle
Settings changes are detected in the main loop
Error Recovery
Failed items are logged and skipped (not blocking the entire sync)
last_sync_time only advances if all items succeed
Failed items will be retried on the next sync
Service continues running even after exceptions
Development
Project Structure
text

service.ultimate-vod/
├── addon.xml                      # Addon manifest
├── settings.xml                   # Settings definition
├── strings.po                     # Localization strings
├── service.py                     # Main service entry
├── resources/
│   └── lib/                      # Python modules
│       ├── __init__.py
│       ├── monitor.py            # Kodi lifecycle monitoring
│       ├── settings.py           # Settings helpers
│       ├── crawler_client.py     # API client with retry logic
│       ├── library_manager.py    # Core sync logic
│       └── lock_utils.py         # Cross-process file locking
└── LICENSE
Module Descriptions
Module
Purpose
service.py	Main entry point, service loop, sync scheduling
monitor.py	Kodi lifecycle monitoring (abort, settings changes)
settings.py	Type-safe setting getters/setters with error handling
crawler_client.py	HTTP client with retries, health checks, API calls
library_manager.py	.strm generation, cache management, sync logic
lock_utils.py	Cross-process advisory file locking

Adding New Content Types
Update the crawler API to include the new type in the export
Add a _process_{type}() method in LibraryManager
Update _generate_strm_files() to handle the new type
Update _count_items() to track the new type
Update the ID index builder if needed
Testing
Manual Testing:

Enable Kodi debug logging
Set a short sync interval (e.g., 60 seconds)
Monitor kodi.log for sync operations
Test manual sync: RunScript(service.ultimate-vod, sync)
Test settings changes while the service is running
Test Scenarios:

✅ Initial full sync on startup
✅ Incremental sync after changes
✅ Manual sync while automatic sync is running (should lock)
✅ Network failures during sync
✅ Invalid data from crawler API
✅ Kodi shutdown during sync
✅ Settings changes during sync
Extending the Addon
Adding a Sync Schedule UI:

Add settings for day/time selection
Modify the scheduling logic in UltimateService.run()
Custom Cache Locations:

Settings already support library_path
Could add per-provider subdirectories
Multiple Crawler Support:

Extend crawler_client to support multiple URLs
Add provider-specific sync logic
Web UI Integration:

Add a web server endpoint
Use JSON-RPC to trigger sync from browser
Troubleshooting
Common Issues
"Crawler not available" errors
Symptoms:

text

[Ultimate VOD Service] Crawler not available: Connection refused
[Ultimate VOD Service] Crawler not available, skipping sync
Solutions:

Verify the crawler API URL is accessible from Kodi's network
Check if the crawler service is running: curl http://localhost:7888/health
Ensure no firewall blocks port 7888 (default)
Check Kodi's network settings (proxy, etc.)
Sync stuck or doesn't start
Symptoms:

No sync activity in logs
"Another sync is already in progress" messages
Solutions:

Check if a stale lock file exists: ls -la /path/to/cache/.sync.lock
Remove the lock file if the sync process died: rm /path/to/cache/.sync.lock
Verify settings: "Enable Library Sync" must be checked
Check logs for "Settings changed" messages
Restart Kodi to reset the service
.strm files not appearing in Kodi library
Symptoms:

Files exist in cache but Kodi doesn't show them
Kodi library scan runs but no new items appear
Solutions:

Ensure the .strm files have the correct extension (.strm)
Verify the file content is a valid URL
Check file permissions (Kodi must be able to read them)
Manually trigger library scan: UpdateLibrary(video)
Check if the content is already in the library (duplicate detection)
High CPU during sync
Symptoms:

Kodi becomes sluggish during sync
CPU usage spikes to 100%
Solutions:

Large libraries may take time to process
Consider adjusting sync interval (less frequent)
Use incremental syncs (already supported)
Check if the crawler is returning huge exports
Consider adding batch processing in library_manager.py
Settings not applying
Symptoms:

Changed settings don't take effect
Service continues using old configuration
Solutions:

The service checks settings every 5 seconds
Wait for the next check cycle
Check logs for "Settings changed" messages
Try restarting the service (restart Kodi)
Debug Logging
Enable debug logging to see detailed sync operations:

In Kodi:

Settings → System → Logging → Enable debug logging
Restart Kodi or wait for next sync
Check ~/.kodi/temp/kodi.log (Linux/macOS) or %APPDATA%\Kodi\temp\kodi.log (Windows)
Log Levels:

LOGERROR: Critical failures
LOGWARNING: Non-fatal issues (settings errors, network timeouts)
LOGINFO: Important operations (sync start/complete, settings changes)
LOGDEBUG: Detailed operations (file writes, API calls, health checks)
Getting Help
Check the logs - Most issues are visible in kodi.log
GitHub Issues - Report bugs at [repository issues]
Kodi Forum - Search the Kodi forum
Community Discord - Join the Kodi community Discord
Performance Considerations
Scalability

Small libraries (< 1000 items): Sync completes in seconds
Medium libraries (1000-10000 items): Sync takes 1-5 minutes
Large libraries (> 10000 items): Consider increasing sync interval
Optimization Tips

Database Indexing: The addon builds a full index of the library during sync. For very large libraries, consider storing the index in memory.
Batch Processing: The current implementation processes items one by one. Consider batching file operations.
Cache Warming: On systems with slow storage, the initial sync may take longer. Consider pre-warming the cache.
Network Bandwidth: The crawler API may return large exports. Consider compressing the transfer.
Security Considerations
Path Traversal: The addon sanitizes all user-provided data to prevent path traversal attacks.
Input Validation: All data from the crawler is validated before use.
Network Security: HTTPS is supported for secure communication.
File Permissions: Files are created with default permissions (user-writable only).
Future Roadmap
Planned Features
 SQLite Database: Replace file-based index with proper database
 Batch Processing: Process items in batches for better performance
 Smarter Retries: Exponential backoff for failed items
 Web Dashboard: Monitor sync status via web interface
 Custom Schedule: Support for custom cron-like schedules
 Multiple Crawlers: Sync from multiple crawler instances
Contributing
Fork the repository
Create a feature branch
Make your changes (with tests if possible)
Submit a pull request
Guidelines:

Follow PEP 8 style guide
Add docstrings to new functions
Update README.md for user-facing changes
Test on at least one platform before submitting
License
MIT License - See LICENSE file for details.

Acknowledgments
Kodi Team for the excellent API
Community contributors for testing and feedback
Requests library for HTTP client
Support
Documentation: GitHub Wiki
Issues: GitHub Issues
Forum Thread: Kodi Forum
Discord: Join the Kodi community Discord
Changelog
v1.0.0 - 2024-01-01
Features:

Initial release
Support for movie and TV show syncing
Incremental updates with since parameter
Cross-process file locking for safety
Atomic file writes to prevent corruption
Manual sync trigger via script arguments
Configurable sync interval
Kodi notifications for sync events
Settings change monitoring without restart
Support for Kodi 18+ and Python 3.0+
Platforms:

✅ Linux (x86_64, ARM)
✅ macOS (Intel, Apple Silicon)
✅ Windows (x86_64)
✅ Android
✅ Raspberry Pi
API Features:

Health check endpoint
Library export with incremental support
Status endpoint
Manual sync trigger
Retry logic with exponential backoff
HTTP session reuse for performance
Bug Fixes:

Fixed race condition in settings change handling
Fixed path traversal vulnerability in file naming
Fixed memory leak in large exports
Fixed sync interval not updating without restart