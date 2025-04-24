import os
import ast
import json
import hashlib
import logging
import time
from typing import Dict, List, Set
from tqdm import tqdm
from app import progress

logger = logging.getLogger(__name__)

class DependencyGraphBuilder:
    def __init__(self, repo_path: str, output_file: str = "dependency_graph.json", hash_file: str = "file_hashes.json"):
        self.repo_path = repo_path
        self.output_file = output_file
        self.hash_file = hash_file
        self.graph: Dict[str, Dict] = {}
        self.file_hashes: Dict[str, str] = {}
        self.changed_files: Set[str] = set()
        logger.info(f"DependencyGraphBuilder initialisiert für: {repo_path}")

    def build_graph(self) -> Dict[str, Dict]:
        """
        Baut einen Abhängigkeitsgraphen basierend auf Funktionsaufrufen im Python-Code.
        Verfolgt geänderte Dateien seit dem letzten Build.

        Returns:
            Dependency graph dictionary
        """
        start_time = time.time()
        logger.info(f"Baue Abhängigkeitsgraph für {self.repo_path}")
        progress.start(desc="Baue Abhängigkeitsgraph", total=4, unit="schritte")
        
        self.changed_files.clear()
        processed_files = 0
        skipped_files = 0
        changed_count = 0
        
        # Schritt 1: Finde alle Python-Dateien und überprüfe Änderungen
        logger.info("Suche nach Python-Dateien und überprüfe Änderungen...")
        all_python_files = []
        
        for root, _, files in os.walk(self.repo_path):
            for file in files:
                if file.endswith(".py") and not file.startswith("test_"):
                    full_path = os.path.join(root, file)
                    all_python_files.append((full_path, os.path.relpath(full_path, self.repo_path)))
        
        progress.update(1)
        
        # Schritt 2: Verarbeite Dateien und überprüfe Änderungen
        if all_python_files:
            logger.info(f"Überprüfe Änderungen in {len(all_python_files)} Python-Dateien...")
            file_progress = tqdm(all_python_files, desc="Prüfe Dateien", unit="dateien")
            
            for full_path, rel_path in file_progress:
                file_progress.set_description(f"Prüfe {os.path.basename(full_path)}")
                if self._file_changed(full_path, rel_path):
                    file_progress.set_description(f"Verarbeite {os.path.basename(full_path)}")
                    self._process_file(rel_path, full_path)
                    self.changed_files.add(rel_path)
                    changed_count += 1
                    processed_files += 1
                else:
                    skipped_files += 1
            
            progress.update(1)
            
            # Schritt 3: Erstelle umgekehrte Beziehungen
            logger.info("Erstelle umgekehrte Aufrufbeziehungen...")
            for src, meta in self.graph.items():
                for called in meta["calls"]:
                    for target, t_meta in self.graph.items():
                        if t_meta["name"] == called:
                            t_meta.setdefault("called_by", []).append(meta["name"])
                            
            progress.update(1)
            
            # Schritt 4: Speichere Graph und Hashes
            logger.info("Speichere Abhängigkeitsgraph und Datei-Hashes...")
            self._save_graph()
            self._save_hashes()
            
            elapsed = time.time() - start_time
            node_count = len(self.graph)
            edges = sum(len(node.get("calls", [])) for node in self.graph.values())
            
            # Fertig
            progress.finish(f"Graph erstellt: {node_count} Knoten, {edges} Kanten, {changed_count} Dateien aktualisiert")
            logger.info(f"Abhängigkeitsgraph erstellt in {elapsed:.2f}s: {node_count} Knoten, {edges} Kanten")
        else:
            logger.warning("Keine Python-Dateien gefunden")
            progress.finish("Keine Python-Dateien gefunden")
        
        return self.graph

    def _file_changed(self, full_path: str, rel_path: str) -> bool:
        """Check if file content hash has changed since last build."""
        try:
            with open(full_path, 'rb') as f:
                content = f.read()
                sha256 = hashlib.sha256(content).hexdigest()
            if not os.path.exists(self.hash_file):
                return True
            if rel_path not in self.file_hashes:
                return True
            return self.file_hashes[rel_path] != sha256
        except Exception as e:
            logger.error(f"Fehler beim Prüfen von Änderungen in {full_path}: {str(e)}")
            return True  # Bei Fehlern besser neu verarbeiten

    def _process_file(self, rel_path: str, full_path: str):
        """
        Process a Python file and extract dependencies with improved error handling.
        """
        try:
            # Try to detect encoding
            encoding = self._detect_file_encoding(full_path)
            
            with open(full_path, 'r', encoding=encoding) as f:
                source = f.read()
            
            # AST-Parsing
            try:
                tree = ast.parse(source)
            except SyntaxError as e:
                logger.error(f"Syntax error in {rel_path}: {str(e)}")
                # Still update the hash to avoid reprocessing
                self._update_file_hash(full_path, rel_path)
                return

            # Update hash
            self._update_file_hash(full_path, rel_path)

            # Track function calls
            calls = {}  # name -> called function names
            defined = set()  # defined function/class names
            current_function = None  # Currently processed function

            class FunctionCallVisitor(ast.NodeVisitor):
                def visit_Call(self, node):
                    if isinstance(node.func, ast.Name):
                        calls.setdefault(current_function, set()).add(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        calls.setdefault(current_function, set()).add(node.func.attr)
                    self.generic_visit(node)

            # Process functions and classes
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    start_line = node.lineno
                    end_line = max(getattr(n, 'lineno', start_line) for n in ast.walk(node))
                    current_function = node.name
                    defined.add(current_function)
                    FunctionCallVisitor().visit(node)

            # Create graph entries
            for func, called in calls.items():
                self.graph[f"{rel_path}::{func}"] = {
                    "file": rel_path,
                    "name": func,
                    "calls": list(called),
                    "called_by": []
                }
                
            logger.debug(f"File {rel_path} processed: {len(calls)} functions with calls found")
            
        except UnicodeDecodeError as e:
            logger.error(f"Encoding error processing {full_path}: {str(e)}")
            # Still update hash to prevent continuous reprocessing
            self._update_file_hash(full_path, rel_path)
        except Exception as e:
            logger.error(f"Error processing {full_path}: {str(e)}")
            # Update hash even on error
            self._update_file_hash(full_path, rel_path)

    def _detect_file_encoding(self, file_path: str) -> str:
        """
        Try to detect the correct encoding of a file.
        """
        encodings = ['utf-8', 'latin-1', 'cp1252', 'utf-16']
        
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    f.read(1024)  # Read a small part to test
                return encoding
            except UnicodeDecodeError:
                continue
        
        # Default to latin-1 which can read almost any file
        return 'latin-1'

    def _update_file_hash(self, full_path: str, rel_path: str):
        """
        Update the hash of a file regardless of encoding issues.
        """
        try:
            # Read in binary mode to calculate hash without encoding issues
            with open(full_path, 'rb') as f:
                content = f.read()
                sha256 = hashlib.sha256(content).hexdigest()
            self.file_hashes[rel_path] = sha256
        except Exception as e:
            logger.error(f"Error updating hash for {full_path}: {str(e)}")
            # Generate a random hash to mark as processed
            import uuid
            self.file_hashes[rel_path] = f"error-{uuid.uuid4().hex}"

    def _save_graph(self):
        """Speichert den Abhängigkeitsgraphen in eine JSON-Datei."""
        try:
            with open(self.output_file, 'w', encoding='utf-8') as f:
                json.dump(self.graph, f, indent=2)
            logger.info(f"Abhängigkeitsgraph in {self.output_file} gespeichert")
        except Exception as e:
            logger.error(f"Fehler beim Speichern des Abhängigkeitsgraphen: {str(e)}")

    def _save_hashes(self):
        """Speichert die Datei-Hashes in eine JSON-Datei."""
        try:
            with open(self.hash_file, 'w', encoding='utf-8') as f:
                json.dump(self.file_hashes, f, indent=2)
            logger.info(f"Datei-Hashes in {self.hash_file} gespeichert: {len(self.file_hashes)} Einträge")
        except Exception as e:
            logger.error(f"Fehler beim Speichern der Datei-Hashes: {str(e)}")

    def load_hashes(self):
        if os.path.exists(self.hash_file):
            with open(self.hash_file, 'r', encoding='utf-8') as f:
                self.file_hashes = json.load(f)

# Integration Example (to be used in RAGManager):
# builder = DependencyGraphBuilder("./your_repo")
# builder.load_hashes()
# graph = builder.build_graph()
# changed = builder.changed_files
