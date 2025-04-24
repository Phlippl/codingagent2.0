from typing import List, Dict, Any, Optional, Union, Tuple, Set
from app.embedding_manager import EmbeddingManager
from app.preprocessor import Preprocessor
from app.dependency_graph_builder import DependencyGraphBuilder
from app.change_analyzer import ChangeAnalyzer
from app.local_scan_manager import LocalScanManager
from app import progress
import json
import os
import logging
import threading
import time
from datetime import datetime
from tqdm import tqdm

env_local_path = os.getenv("LOCAL_PROJECT_PATH", ".")

logger = logging.getLogger(__name__)

class RAGManager:
    """
    Enhanced Retrieval-Augmented Generation Manager: Combines preprocessed code chunks,
    vector search, and prompt creation for Large Language Models.
    
    Offers enhanced features such as:
    - Automatic synchronization of the local directory state
    - Improved semantic search with context
    - Optimized chunking with different strategies
    - Contextual understanding of longer code stretches
    - Dependencies between files and components
    """
    def __init__(self,
                local_path: str = env_local_path,
                model_name: str = "all-MiniLM-L6-v2",
                index_file: str = "index.faiss",
                meta_file: str = "metadata.pkl",
                auto_sync: bool = True,
                sync_interval: int = 90,
                ignore_dirs: List[str] = None,
                track_extensions: List[str] = None):  # Neue Parameter hinzufügen
        # Initialize preprocessor and embedding manager
        logger.info(f"Initialisiere RAGManager für Verzeichnis: {local_path}")
        progress.start(desc="Initialisiere RAG-System", total=5, unit="komponenten")
        
        self.local_path = os.path.abspath(local_path)
        logger.info(f"Absoluter Pfad: {self.local_path}")
        progress.update(1)
        
        if ignore_dirs is None:
            ignore_dirs = [".git", "venv", "__pycache__", "node_modules", ".idea", ".vscode"]
        if track_extensions is None:
            track_extensions = [".py", ".js", ".ts", ".html", ".css", ".md", ".json"]

        # Initialisiere alle Komponenten
        logger.info("Initialisiere Preprocessor...")
        self.preprocessor = Preprocessor(local_path)
        progress.update(1)
        
        logger.info("Initialisiere Embedding Manager...")
        self.embed_mgr = EmbeddingManager(model_name=model_name,
                                        index_file=index_file,
                                        meta_file=meta_file)
        progress.update(1)
        
        logger.info("Initialisiere Dependency Graph Builder...")
        self.graph_builder = DependencyGraphBuilder(local_path)
        self.graph_builder.load_hashes()
        progress.update(1)
        
        logger.info("Initialisiere Change Analyzer und Local Scan Manager...")
        self.change_analyzer = ChangeAnalyzer(local_path)
        self.scan_manager = LocalScanManager(
            local_path, 
            tracked_extensions=track_extensions,
            skip_dirs=ignore_dirs
        )
        progress.update(1)
        
        # Automatic synchronization if desired
        self.auto_sync = auto_sync
        self.sync_interval = sync_interval
        self.sync_thread = None
        self.last_sync_time = None
        
        # Structure for File-Tree Cache
        self.file_tree_cache = None
        self.file_tree_last_updated = None
        
        progress.finish("RAG-System erfolgreich initialisiert")
        
        # Initialize automatic synchronization, if activated
        if self.auto_sync:
            logger.info(f"Aktiviere automatische Synchronisierung (Intervall: {self.sync_interval}s)")
            self.start_auto_sync()
            
    def start_auto_sync(self) -> None:
        """
        Starts a background thread for regular local directory synchronization.
        """
        if self.sync_thread is not None and self.sync_thread.is_alive():
            logger.info("Synchronisierungsthread läuft bereits")
            return
            
        def sync_task():
            while self.auto_sync:
                try:
                    logger.info(f"Automatische Synchronisierung des lokalen Verzeichnisses {self.local_path}")
                    self.sync_directory()
                    self.last_sync_time = datetime.now()
                    logger.info(f"Nächste Synchronisierung in {self.sync_interval} Sekunden")
                except Exception as e:
                    logger.error(f"Fehler bei der automatischen Synchronisierung: {str(e)}")
                
                time.sleep(self.sync_interval)
        
        self.sync_thread = threading.Thread(target=sync_task, daemon=True)
        self.sync_thread.start()
        logger.info(f"Automatische Synchronisierung gestartet (Intervall: {self.sync_interval}s)")
    
    def stop_auto_sync(self) -> None:
        """
        Stops the automatic local directory synchronization.
        """
        if self.sync_thread is not None:
            # Thread cannot be stopped directly, set a marker
            self.auto_sync = False
            logger.info("Automatische Synchronisierung wird beim nächsten Zyklus gestoppt")
    
    def sync_directory(self, force_rescan: bool = False) -> Dict[str, Any]:
        """
        Synchronizes the local directory and updates the index on changes.
        
        Args:
            force_rescan: If True, scans all files regardless of their hash
            
        Returns:
            Dict with information about the synchronization process
        """
        start_time = time.time()
        
        try:
            desc = "Vollständiger Scan (erzwungen)" if force_rescan else "Scan nach Änderungen"
            logger.info(f"{desc} des Verzeichnisses {self.local_path}")
            progress.start(desc=desc, total=2, unit="schritte")
            
            # Scan for changed files
            changed_files = self.scan_manager.scan_for_changes(force_rescan)
            progress.update(1)
            
            if changed_files:
                logger.info(f"{len(changed_files)} geänderte Dateien gefunden, aktualisiere Index")
                
                # Convert to absolute paths for processing
                abs_changed_files = {self.scan_manager.get_full_path(rel_path) for rel_path in changed_files}
                self.process_changed_files(abs_changed_files)
                
                # Reset cache for file structure
                self.file_tree_cache = None
                
                elapsed = time.time() - start_time
                progress.finish(f"{len(changed_files)} Dateien in {elapsed:.2f}s verarbeitet")
                
                return {
                    "status": "success", 
                    "changed_files": list(changed_files),
                    "execution_time_seconds": elapsed,
                    "timestamp": datetime.now().isoformat()
                }
            else:
                elapsed = time.time() - start_time
                logger.info(f"Keine Änderungen im lokalen Verzeichnis gefunden (Scan in {elapsed:.2f}s)")
                progress.finish(f"Keine Änderungen gefunden (Scan in {elapsed:.2f}s)")
                
                return {
                    "status": "success", 
                    "changed_files": [], 
                    "execution_time_seconds": elapsed,
                    "message": "Keine Änderungen gefunden",
                    "timestamp": datetime.now().isoformat()
                }
                
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Fehler bei der Verzeichnissynchronisierung: {str(e)}")
            progress.finish(f"Fehler beim Scan: {str(e)}")
            return {
                "status": "error", 
                "message": str(e),
                "execution_time_seconds": elapsed
            }
            
    def retrieve(self, query: str, top_k: int = 5, include_dependencies: bool = True) -> List[Dict[str, Any]]:
        """
        Retrieves the top-k relevant chunks for a natural language query.
        Optionally includes dependent chunks as well.
        
        Args:
            query: The natural language query
            top_k: Number of results to return
            include_dependencies: If True, related files are also included
            
        Returns:
            List of Dicts with 'score' and 'metadata'
        """
        start_time = time.time()
        logger.info(f"Suche relevante Code-Chunks für Query: '{query}'")
        
        # Führe Vektorsuche durch
        raw_matches = self.embed_mgr.query(query, top_k=top_k)
        
        if not include_dependencies:
            elapsed = time.time() - start_time
            logger.info(f"{len(raw_matches)} relevante Chunks gefunden in {elapsed:.2f}s")
            return raw_matches
            
        # Erweitere mit abhängigen Dateien
        progress.start(desc="Erweitere mit Abhängigkeiten", total=1, unit="step")
        enhanced_matches = self._enhance_with_dependencies(raw_matches)
        
        elapsed = time.time() - start_time
        added_deps = len(enhanced_matches) - len(raw_matches)
        progress.finish(f"{len(raw_matches)} direkte Matches + {added_deps} Abhängigkeiten gefunden")
        
        logger.info(f"Insgesamt {len(enhanced_matches)} Chunks gefunden (mit Abhängigkeiten) in {elapsed:.2f}s")
        return enhanced_matches
    
    def _enhance_with_dependencies(self, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Enhances the matches found with relevant dependencies from the dependency graph.
        
        Args:
            matches: List of original matches
            
        Returns:
            Enhanced list with additional dependent files/functions
        """
        enhanced = matches.copy()
        seen = set(f"{m['metadata']['path']}::{m['metadata']['name']}" for m in matches)
        
        # Load Dependency Graph
        try:
            with open("dependency_graph.json", "r", encoding="utf-8") as f:
                graph = json.load(f)
        except FileNotFoundError:
            logger.warning("Dependency graph nicht gefunden, überspringe Abhängigkeitsanalyse")
            return enhanced
        
        # Collect all important dependent nodes
        dependencies = set()
        for match in matches:
            meta = match["metadata"]
            key = f"{meta['path']}::{meta['name']}"
            if key in graph:
                # Direct calls
                dependencies.update(graph[key].get("calls", []))
                # Called by
                dependencies.update(graph[key].get("called_by", []))
        
        # Find matching entries in the graph
        for dep_name in dependencies:
            for key, node in graph.items():
                if node["name"] == dep_name and key not in seen:
                    seen.add(key)
                    
                    # Search for corresponding metadata
                    for idx, meta in enumerate(self.embed_mgr.metadata):
                        if meta["path"] == node["file"] and meta["name"] == node["name"]:
                            enhanced.append({
                                "score": 0.0,  # Dependency has no relevance score
                                "metadata": meta,
                                "is_dependency": True
                            })
                            break
        
        return enhanced

    def build_prompt(self, query: str, top_k: int = 5, max_context_length: int = 8000) -> str:
        """
        Creates a prompt for the LLM that includes the query, relevant context,
        and function dependencies (calls and callers).
        
        Args:
            query: The natural language query
            top_k: Number of results to include
            max_context_length: Maximum length of the context (in characters)
            
        Returns:
            Formatted prompt text for the LLM
        """
        start_time = time.time()
        logger.info(f"Erzeuge Prompt für Query: '{query}' (max. {max_context_length} Zeichen)")
        progress.start(desc="Suche relevante Code-Chunks", total=3, unit="schritte")
        
        # Finde relevante Chunks
        matches = self.retrieve(query, top_k)
        progress.update(1)
        
        # Initialisiere Tracking-Variablen
        seen = set()
        context_blocks = []
        context_length = 0

        # Lade Dependency Graph
        try:
            with open("dependency_graph.json", "r", encoding="utf-8") as f:
                graph = json.load(f)
            progress.update(1)
        except FileNotFoundError:
            logger.warning("Dependency graph nicht gefunden")
            graph = {}
            progress.update(1)

        # Hilfsfunktion: angrenzende Funktionsnamen holen
        def get_related_keys(match_meta):
            key = f"{match_meta['path']}::{match_meta['name']}"
            if key not in graph:
                return set()
            return set(graph[key].get("calls", [])) | set(graph[key].get("called_by", []))

        # Erstelle initialen Match-Index für schnellere Lookups
        all_chunks = {f"{m['metadata']['path']}::{m['metadata']['name']}": m for m in matches}
        
        logger.info(f"Sammle relevante Code-Chunks für Kontext (max. Länge: {max_context_length})")
        
        # Lokaler Fortschrittsbalken für Chunks
        sub_progress = tqdm(matches, desc="Sammle Code-Chunks", unit="chunks")

        # Sammle relevante Chunks, beginnend mit den relevantesten
        for match in sub_progress:
            meta = match["metadata"]
            key = f"{meta['path']}::{meta['name']}"
            if key in seen:
                continue
                
            # Prüfe, ob wir noch Platz haben
            chunk_text = f"# File: {key} (Score: {match.get('score', 0):.4f})\n{meta['code']}\n"
            if context_length + len(chunk_text) > max_context_length:
                # Wenn kein Platz mehr ist, überspringe
                continue
                
            seen.add(key)
            context_blocks.append(chunk_text)
            context_length += len(chunk_text)
            sub_progress.set_description(f"Kontext: {context_length}/{max_context_length} Zeichen")

            # Verwandte Chunks einbeziehen (aus Graph), wenn Platz
            for rel_name in get_related_keys(meta):
                rel_key = next((k for k in all_chunks if k.endswith(f"::{rel_name}")), None)
                if rel_key and rel_key not in seen:
                    rel_meta = all_chunks[rel_key]["metadata"]
                    rel_text = f"# File: {rel_key} (related)\n{rel_meta['code']}\n"
                    
                    # Prüfe, ob wir noch Platz haben
                    if context_length + len(rel_text) > max_context_length:
                        continue
                        
                    seen.add(rel_key)
                    context_blocks.append(rel_text)
                    context_length += len(rel_text)
                    sub_progress.set_description(f"Kontext: {context_length}/{max_context_length} Zeichen")
        
        sub_progress.close()
        
        # Erstelle Prompt mit Kontext
        context = "\n".join(context_blocks)
        prompt = (
            "Du bist ein hochprofessioneller Code-Assistent. Verwende den folgenden Kontext aus dem lokalen Verzeichnis, um die Frage zu beantworten.\n"
            f"Kontext:\n{context}\n"
            f"Frage: {query}\n"
            "Antworte mit Code-Snippets und Erklärungen nach Bedarf. Wenn der Kontext nicht ausreicht, gib an, welche Informationen fehlen."
        )
        
        elapsed = time.time() - start_time
        progress.update(1)
        progress.finish(f"Prompt mit {len(context_blocks)} Code-Chunks erstellt ({context_length} Zeichen, {elapsed:.2f}s)")
        
        logger.info(f"Prompt erstellt: {len(seen)} von {len(matches)} Chunks verwendet, Gesamtlänge: {len(prompt)} Zeichen")
        return prompt
    
    def get_file_structure(self, force_refresh: bool = False, depth: int = 0, path: str = None) -> Dict[str, Any]:
        """
        Generates a hierarchical representation of files in the local directory.
        
        Args:
            force_refresh: If True, cache is ignored and rebuilt
            depth: Maximum depth of the directory structure to return (0 = unlimited)
            path: Optional relative path within the project to only show a subdirectory
            
        Returns:
            Dict with the hierarchical file structure
        """
        start_time = time.time()
        
        # Use cache if available and no refresh is forced (but only for standard requests)
        if self.file_tree_cache is not None and not force_refresh and path is None and depth == 1:
            logger.info("Verwende gecachte Dateistruktur")
            return self.file_tree_cache
        
        # Determine start path (full directory or subpath)
        start_path = self.local_path
        if path:
            # Normalize path to handle both Windows and Unix-style paths
            norm_path = os.path.normpath(path)
            full_path = os.path.join(self.local_path, norm_path)
            
            if os.path.exists(full_path) and os.path.isdir(full_path):
                start_path = full_path
                logger.info(f"Verwende Unterpfad: {path} -> {start_path}")
            else:
                error_msg = f"Pfad nicht gefunden: {path}"
                logger.error(error_msg)
                return {"error": error_msg}
        
        logger.info(f"Erzeuge Dateistruktur für {os.path.relpath(start_path, self.local_path)} mit Tiefe {depth}")
        progress.start(desc="Scanne Verzeichnisstruktur", total=1, unit="dir")
        
        # Helper functions for counting
        def _count_files(tree_node):
            if not isinstance(tree_node, dict):
                return
            for key, value in tree_node.items():
                if isinstance(value, dict) and "type" in value and value["type"] == "file":
                    yield 1
                elif isinstance(value, dict) and "type" not in value:
                    yield from _count_files(value)
        
        def _count_dirs(tree_node):
            if not isinstance(tree_node, dict):
                return
            for key, value in tree_node.items():
                if isinstance(value, dict) and "type" not in value:
                    yield 1
                    yield from _count_dirs(value)
                elif isinstance(value, dict) and "type" in value and value["type"] == "directory":
                    yield 1
        
        # Recursive function to build the folder structure with depth limit
        def build_tree(current_path, current_depth=0):
            result = {}
            
            try:
                items = os.listdir(current_path)
            except PermissionError:
                logger.warning(f"Keine Leserechte für: {current_path}")
                return {"error": "Keine Leserechte"}
            except Exception as e:
                logger.error(f"Fehler beim Lesen von {current_path}: {str(e)}")
                return {"error": str(e)}
            
            sub_progress = tqdm(items, desc=f"Scanne {os.path.basename(current_path)}", leave=False)
            
            for item in sub_progress:
                # Ignore directories that should be skipped
                if item.startswith('.') or item in self.scan_manager.skip_dirs:
                    continue
                    
                full_item_path = os.path.join(current_path, item)
                rel_item_path = os.path.relpath(full_item_path, self.local_path)
                
                if os.path.isdir(full_item_path):
                    # Check depth limit before recursion
                    if depth > 0 and current_depth >= depth:
                        # If at depth limit, just indicate directory exists
                        result[item] = {
                            "type": "directory",
                            "path": rel_item_path,
                            "truncated": True
                        }
                    else:
                        # Continue recursion within depth limit
                        child_tree = build_tree(full_item_path, current_depth + 1)
                        # If we're directly looking at a specific path, make this the root
                        if path and current_path == start_path:
                            return child_tree
                        result[item] = child_tree
                else:
                    # For files: size and last modified date
                    try:
                        stat = os.stat(full_item_path)
                        result[item] = {
                            "type": "file",
                            "size": stat.st_size,
                            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                            "path": rel_item_path
                        }
                    except Exception as e:
                        logger.warning(f"Fehler beim Abrufen von Dateiinformationen für {full_item_path}: {str(e)}")
                        result[item] = {
                            "type": "file",
                            "error": str(e),
                            "path": rel_item_path
                        }
            
            return result

        try:
            # Special case - if we're looking at a specific path
            if path:
                # Build tree directly from the specific path
                tree_data = build_tree(start_path)
                
                # Wrap in a more consistent structure
                tree = {
                    "path": os.path.relpath(start_path, self.local_path),
                    "content": tree_data
                }
            else:
                # Standard case - full directory
                tree = {"root": build_tree(start_path)}
                
                # Only cache the default view (no specific path, default depth)
                self.file_tree_cache = tree
                self.file_tree_last_updated = datetime.now()
            
            elapsed = time.time() - start_time
            
            # Count files and directories, handling both formats
            if path:
                file_count = sum(1 for _ in _count_files(tree["content"]))
                dir_count = sum(1 for _ in _count_dirs(tree["content"]))
            else:
                file_count = sum(1 for _ in _count_files(tree["root"]))
                dir_count = sum(1 for _ in _count_dirs(tree["root"]))
            
            progress.finish(f"Dateistruktur erstellt: {file_count} Dateien, {dir_count} Verzeichnisse in {elapsed:.2f}s")
            logger.info(f"Dateistruktur erfolgreich erstellt in {elapsed:.2f}s")
            
            return tree
        except Exception as e:
            logger.error(f"Fehler beim Erstellen der Dateistruktur: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            progress.finish(f"Fehler beim Erstellen der Dateistruktur: {str(e)}")
            return {"error": str(e)}
            
    def get_file_content(self, file_path: str) -> Dict[str, Any]:
        """
        Reads the content of a file from the local directory.
        
        Args:
            file_path: Relative path to the file in the local directory
            
        Returns:
            Dict with file content and metadata
        """
        start_time = time.time()
        try:
            logger.info(f"Lese Dateiinhalt: {file_path}")
            progress.start(desc=f"Lese Datei {os.path.basename(file_path)}", total=2, unit="steps")
            
            abs_path = os.path.join(self.local_path, file_path)
            if not os.path.exists(abs_path) or not os.path.isfile(abs_path):
                progress.finish(f"Datei nicht gefunden: {file_path}")
                return {"status": "error", "message": "Datei nicht gefunden"}
                
            with open(abs_path, 'r', encoding='utf-8') as f:
                content = f.read()
            progress.update(1)
                
            # Metadaten erfassen
            stat = os.stat(abs_path)
            metadata = {
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "lines": content.count('\n') + 1
            }
            
            # Abhängigkeiten laden, falls verfügbar
            dependencies = {"calls": [], "called_by": []}
            try:
                with open("dependency_graph.json", "r", encoding='utf-8') as f:
                    graph = json.load(f)
                    for key, node in graph.items():
                        if node["file"] == file_path:
                            dependencies["calls"].extend(node.get("calls", []))
                            dependencies["called_by"].extend(node.get("called_by", []))
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            
            progress.update(1)
            elapsed = time.time() - start_time
            progress.finish(f"Datei gelesen: {metadata['lines']} Zeilen, {metadata['size']} Bytes in {elapsed:.2f}s")
                
            return {
                "status": "success",
                "content": content,
                "metadata": metadata,
                "dependencies": dependencies,
                "execution_time_seconds": elapsed
            }
            
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Fehler beim Lesen der Datei {file_path}: {str(e)}")
            progress.finish(f"Fehler beim Lesen der Datei: {str(e)}")
            return {
                "status": "error", 
                "message": str(e), 
                "execution_time_seconds": elapsed
            }
    
    def get_dependency_graph(self) -> Dict[str, Any]:
        """
        Returns the current dependency graph.
        
        Returns:
            Dict with the dependency graph or error message
        """
        start_time = time.time()
        try:
            logger.info("Lade Abhängigkeitsgraph")
            progress.start(desc="Lade Abhängigkeitsgraph", total=1, unit="graph")
            
            with open("dependency_graph.json", "r", encoding='utf-8') as f:
                graph = json.load(f)
            
            node_count = len(graph)
            
            # Zähle Verbindungen
            edges = 0
            for node_key, node in graph.items():
                edges += len(node.get("calls", []))
                edges += len(node.get("called_by", []))
            edges = edges // 2  # Jede Kante wird doppelt gezählt
            
            elapsed = time.time() - start_time
            progress.finish(f"Graph geladen: {node_count} Knoten, {edges} Kanten in {elapsed:.2f}s")
            
            return {
                "status": "success", 
                "graph": graph,
                "node_count": node_count,
                "edge_count": edges,
                "execution_time_seconds": elapsed
            }
        except FileNotFoundError:
            elapsed = time.time() - start_time
            logger.warning("Abhängigkeitsgraph nicht gefunden")
            progress.finish("Abhängigkeitsgraph nicht gefunden")
            return {
                "status": "error", 
                "message": "Abhängigkeitsgraph nicht gefunden",
                "execution_time_seconds": elapsed
            }
        except json.JSONDecodeError:
            elapsed = time.time() - start_time
            logger.error("Fehler beim Parsen des Abhängigkeitsgraphen")
            progress.finish("Fehler beim Parsen des Abhängigkeitsgraphen")
            return {
                "status": "error", 
                "message": "Fehler beim Parsen des Abhängigkeitsgraphen",
                "execution_time_seconds": elapsed
            }

    def handle_file_changes(self, changed_files: Set[str]) -> None:
        """
        Callback method for the file system monitor to process changed files.
        
        Args:
            changed_files: Set of absolute paths to changed files
        """
        logger.info(f"Event: {len(changed_files)} geänderte Dateien erkannt")
        progress.start(desc=f"Verarbeite {len(changed_files)} geänderte Dateien", total=1, unit="batch")
        self.process_changed_files(changed_files)
        progress.finish("Dateiänderungen verarbeitet")

    def process_changed_files(self, changed_files: Set[str]) -> None:
        """
        Process a set of changed files to update the index and dependency graph.
        Removes existing chunks for changed files before adding new ones to avoid duplicates.
        
        Args:
            changed_files: Set of absolute paths to changed files
        """
        if not changed_files:
            logger.info("Keine Dateien zum Verarbeiten")
            return
            
        logger.info(f"Verarbeite {len(changed_files)} geänderte Dateien")
        progress.start(desc="Aktualisiere Abhängigkeitsgraph", total=1, unit="graph")
        
        # Update dependency graph
        self.graph_builder.build_graph()
        progress.finish("Abhängigkeitsgraph aktualisiert")
        
        # Convert absolute paths to relative for index operations
        relative_changed_paths = set()
        for abs_path in changed_files:
            if os.path.exists(abs_path):
                rel_path = os.path.relpath(abs_path, self.local_path)
                relative_changed_paths.add(rel_path)
        
        # First remove existing chunks for all changed files to avoid duplicates
        if relative_changed_paths:
            logger.info(f"Entferne vorhandene Chunks für {len(relative_changed_paths)} geänderte Dateien")
            progress.start(desc="Entferne vorhandene Chunks", total=1, unit="operation")
            self.embed_mgr.remove_chunks(list(relative_changed_paths))
            progress.finish("Vorhandene Chunks entfernt")
        
        # Process each changed file
        changed_chunks = []
        progress.start(desc="Verarbeite geänderte Dateien", total=len(changed_files), unit="dateien")
        
        for abs_path in changed_files:
            try:
                if os.path.exists(abs_path):  # File was added or modified
                    # Only process files with tracked extensions
                    if any(abs_path.endswith(ext) for ext in self.scan_manager.tracked_extensions):
                        with open(abs_path, "r", encoding="utf-8") as f:
                            content = f.read()
                            self.change_analyzer.track_change(abs_path, content)
                        
                        file_chunks = list(self.preprocessor.extract_chunks(abs_path))
                        for chunk in file_chunks:
                            changed_chunks.append(chunk.to_dict())
                        logger.info(f"Datei '{os.path.basename(abs_path)}' verarbeitet: {len(file_chunks)} Chunks extrahiert")
                else:  # File was deleted - we've already removed it earlier
                    logger.info(f"Datei '{os.path.basename(abs_path)}' wurde gelöscht und aus dem Index entfernt")
            except Exception as e:
                logger.error(f"Fehler beim Verarbeiten von {abs_path}: {str(e)}")
            
            progress.update(1)
        
        progress.finish(f"{len(changed_files)} Dateien verarbeitet, {len(changed_chunks)} Chunks extrahiert")
        
        # Update the embeddings for all changed chunks
        if changed_chunks:
            logger.info(f"Aktualisiere Embeddings für {len(changed_chunks)} Chunks")
            self.embed_mgr.upsert_chunks(changed_chunks)
        else:
            logger.info("Keine Chunks zum Aktualisieren")

    def _determine_file_encoding(self, file_path: str) -> str:
        """
        Try to determine the correct encoding of a file.
        
        Args:
            file_path: Path to the file
            
        Returns:
            Encoding name to use
        """
        # Try these encodings in order
        encodings = ['utf-8', 'latin-1', 'cp1252', 'utf-16']
        
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    # Try to read a small portion to check encoding
                    f.read(1024)
                return encoding
            except UnicodeDecodeError:
                continue
        
        # If all fail, default to latin-1 which should read any file
        return 'latin-1'

    def _should_process_file(self, file_path: str) -> bool:
        """
        Check if a file should be processed based on extension.
        
        Args:
            file_path: Path to the file
            
        Returns:
            True if file should be processed, False otherwise
        """
        # Get file extension
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()
        
        # Check against tracked extensions
        return ext in self.scan_manager.tracked_extensions

    def build_index(self) -> Dict[str, Any]:
        """
        Performs preprocessing and populates the FAISS index with embeddings.
        Also generates the dependency graph.
        
        Returns:
            Dict with information about the build process
        """
        try:
            start_time = time.time()
            logger.info(f"Starte Vollständigen Index-Aufbau für Verzeichnis {self.local_path}")
            progress.start(desc="Verarbeite Code-Chunks", total=3, unit="schritte")
            
            # Starte den Preprocessing-Prozess
            logger.info("Extrahiere Code-Chunks...")
            chunks = self.preprocessor.process()
            chunk_count = len(chunks)
            logger.info(f"{chunk_count} Code-Chunks extrahiert")
            progress.update(1)
            
            # Erzeuge Embeddings und speichere im Index
            logger.info(f"Erzeuge Embeddings für {chunk_count} Chunks...")
            self.embed_mgr.upsert_chunks(chunks)
            progress.update(1)

            # Erzeuge Abhängigkeitsgraph
            logger.info("Baue Abhängigkeitsgraph...")
            self.graph_builder.build_graph()
            progress.update(1)
            
            # Reset cache for file structure
            self.file_tree_cache = None
            
            end_time = time.time()
            elapsed = end_time - start_time
            
            progress.finish(f"Index mit {chunk_count} Chunks in {elapsed:.2f}s aufgebaut")
            
            return {
                "status": "success",
                "chunks_processed": chunk_count,
                "execution_time_seconds": elapsed,
                "time_taken": round(elapsed, 2),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Fehler beim Aufbau des Index: {str(e)}")
            progress.finish(f"Fehler beim Aufbau des Index: {str(e)}")
            return {"status": "error", "message": str(e)}

    def update_context_on_change(self) -> Dict[str, Any]:
        """
        Updates FAISS index and dependency graph based on changed files.
        Tracks versions via ChangeAnalyzer and saves changed state.
        
        Returns:
            Dict with information about the update process
        """
        try:
            start_time = time.time()
            logger.info("Überprüfe auf Änderungen im lokalen Verzeichnis...")
            
            # Scan for changes
            progress.start(desc="Prüfe auf Änderungen", total=1, unit="scan")
            changed_files = self.scan_manager.scan_for_changes()
            progress.finish(f"{len(changed_files)} geänderte Dateien gefunden")
            
            if changed_files:
                logger.info(f"{len(changed_files)} geänderte Dateien gefunden")
                # Convert to absolute paths for processing
                abs_changed_files = {self.scan_manager.get_full_path(rel_path) for rel_path in changed_files}
                self.process_changed_files(abs_changed_files)
                
                elapsed = time.time() - start_time
                return {
                    "status": "success",
                    "changed_files": list(changed_files),
                    "execution_time_seconds": elapsed,
                    "timestamp": datetime.now().isoformat()
                }
            else:
                logger.info("Keine Änderungen gefunden")
                elapsed = time.time() - start_time
                return {
                    "status": "success",
                    "changed_files": [],
                    "execution_time_seconds": elapsed,
                    "message": "Keine Änderungen gefunden",
                    "timestamp": datetime.now().isoformat()
                }
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"Fehler beim Aktualisieren des Kontexts: {str(e)}")
            return {
                "status": "error", 
                "message": str(e),
                "execution_time_seconds": elapsed
            }