#!/usr/bin/env python3
"""
Library manager - handles .strm file generation and sync
"""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Dict, Tuple, List

import xbmc
import xbmcvfs
import xbmcgui

from crawler_client import CrawlerClient
from settings import get_setting, get_setting_bool, set_setting
from lock_utils import FileLock, LockTimeoutError

LOG_PREFIX = "[Ultimate VOD Service]"

# Characters not safe in filesystem path components across Windows/macOS/Linux.
_INVALID_CHARS = '<>:"/\\|?*'
_MAX_COMPONENT_LEN = 200


class LibraryManager:
    """Manages Kodi library integration via crawler"""

    def __init__(self):
        self.crawler_url = get_setting('crawler_url')
        self.cache_dir = Path(xbmcvfs.translatePath(
            get_setting('library_path', 'special://profile/addon_data/service.ultimate-vod/cache')
        ))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.lock_path = self.cache_dir / ".sync.lock"

        self.client = CrawlerClient(self.crawler_url) if self.crawler_url else None
        self.last_sync = get_setting('last_sync_time') or None

    def is_enabled(self) -> bool:
        """Check if crawler integration is enabled"""
        enabled = get_setting_bool('crawler_enabled')
        return enabled and self.client is not None and self.client.is_available()

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
        """
        Sync library with crawler.

        Serialized across processes via a file lock on the cache directory,
        since a manual "sync now" invocation (separate Kodi script process)
        can otherwise overlap with the background service's scheduled sync
        and corrupt the .strm tree (concurrent writes + empty-dir cleanup
        racing against files being written).
        """
        if not self.is_enabled():
            return False, "Crawler integration disabled", {}

        progress = None
        if show_progress:
            progress = xbmcgui.DialogProgressBG()
            progress.create("Ultimate VOD", "Waiting for sync lock...")

        try:
            with FileLock(self.lock_path, timeout=30):
                return self._sync_locked(progress)
        except LockTimeoutError as e:
            xbmc.log(f"{LOG_PREFIX} {e}", xbmc.LOGWARNING)
            if progress:
                progress.close()
            return False, "Another sync is already in progress", {}
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Sync error: {e}", xbmc.LOGERROR)
            if progress:
                progress.close()
            return False, f"Sync error: {str(e)}", {}

    def _sync_locked(self, progress) -> Tuple[bool, str, Dict]:
        """The actual sync body, run while holding the cross-process lock."""
        if progress:
            progress.update(10, message="Fetching library data...")

        since = self.last_sync if self.last_sync else None
        data = self.client.get_export(since=since)

        if not data:
            if progress:
                progress.close()
            return False, "Failed to fetch library data", {}

        is_incremental = data.get('type') == 'incremental'

        if progress:
            progress.update(30, message=f"{'Incremental' if is_incremental else 'Full'} sync...")

        stats = self._generate_strm_files(data, progress)

        if progress:
            progress.update(80, message="Updating Kodi library...")

        # Only advance the sync watermark if nothing went wrong. If some
        # items failed, we deliberately do NOT advance last_sync, so the
        # next incremental export re-includes the failed items rather than
        # silently skipping them forever.
        timestamp = data.get('timestamp')
        if timestamp and stats.get('errors', 0) == 0:
            self.last_sync = timestamp
            set_setting('last_sync_time', self.last_sync)
        elif stats.get('errors', 0) > 0:
            xbmc.log(
                f"{LOG_PREFIX} Sync had {stats['errors']} item error(s); "
                f"not advancing last_sync so they are retried next run",
                xbmc.LOGWARNING
            )

        if progress:
            progress.update(100, message="Done!")
            progress.close()

        if stats.get('added', 0) > 0 or stats.get('deleted', 0) > 0:
            self._trigger_library_scan()

        message = "Sync complete" if stats.get('errors', 0) == 0 else \
            f"Sync complete with {stats['errors']} error(s)"
        return True, message, stats

    def _generate_strm_files(self, data: Dict, progress=None) -> Dict:
        """
        Generate .strm files from export data.

        IDs are embedded in parentheses () for glob compatibility.
        Kodi scrapers naturally ignore text in parentheses for titles.

        Each item is processed independently: a malformed record (missing
        required fields) is logged and skipped rather than aborting the
        whole batch, since partial data from one bad record shouldn't cost
        the rest of an otherwise-successful sync.
        """
        stats = {'added': 0, 'updated': 0, 'deleted': 0, 'movies': 0, 'episodes': 0, 'errors': 0}

        movies = data.get('movies', [])
        episodes = data.get('episodes', [])
        deleted = data.get('deleted', [])
        total = len(movies) + len(episodes)
        done = 0

        for movie in movies:
            done += 1
            try:
                self._process_movie(movie, stats)
            except Exception as e:
                stats['errors'] += 1
                xbmc.log(f"{LOG_PREFIX} Failed to process movie "
                          f"{movie.get('id', '?')!r}: {e}", xbmc.LOGWARNING)

            if progress and done % 10 == 0:
                pct = 30 + int((done / max(total, 1)) * 50)
                title = str(movie.get('title', 'Movie'))[:30]
                progress.update(pct, message=f"Writing {title}...")

        for episode in episodes:
            done += 1
            try:
                self._process_episode(episode, stats)
            except Exception as e:
                stats['errors'] += 1
                xbmc.log(f"{LOG_PREFIX} Failed to process episode "
                          f"{episode.get('id', '?')!r}: {e}", xbmc.LOGWARNING)

            if progress and done % 10 == 0:
                pct = 30 + int((done / max(total, 1)) * 50)
                title = str(episode.get('title', 'Episode'))[:30]
                progress.update(pct, message=f"Writing {title}...")

        if deleted:
            self._process_deletions(deleted, stats)

        self._clean_empty_dirs()

        return stats

    def _process_movie(self, movie: Dict, stats: Dict) -> None:
        stream_url = movie.get('stream_url')
        movie_id = movie.get('id')
        if not stream_url or not movie_id:
            raise ValueError(f"missing stream_url/id: {movie!r}")

        filepath = self._get_movie_path(movie)
        existing_content = self._read_strm_file(filepath)

        if not self._write_strm_file(filepath, stream_url):
            stats['errors'] += 1
            return

        stats['movies'] += 1
        if existing_content is None:
            stats['added'] += 1
        elif existing_content.strip() != stream_url.strip():
            stats['updated'] += 1

    def _process_episode(self, episode: Dict, stats: Dict) -> None:
        stream_url = episode.get('stream_url')
        episode_id = episode.get('id')
        if not stream_url or not episode_id:
            raise ValueError(f"missing stream_url/id: {episode!r}")

        filepath = self._get_episode_path(episode)
        existing_content = self._read_strm_file(filepath)

        if not self._write_strm_file(filepath, stream_url):
            stats['errors'] += 1
            return

        stats['episodes'] += 1
        if existing_content is None:
            stats['added'] += 1
        elif existing_content.strip() != stream_url.strip():
            stats['updated'] += 1

    def _process_deletions(self, deleted: List[Dict], stats: Dict) -> None:
        """
        Delete .strm files by ID.

        Builds a single {id: path} index up front instead of doing an
        rglob() scan per deleted item, since a per-item scan is O(n) over
        the whole library for every deletion (an N+1-style scan pattern).
        """
        movie_index = self._build_id_index(self.cache_dir / "Movies")
        show_index = self._build_id_index(self.cache_dir / "TV Shows")

        for item in deleted:
            try:
                item_id = item.get('id')
                item_type = item.get('type')
                if not item_id or not item_type:
                    raise ValueError(f"missing id/type: {item!r}")

                index = movie_index if item_type == 'movie' else show_index
                filepath = index.get(str(item_id))

                if filepath and filepath.exists():
                    filepath.unlink()
                    stats['deleted'] += 1
            except Exception as e:
                stats['errors'] += 1
                xbmc.log(f"{LOG_PREFIX} Failed to process deletion "
                          f"{item.get('id', '?')!r}: {e}", xbmc.LOGWARNING)

    @staticmethod
    def _build_id_index(root: Path) -> Dict[str, Path]:
        """Build a {id: path} index from .strm filenames of the form
        '... (id).strm' with a single directory walk."""
        index: Dict[str, Path] = {}
        if not root.exists():
            return index

        for filepath in root.rglob("*.strm"):
            stem = filepath.stem
            if stem.endswith(")") and "(" in stem:
                item_id = stem[stem.rfind("(") + 1:-1]
                # Last write wins on duplicate IDs; duplicates shouldn't
                # occur under normal operation but we don't want an
                # ambiguous match to silently pick an arbitrary file.
                index[item_id] = filepath

        return index

    def _get_movie_path(self, movie: Dict) -> Path:
        """Get the filesystem path for a movie .strm file"""
        provider = self._clean_path_component(movie.get('provider', 'unknown'))
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
        show_title = self._clean_path_component(episode.get('show_title', 'Unknown Show'))
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

    @staticmethod
    def _write_strm_file(filepath: Path, content: str) -> bool:
        """
        Write a .strm file atomically: write to a temp file in the same
        directory, then os.replace() it into place. This avoids leaving a
        truncated/corrupt .strm file behind if the process is killed
        mid-write (e.g. container stop/OOM), which Kodi would otherwise
        try to play and fail on.

        Returns True on success, False on failure, so callers can keep
        their added/updated/movies/episodes counters accurate instead of
        counting writes that never actually landed.
        """
        try:
            filepath.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(filepath.parent), prefix=".tmp_", suffix=".strm"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, str(filepath))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            return True
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Failed to write {filepath}: {e}", xbmc.LOGWARNING)
            return False

    @staticmethod
    def _read_strm_file(filepath: Path) -> Optional[str]:
        """Read the content of a .strm file"""
        if not filepath.exists():
            return None
        try:
            return filepath.read_text(encoding='utf-8')
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Failed to read {filepath}: {e}", xbmc.LOGDEBUG)
            return None

    @staticmethod
    def _clean_path_component(value: str) -> str:
        """
        Sanitize a single path component (e.g. show title, provider name)
        for cross-platform filesystem safety. Distinct from _clean_filename
        in that it also rejects '..'/'.' components to prevent a
        crawler-supplied title from escaping cache_dir via directory
        traversal.
        """
        value = str(value).strip()
        for char in _INVALID_CHARS:
            value = value.replace(char, '_')
        value = value.strip('. ')  # trailing dots/spaces are unsafe on Windows
        if value in ('', '.', '..'):
            value = '_'
        if len(value) > _MAX_COMPONENT_LEN:
            value = value[:_MAX_COMPONENT_LEN]
        return value

    @staticmethod
    def _clean_filename(filename: str) -> str:
        """Clean a filename for filesystem compatibility"""
        # Remove invalid characters
        for char in _INVALID_CHARS:
            filename = filename.replace(char, '_')
        # Limit length
        if len(filename) > _MAX_COMPONENT_LEN:
            name, ext = os.path.splitext(filename)
            filename = name[:_MAX_COMPONENT_LEN - len(ext)] + ext
        return filename

    def _clean_empty_dirs(self) -> None:
        """Remove empty directories from the cache"""
        for root, dirs, files in os.walk(str(self.cache_dir), topdown=False):
            for dir_name in dirs:
                dir_path = Path(root) / dir_name
                try:
                    if not any(dir_path.iterdir()):
                        dir_path.rmdir()
                        xbmc.log(f"{LOG_PREFIX} Removed empty dir: {dir_path}", xbmc.LOGDEBUG)
                except OSError as e:
                    xbmc.log(f"{LOG_PREFIX} Could not remove dir {dir_path}: {e}", xbmc.LOGDEBUG)

    @staticmethod
    def _trigger_library_scan() -> None:
        """Tell Kodi to scan the library"""
        xbmc.executebuiltin("UpdateLibrary(video)")

    @staticmethod
    def _is_source_added() -> bool:
        """Check if the cache directory is already in sources.xml"""
        import xml.etree.ElementTree as ET

        sources_path = xbmcvfs.translatePath('special://profile/sources.xml')
        raw_path = get_setting('library_path', 'special://profile/addon_data/service.ultimate-vod/cache')
        if not raw_path.endswith('/') and not raw_path.endswith('\\'):
            raw_path += '/'

        if not xbmcvfs.exists(sources_path):
            return False

        try:
            tree = ET.parse(sources_path)
            root = tree.getroot()
            video_node = root.find('video')
            if video_node is None:
                return False

            for source in video_node.findall('source'):
                path_node = source.find('path')
                if path_node is not None and path_node.text == raw_path:
                    return True
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Error checking sources.xml: {e}", xbmc.LOGWARNING)

        return False

    def add_source_to_library(self) -> Tuple[bool, str]:
        """Automatically add the cache folder to Kodi's sources.xml"""
        import xml.etree.ElementTree as ET
        from xml.dom import minidom

        if self._is_source_added():
            return True, "already_exists"

        sources_path = xbmcvfs.translatePath('special://profile/sources.xml')
        raw_path = get_setting('library_path', 'special://profile/addon_data/service.ultimate-vod/cache')
        if not raw_path.endswith('/') and not raw_path.endswith('\\'):
            raw_path += '/'

        try:
            # Parse existing sources.xml or create a new one
            try:
                tree = ET.parse(sources_path)
                root = tree.getroot()
            except (IOError, FileNotFoundError):
                root = ET.Element('sources')
                tree = ET.ElementTree(root)

            # Find or create <video> section
            video_node = root.find('video')
            if video_node is None:
                video_node = ET.SubElement(root, 'video')
                ET.SubElement(video_node, 'default', {'pathversion': '1'})

            # Create new source node
            source_node = ET.SubElement(video_node, 'source')
            name_node = ET.SubElement(source_node, 'name')
            name_node.text = "Ultimate VOD"
            path_node = ET.SubElement(source_node, 'path')
            path_node.set('pathversion', '1')
            path_node.text = raw_path
            allowsharing_node = ET.SubElement(source_node, 'allowsharing')
            allowsharing_node.text = "true"

            # Write back to file with pretty formatting
            xml_str = ET.tostring(root, encoding='utf-8')
            pretty_xml = minidom.parseString(xml_str).toprettyxml(indent="  ", encoding='utf-8')

            with open(sources_path, 'wb') as f:
                f.write(pretty_xml)

            xbmc.log(f"{LOG_PREFIX} Source added to sources.xml successfully", xbmc.LOGINFO)
            return True, ""
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Exception adding source to XML: {e}", xbmc.LOGERROR)
            return False, str(e)

    def clear_cache(self) -> None:
        """Clear the entire library cache"""
        try:
            with FileLock(self.lock_path, timeout=30):
                if self.cache_dir.exists():
                    for entry in self.cache_dir.iterdir():
                        if entry == self.lock_path:
                            continue
                        if entry.is_dir():
                            shutil.rmtree(entry)
                        else:
                            entry.unlink()
                    xbmc.log(f"{LOG_PREFIX} Library cache cleared", xbmc.LOGINFO)
        except LockTimeoutError as e:
            xbmc.log(f"{LOG_PREFIX} {e}", xbmc.LOGWARNING)