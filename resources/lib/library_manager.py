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
        enabled = get_setting_bool('crawler_enabled')
        return enabled and self.client is not None and self.client.is_available()

    def get_status(self) -> Dict:
        if not self.is_enabled():
            return {"enabled": False}

        status = self.client.get_status() or {}
        status['enabled'] = True
        status['last_sync'] = self.last_sync
        status['library_path'] = str(self.cache_dir)
        status['items_count'] = self._count_items()

        return status

    def _count_items(self) -> Dict[str, int]:
        counts = {'movies': 0, 'episodes': 0, 'shows': 0}

        movies_dir = self.cache_dir / "Movies"
        if movies_dir.exists():
            counts['movies'] = sum(1 for _ in movies_dir.rglob("*.strm"))

        shows_dir = self.cache_dir / "TV Shows"
        if shows_dir.exists():
            counts['episodes'] = sum(1 for _ in shows_dir.rglob("*.strm"))
            counts['shows'] = sum(1 for _ in shows_dir.iterdir() if _.is_dir())

        return counts

    def sync(self, show_progress: bool = False, force_full: bool = False) -> Tuple[bool, str, Dict]:
        if not self.is_enabled():
            return False, "Crawler integration disabled", {}

        progress = None
        if show_progress:
            progress = xbmcgui.DialogProgressBG()
            progress.create("Ultimate VOD", "Waiting for sync lock...")

        try:
            with FileLock(self.lock_path, timeout=30):
                return self._sync_locked(progress, force_full)
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

    def _sync_locked(self, progress, force_full: bool = False) -> Tuple[bool, str, Dict]:
        if progress:
            progress.update(10, message="Fetching library data...")

        since = None if force_full else (self.last_sync if self.last_sync else None)
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
        index: Dict[str, Path] = {}
        if not root.exists():
            return index

        # rglob("*.strm") intentionally ignores our atomic-write temp files
        # (.tmp_*.strm) because leading dots are not matched by '*' on POSIX.
        for filepath in root.rglob("*.strm"):
            stem = filepath.stem
            if stem.endswith(")") and "(" in stem:
                item_id = stem[stem.rfind("(") + 1:-1]
                index[item_id] = filepath

        return index

    def _get_movie_path(self, movie: Dict) -> Path:
        provider = self._clean_path_component(movie.get('provider', 'unknown'))
        provider_dir = self.cache_dir / "Movies" / provider
        provider_dir.mkdir(parents=True, exist_ok=True)

        title = movie.get('title', 'Unknown')
        year = movie.get('release_year', '')
        year_str = f" ({year})" if year else ""

        movie_id = movie.get('id', '')
        id_suffix = f" ({movie_id})"
        ext = ".strm"

        # Truncate title specifically so the ID suffix isn't stripped later.
        # max(0, ...) prevents negative slicing if id_suffix exceeds the budget.
        max_title_len = max(0, _MAX_COMPONENT_LEN - len(year_str) - len(id_suffix) - len(ext))
        if len(title) > max_title_len:
            title = title[:max_title_len]

        filename = f"{title}{year_str}{id_suffix}{ext}"
        return provider_dir / self._clean_filename(filename)

    def _get_episode_path(self, episode: Dict) -> Path:
        show_title = self._clean_path_component(episode.get('show_title', 'Unknown Show'))
        show_dir = self.cache_dir / "TV Shows" / show_title
        season = episode.get('season_number', 1)
        season_dir = show_dir / f"Season {season:02d}"
        season_dir.mkdir(parents=True, exist_ok=True)

        ep_num = episode.get('episode_number', 0)
        title = episode.get('title', 'Episode')

        episode_id = episode.get('id', '')
        id_suffix = f" ({episode_id})"

        prefix = f"S{season:02d}E{ep_num:02d} - "
        ext = ".strm"

        # Truncate title specifically so the ID suffix isn't stripped later.
        # max(0, ...) prevents negative slicing if id_suffix exceeds the budget.
        max_title_len = max(0, _MAX_COMPONENT_LEN - len(prefix) - len(id_suffix) - len(ext))
        if len(title) > max_title_len:
            title = title[:max_title_len]

        filename = f"{prefix}{title}{id_suffix}{ext}"
        return season_dir / self._clean_filename(filename)

    @staticmethod
    def _write_strm_file(filepath: Path, content: str) -> bool:
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
        if not filepath.exists():
            return None
        try:
            return filepath.read_text(encoding='utf-8')
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Failed to read {filepath}: {e}", xbmc.LOGDEBUG)
            return None

    @staticmethod
    def _clean_path_component(value: str) -> str:
        value = str(value).strip()
        for char in _INVALID_CHARS:
            value = value.replace(char, '_')
        value = value.strip('. ')
        if value in ('', '.', '..'):
            value = '_'
        if len(value) > _MAX_COMPONENT_LEN:
            value = value[:_MAX_COMPONENT_LEN]
        return value

    @staticmethod
    def _clean_filename(filename: str) -> str:
        for char in _INVALID_CHARS:
            filename = filename.replace(char, '_')
        if len(filename) > _MAX_COMPONENT_LEN:
            name, ext = os.path.splitext(filename)
            filename = name[:_MAX_COMPONENT_LEN - len(ext)] + ext
        return filename

    def _clean_empty_dirs(self) -> None:
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
        xbmc.executebuiltin("UpdateLibrary(video)")

    @staticmethod
    def _is_source_added() -> bool:
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
        import xml.etree.ElementTree as ET

        if self._is_source_added():
            return True, "already_exists"

        sources_path = xbmcvfs.translatePath('special://profile/sources.xml')
        raw_path = get_setting('library_path', 'special://profile/addon_data/service.ultimate-vod/cache')
        if not raw_path.endswith('/') and not raw_path.endswith('\\'):
            raw_path += '/'

        try:
            try:
                tree = ET.parse(sources_path)
                root = tree.getroot()
            except Exception:
                xbmc.log(f"{LOG_PREFIX} sources.xml missing or corrupt, creating new.", xbmc.LOGWARNING)
                root = ET.Element('sources')
                tree = ET.ElementTree(root)

            video_node = root.find('video')
            if video_node is None:
                video_node = ET.SubElement(root, 'video')
                default_node = ET.SubElement(video_node, 'default')
                default_node.set('pathversion', '1')

            source_node = ET.SubElement(video_node, 'source')
            name_node = ET.SubElement(source_node, 'name')
            name_node.text = "Ultimate VOD"

            path_node = ET.SubElement(source_node, 'path')
            path_node.set('pathversion', '1')
            path_node.text = raw_path

            allowsharing_node = ET.SubElement(source_node, 'allowsharing')
            allowsharing_node.text = "true"

            xml_bytes = ET.tostring(root, encoding='utf-8')
            f = xbmcvfs.File(sources_path, 'wb')
            try:
                f.write(xml_bytes)
            finally:
                f.close()

            xbmc.log(f"{LOG_PREFIX} Source added to sources.xml successfully", xbmc.LOGINFO)
            return True, ""
        except Exception as e:
            xbmc.log(f"{LOG_PREFIX} Exception adding source to XML: {e}", xbmc.LOGERROR)
            return False, str(e)

    def clear_cache(self) -> None:
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

                self.last_sync = None
                set_setting('last_sync_time', '')
        except LockTimeoutError as e:
            xbmc.log(f"{LOG_PREFIX} {e}", xbmc.LOGWARNING)