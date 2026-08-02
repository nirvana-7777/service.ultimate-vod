#!/usr/bin/env python3
"""
Library manager - handles .strm file generation and sync
"""

import os
import json
import hashlib
import shutil
from pathlib import Path
from typing import Optional, Dict, Tuple, List

import xbmc
import xbmcvfs
import xbmcgui

from crawler_client import CrawlerClient
from settings import get_setting, get_setting_bool, set_setting


class LibraryManager:
    """Manages Kodi library integration via crawler"""

    def __init__(self):
        self.crawler_url = get_setting('crawler_url')
        self.cache_dir = Path(xbmcvfs.translatePath(
            get_setting('library_path', 'special://profile/addon_data/service.ultimate-vod/cache')
        ))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.client = CrawlerClient(self.crawler_url) if self.crawler_url else None
        self.last_sync = get_setting('last_sync_time') or None

    def is_enabled(self) -> bool:
        """Check if crawler integration is enabled"""
        enabled = get_setting_bool('crawler_enabled')
        return enabled and self.client and self.client.is_available()

    def get_status(self) -> Dict:
        """Get current library status"""
        if not self.is_enabled():
            return {"enabled": False}

        status = self.client.get_status() or {}
        status['enabled'] = True
        status['last_sync'] = self.last_sync
        status['library_path'] = str(self.cache_dir)
        status['items_count'] = self._count_items()

        return status

    def _count_items(self) -> Dict[str, int]:
        """Count items in the library cache"""
        counts = {'movies': 0, 'episodes': 0, 'shows': 0}

        movies_dir = self.cache_dir / "Movies"
        if movies_dir.exists():
            counts['movies'] = sum(1 for _ in movies_dir.rglob("*.strm"))

        shows_dir = self.cache_dir / "TV Shows"
        if shows_dir.exists():
            counts['episodes'] = sum(1 for _ in shows_dir.rglob("*.strm"))
            counts['shows'] = sum(1 for _ in shows_dir.iterdir() if _.is_dir())

        return counts

    def sync(self, show_progress: bool = False) -> Tuple[bool, str, Dict]:
        """Sync library with crawler"""
        if not self.is_enabled():
            return False, "Crawler integration disabled", {}

        progress = None
        if show_progress:
            progress = xbmcgui.DialogProgressBG()
            progress.create("Ultimate VOD", "Syncing library...")

        try:
            # Get export data
            if progress:
                progress.update(10, message="Fetching library data...")

            since = self.last_sync if self.last_sync else None
            data = self.client.get_export(since=since)

            if not data:
                return False, "Failed to fetch library data", {}

            # Determine if this is incremental or full
            is_incremental = data.get('type') == 'incremental'

            if progress:
                progress.update(30, message=f"{'Incremental' if is_incremental else 'Full'} sync...")

            # Generate .strm files
            stats = self._generate_strm_files(data, progress)

            if progress:
                progress.update(80, message="Updating Kodi library...")

            # Save last sync time
            timestamp = data.get('timestamp')
            if timestamp:
                self.last_sync = timestamp
                set_setting('last_sync_time', self.last_sync)

            if progress:
                progress.update(100, message="Done!")
                progress.close()

            # Trigger library scan if content changed
            if stats.get('added', 0) > 0 or stats.get('deleted', 0) > 0:
                self._trigger_library_scan()

            return True, "Sync complete", stats

        except Exception as e:
            xbmc.log(f"[Ultimate VOD Service] Sync error: {e}", xbmc.LOGERROR)
            if progress:
                progress.close()
            return False, f"Sync error: {str(e)}", {}

    def _generate_strm_files(self, data: Dict, progress=None) -> Dict:
        """
        Generate .strm files from export data.
        
        IDs are embedded in parentheses () for glob compatibility.
        Kodi scrapers naturally ignore text in parentheses for titles.
        """
        stats = {'added': 0, 'updated': 0, 'deleted': 0, 'movies': 0, 'episodes': 0}

        # Process movies
        movies = data.get('movies', [])
        total = len(movies) + len(data.get('episodes', []))
        done = 0

        for movie in movies:
            filepath = self._get_movie_path(movie)
            existing_content = self._read_strm_file(filepath)

            self._write_strm_file(filepath, movie['stream_url'])
            stats['movies'] += 1

            if existing_content is None:
                stats['added'] += 1
            elif existing_content.strip() != movie['stream_url'].strip():
                stats['updated'] += 1

            done += 1
            if progress and done % 10 == 0:
                pct = 30 + int((done / max(total, 1)) * 50)
                title = movie.get('title', 'Movie')[:30]
                progress.update(pct, message=f"Writing {title}...")

        # Process episodes
        episodes = data.get('episodes', [])
        for episode in episodes:
            filepath = self._get_episode_path(episode)
            existing_content = self._read_strm_file(filepath)

            self._write_strm_file(filepath, episode['stream_url'])
            stats['episodes'] += 1

            if existing_content is None:
                stats['added'] += 1
            elif existing_content.strip() != episode['stream_url'].strip():
                stats['updated'] += 1

            done += 1
            if progress and done % 10 == 0:
                pct = 30 + int((done / max(total, 1)) * 50)
                title = episode.get('title', f"S{episode['season_number']:02d}E{episode['episode_number']:02d}")[:30]
                progress.update(pct, message=f"Writing {title}...")

        # Process deletions - uses ID in filename for fast lookup
        for deleted in data.get('deleted', []):
            if deleted['type'] == 'movie':
                filepath = self._get_movie_path_by_id(deleted['id'])
            else:
                filepath = self._get_episode_path_by_id(deleted['id'])

            if filepath and filepath.exists():
                filepath.unlink()
                stats['deleted'] += 1

        # Clean up empty directories
        self._clean_empty_dirs()

        return stats

    def _get_movie_path(self, movie: Dict) -> Path:
        """Get the filesystem path for a movie .strm file"""
        provider = movie.get('provider', 'unknown')
        provider_dir = self.cache_dir / "Movies" / provider
        provider_dir.mkdir(parents=True, exist_ok=True)

        title = movie.get('title', 'Unknown')
        year = movie.get('release_year', '')
        year_str = f" ({year})" if year else ""
        
        # Use parentheses () for ID - glob compatible and Kodi scrapers ignore them
        movie_id = movie.get('id', '')
        filename = f"{title}{year_str} ({movie_id}).strm"
        
        return provider_dir / self._clean_filename(filename)

    def _get_episode_path(self, episode: Dict) -> Path:
        """Get the filesystem path for an episode .strm file"""
        show_title = episode.get('show_title', 'Unknown Show')
        show_dir = self.cache_dir / "TV Shows" / show_title
        season = episode.get('season_number', 1)
        season_dir = show_dir / f"Season {season:02d}"
        season_dir.mkdir(parents=True, exist_ok=True)

        ep_num = episode.get('episode_number', 0)
        title = episode.get('title', 'Episode')
        
        # Use parentheses () for ID - glob compatible and Kodi scrapers ignore them
        episode_id = episode.get('id', '')
        filename = f"S{season:02d}E{ep_num:02d} - {title} ({episode_id}).strm"
        
        return season_dir / self._clean_filename(filename)

    def _get_movie_path_by_id(self, movie_id: str) -> Optional[Path]:
        """Find movie .strm file by ID using filename pattern (fast)"""
        movies_dir = self.cache_dir / "Movies"
        if not movies_dir.exists():
            return None
        
        # Use parentheses () in glob pattern
        pattern = f"*({movie_id}).strm"
        for filepath in movies_dir.rglob(pattern):
            return filepath
        return None

    def _get_episode_path_by_id(self, episode_id: str) -> Optional[Path]:
        """Find episode .strm file by ID using filename pattern (fast)"""
        shows_dir = self.cache_dir / "TV Shows"
        if not shows_dir.exists():
            return None
        
        # Use parentheses () in glob pattern
        pattern = f"*({episode_id}).strm"
        for filepath in shows_dir.rglob(pattern):
            return filepath
        return None

    def _write_strm_file(self, filepath: Path, content: str) -> None:
        """Write a .strm file with the given content"""
        try:
            filepath.write_text(content, encoding='utf-8')
        except Exception as e:
            xbmc.log(f"[Ultimate VOD Service] Failed to write {filepath}: {e}", xbmc.LOGWARNING)

    def _read_strm_file(self, filepath: Path) -> Optional[str]:
        """Read the content of a .strm file"""
        if not filepath.exists():
            return None
        try:
            return filepath.read_text(encoding='utf-8')
        except:
            return None

    def _clean_filename(self, filename: str) -> str:
        """Clean a filename for filesystem compatibility"""
        # Remove invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            filename = filename.replace(char, '_')
        # Limit length
        max_len = 200
        if len(filename) > max_len:
            name, ext = os.path.splitext(filename)
            filename = name[:max_len - len(ext)] + ext
        return filename

    def _clean_empty_dirs(self) -> None:
        """Remove empty directories from the cache"""
        for root, dirs, files in os.walk(str(self.cache_dir), topdown=False):
            for dir_name in dirs:
                dir_path = Path(root) / dir_name
                try:
                    if not any(dir_path.iterdir()):
                        dir_path.rmdir()
                        xbmc.log(f"[Ultimate VOD Service] Removed empty dir: {dir_path}", xbmc.LOGDEBUG)
                except:
                    pass

    def _trigger_library_scan(self) -> None:
        """Tell Kodi to scan the library"""
        import xbmc
        xbmc.executebuiltin("UpdateLibrary(video)")

    def clear_cache(self) -> None:
        """Clear the entire library cache"""
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            xbmc.log("[Ultimate VOD Service] Library cache cleared", xbmc.LOGINFO)
