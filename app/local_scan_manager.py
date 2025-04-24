"""
LocalScanManager: Manages scanning and monitoring of local directories for changes.
Replaces the RepoManager functionality for GitHub repositories.
"""

import os
import time
import logging
import hashlib
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from typing import Dict, Set, Callable, Optional, List

logger = logging.getLogger(__name__)

class FileChangeHandler(FileSystemEventHandler):
    """
    Watchdog event handler to detect file changes.
    """
    def __init__(self, callback: Callable[[Set[str]], None], tracked_extensions: List[str]):
        """
        Initialize the change handler.
        
        Args:
            callback: Function to call when files change
            tracked_extensions: List of file extensions to track
        """
        self.callback = callback
        self.tracked_extensions = tracked_extensions
        self.changed_files: Set[str] = set()
        self.last_processed_time = time.time()
        self.processing_interval = 5  # Wait at least 5 seconds between processing batches
        
    def _should_track_file(self, path: str) -> bool:
        """Check if this file type should be tracked."""
        if not self.tracked_extensions:
            return True
        return any(path.endswith(ext) for ext in self.tracked_extensions)
        
    def on_modified(self, event):
        if not event.is_directory and self._should_track_file(event.src_path):
            self.changed_files.add(os.path.abspath(event.src_path))
            self._process_if_ready()
            
    def on_created(self, event):
        if not event.is_directory and self._should_track_file(event.src_path):
            self.changed_files.add(os.path.abspath(event.src_path))
            self._process_if_ready()
            
    def on_deleted(self, event):
        if not event.is_directory and self._should_track_file(event.src_path):
            self.changed_files.add(os.path.abspath(event.src_path))
            self._process_if_ready()
            
    def _process_if_ready(self):
        """Process changed files if enough time has passed since last processing."""
        current_time = time.time()
        if current_time - self.last_processed_time >= self.processing_interval and self.changed_files:
            self.callback(self.changed_files)
            self.changed_files = set()
            self.last_processed_time = current_time


class LocalScanManager:
    """
    Manages scanning and monitoring of local directories.
    Replaces the GitHub-based repository management.
    """
    def __init__(
        self, 
        local_path: str,
        tracked_extensions: List[str] = [
            # Code files
            ".py", ".js", ".ts", ".jsx", ".tsx", 
            # Web files
            ".html", ".css", ".scss", ".less",
            # Data files
            ".json", ".yaml", ".yml", ".xml", ".csv",
            # Documentation
            ".md", ".txt",
            # SQL
            ".sql"
        ],
        skip_dirs: List[str] = [
            ".git", "venv", "__pycache__", "node_modules", 
            ".idea", ".vscode", "build", "dist", 
            ".pytest_cache", ".mypy_cache", ".egg-info"
        ]
    ):
        """
        Initialize the local scan manager.
        
        Args:
            local_path: Path to the local directory to scan and monitor
            tracked_extensions: File extensions to track for changes
            skip_dirs: Directories to skip during scanning
        """
        self.local_path = os.path.abspath(local_path)
        self.tracked_extensions = tracked_extensions
        self.skip_dirs = skip_dirs
        self.file_hashes: Dict[str, str] = {}
        self.observer = None
        self.change_callback = None
        
        # Ensure the directory exists
        if not os.path.exists(self.local_path):
            raise ValueError(f"Directory not found: {self.local_path}")
        
        # Load previous file hashes if available
        self._load_file_hashes()
        
    def _load_file_hashes(self, hash_file: str = "file_hashes.json") -> None:
        """
        Load previously calculated file hashes.
        """
        import json
        if os.path.exists(hash_file):
            try:
                with open(hash_file, 'r', encoding='utf-8') as f:
                    self.file_hashes = json.load(f)
                logger.info(f"Loaded hashes for {len(self.file_hashes)} files")
            except Exception as e:
                logger.error(f"Error loading file hashes: {str(e)}")
                self.file_hashes = {}
    
    def _save_file_hashes(self, hash_file: str = "file_hashes.json") -> None:
        """
        Save the current file hashes to disk.
        """
        import json
        try:
            with open(hash_file, 'w', encoding='utf-8') as f:
                json.dump(self.file_hashes, f, indent=2)
            logger.info(f"Saved hashes for {len(self.file_hashes)} files")
        except Exception as e:
            logger.error(f"Error saving file hashes: {str(e)}")
    
    def calculate_file_hash(self, file_path: str) -> str:
        """
        Berechnet einen SHA-256 Hash des Dateiinhalts mit verbesserter Fehlerbehandlung.
        
        Args:
            file_path: Pfad zur Datei
            
        Returns:
            Hex String des SHA-256 Hashs oder leerer String bei Fehler
        """
        try:
            # Prüfen ob Datei existiert
            if not os.path.exists(file_path):
                logger.warning(f"Datei existiert nicht: {file_path}")
                return ""
                
            # Prüfen ob Datei zu groß ist (optional)
            file_size = os.path.getsize(file_path)
            if file_size > 100 * 1024 * 1024:  # 100MB
                logger.warning(f"Datei zu groß für Hashing: {file_path} ({file_size/1024/1024:.1f} MB)")
                # Fallback: Zeitstempel und Größe kombinieren
                stats = os.stat(file_path)
                pseudo_hash = hashlib.sha256(f"{stats.st_mtime}_{stats.st_size}".encode()).hexdigest()
                return f"size_time_{pseudo_hash[:16]}"
                
            # Standard Hashing für normale Dateien
            with open(file_path, 'rb') as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
            return file_hash
        except PermissionError:
            logger.warning(f"Keine Leseberechtigung für Datei: {file_path}")
            return ""
        except Exception as e:
            logger.error(f"Fehler beim Berechnen des Hashs für {file_path}: {str(e)}")
            return ""
    
    def scan_for_changes(self, force_rescan: bool = False, max_depth: int = None) -> Set[str]:
        """
        Scannt das Verzeichnis und identifiziert geänderte Dateien basierend auf Hash-Vergleich.
        Mit verbesserter Rekursion und Tiefenkontrolle.
        
        Args:
            force_rescan: Wenn True, werden alle Dateien als geändert betrachtet
            max_depth: Maximale Rekursionstiefe (None = unbegrenzt)
            
        Returns:
            Set von Pfaden zu geänderten Dateien (relativ zum Basisverzeichnis)
        """
        changed_files = set()
        current_files = set()
        
        def scan_directory(directory, current_depth=0):
            """Rekursive Hilfsfunktion zum Scannen von Verzeichnissen"""
            # Tiefenlimit prüfen
            if max_depth is not None and current_depth > max_depth:
                logger.debug(f"Maximale Tiefe {max_depth} erreicht für {directory}, überspringe weitere Unterverzeichnisse")
                return
                
            nonlocal changed_files, current_files
            
            try:
                # Liste der Verzeichniseinträge abrufen
                entries = os.listdir(directory)
            except PermissionError:
                logger.warning(f"Keine Leseberechtigung für Verzeichnis: {directory}")
                return
            except Exception as e:
                logger.error(f"Fehler beim Lesen von Verzeichnis {directory}: {str(e)}")
                return
            
            # Sortiere Einträge für konsistente Verarbeitung
            entries.sort()
            
            for entry in entries:
                # Überspringe zu ignorierende Verzeichnisse
                if entry.startswith('.') or entry in self.skip_dirs:
                    continue
                    
                full_path = os.path.join(directory, entry)
                rel_path = os.path.relpath(full_path, self.local_path)
                
                if os.path.isdir(full_path):
                    # Rekursiv Unterverzeichnis scannen
                    scan_directory(full_path, current_depth + 1)
                else:
                    # Überprüfe, ob die Datei überwacht werden soll
                    if not self.tracked_extensions or any(entry.endswith(ext) for ext in self.tracked_extensions):
                        current_files.add(rel_path)
                        
                        # Prüfe, ob Datei geändert wurde
                        file_hash = self.calculate_file_hash(full_path)
                        if not file_hash:
                            continue
                            
                        if force_rescan or rel_path not in self.file_hashes or self.file_hashes[rel_path] != file_hash:
                            self.file_hashes[rel_path] = file_hash
                            changed_files.add(rel_path)
                            logger.debug(f"Geänderte Datei gefunden: {rel_path}")
        
        # Starte den rekursiven Scan vom Wurzelverzeichnis
        logger.info(f"Starte rekursiven Scan in {self.local_path}{' (erzwungen)' if force_rescan else ''}")
        scan_directory(self.local_path)
        
        # Finde gelöschte Dateien
        deleted_files = set(self.file_hashes.keys()) - current_files
        for deleted in deleted_files:
            self.file_hashes.pop(deleted, None)
            changed_files.add(deleted)  # Als geändert markieren, damit sie verarbeitet wird
            logger.info(f"Gelöschte Datei erkannt: {deleted}")
            
        # Speichere aktualisierte Hashes
        self._save_file_hashes()
        
        scan_stats = {
            "scanned_files": len(current_files),
            "changed_files": len(changed_files),
            "deleted_files": len(deleted_files)
        }
        logger.info(f"Scan abgeschlossen: {scan_stats['scanned_files']} Dateien gescannt, " 
                    f"{scan_stats['changed_files']} geändert, {scan_stats['deleted_files']} gelöscht")
        
        return changed_files
    
    def start_monitoring(self, callback: Callable[[Set[str]], None]) -> None:
        """
        Start monitoring the directory for file changes.
        
        Args:
            callback: Function to call when files change
        """
        if self.observer is not None:
            self.stop_monitoring()
            
        self.change_callback = callback
        event_handler = FileChangeHandler(callback, self.tracked_extensions)
        self.observer = Observer()
        self.observer.schedule(event_handler, self.local_path, recursive=True)
        self.observer.start()
        logger.info(f"Started file system monitoring for {self.local_path}")
    
    def stop_monitoring(self) -> None:
        """
        Stop monitoring the directory for file changes.
        """
        if self.observer is not None:
            self.observer.stop()
            self.observer.join()
            self.observer = None
            logger.info("Stopped file system monitoring")
    
    def get_full_path(self, rel_path: str) -> str:
        """
        Convert a relative path to an absolute path.
        
        Args:
            rel_path: Path relative to the base directory
            
        Returns:
            Absolute path
        """
        return os.path.join(self.local_path, rel_path)
    
    def get_relative_path(self, full_path: str) -> str:
        """
        Convert an absolute path to a path relative to the base directory.
        
        Args:
            full_path: Absolute path
            
        Returns:
            Path relative to the base directory
        """
        return os.path.relpath(full_path, self.local_path)