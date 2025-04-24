import os
import ast
import time
import logging
import json
from typing import List, Dict, Optional, Set
from tqdm import tqdm
from app import progress

logger = logging.getLogger(__name__)

class CodeChunk:
    """
    Represents a chunk of code extracted from the repository.
    """
    def __init__(self, path: str, name: str, start_line: int, end_line: int, code: str):
        self.path = path
        self.name = name
        self.start_line = start_line
        self.end_line = end_line
        self.code = code

    def to_dict(self) -> Dict[str, str]:
        return {
            "path": self.path,
            "name": self.name,
            "start_line": str(self.start_line),
            "end_line": str(self.end_line),
            "code": self.code
        }

class Preprocessor:
    """
    Scans a directory for files, parses them, and extracts:
      - entire file as a single chunk
      - for Python files: top-level functions, classes, and class methods
      - for other files: appropriate chunks based on file type
    Skips test files (starting with 'test_').
    """
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        # Supported file extensions with appropriate processing
        self.supported_extensions = {
            '.py': self._process_python_file,
            '.js': self._process_simple_file,
            '.ts': self._process_simple_file,
            '.tsx': self._process_simple_file,
            '.jsx': self._process_simple_file,
            '.json': self._process_json_file,
            '.md': self._process_simple_file,
            '.html': self._process_simple_file,
            '.css': self._process_simple_file,
            '.scss': self._process_simple_file,
            '.less': self._process_simple_file,
            '.txt': self._process_simple_file,
            '.yaml': self._process_simple_file,
            '.yml': self._process_simple_file,
            '.xml': self._process_simple_file,
            '.csv': self._process_simple_file,
            '.sql': self._process_simple_file,
            '': self._process_simple_file
        }
        # Directories to skip
        self.skip_dirs = {
            'venv', '__pycache__', 'node_modules', '.git', '.idea', '.vscode',
            'build', 'dist', '.pytest_cache', '.mypy_cache', '.egg-info'
        }
        # Maximum file size to process (50MB)
        self.max_file_size = 50 * 1024 * 1024
        self.supported_extensions['.'] = self._process_simple_file
        logger.info(f"Preprocessor initialized for: {repo_path}")

    def scan_files(self) -> List[str]:
        """
        Walk through the repo and return all processable file paths,
        skipping specified directories and test files.
        """
        logger.info(f"Scanning files in {self.repo_path}")
        progress.start(desc="Searching files", total=1, unit="scan")
        
        files_to_process = []
        processed_dirs = 0
        skipped_dirs = 0
        skipped_files = 0
        
        for root, dirs, files in os.walk(self.repo_path):
            # Skip hidden and specified directories
            orig_dirs_count = len(dirs)
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in self.skip_dirs]
            skipped_dirs += orig_dirs_count - len(dirs)
            processed_dirs += 1
            
            for f in files:
                # Skip test files and check file extension
                if f.startswith('test_'):
                    skipped_files += 1
                    continue
                    
                file_ext = os.path.splitext(f)[1].lower()
                full_path = os.path.join(root, f)
                
                # Skip files that are too large
                try:
                    if os.path.getsize(full_path) > self.max_file_size:
                        logger.warning(f"Skipping large file: {full_path}")
                        skipped_files += 1
                        continue
                except OSError:
                    logger.warning(f"Cannot access file size: {full_path}")
                    skipped_files += 1
                    continue
                
                # Add file if we have a processor for its extension
                if file_ext in self.supported_extensions:
                    files_to_process.append(full_path)
                else:
                    skipped_files += 1
        
        progress.finish(f"{len(files_to_process)} files found, {skipped_files} files and {skipped_dirs} directories skipped")
        return files_to_process

    def _determine_encoding(self, file_path: str) -> str:
        """
        Try to determine the correct encoding of a file by attempting different encodings.
        Falls back to latin-1 which can read almost any file without raising an exception.
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

    def _process_large_file(self, file_path: str, source: str) -> List[CodeChunk]:
        """
        Verarbeitet große Dateien durch intelligentes Chunking in überlappende Segmente.
        Wird bei Dateien verwendet, die größer als ein bestimmter Schwellenwert sind.
        """
        lines = source.splitlines()
        rel_path = os.path.relpath(file_path, self.repo_path)
        total_lines = len(lines)
        chunks: List[CodeChunk] = []
        
        # 1) Ganzes Dokument als ein Chunk hinzufügen (für Kontext), aber nur wenn nicht zu groß
        if total_lines <= 5000:  # Nur für Files mit weniger als 5000 Zeilen
            chunks.append(CodeChunk(
                path=rel_path,
                name=f"{rel_path}:full",
                start_line=1,
                end_line=total_lines,
                code=source
            ))
        
        # 2) Teile die Datei in überlappende Segmente auf
        chunk_size = 1000  # Zeilen pro Chunk
        overlap = 100      # Überlappende Zeilen
        
        for start in range(0, total_lines, chunk_size - overlap):
            end = min(start + chunk_size, total_lines)
            
            # Verhindere zu kleine Chunks am Ende
            if end - start < 100 and len(chunks) > 0:
                break
                
            chunk_code = '\n'.join(lines[start:end])
            chunk_name = f"{rel_path}:{start+1}-{end}"
            
            chunks.append(CodeChunk(
                path=rel_path,
                name=chunk_name,
                start_line=start + 1,  # 1-basierter Zeilenindex
                end_line=end,
                code=chunk_code
            ))
        
        return chunks


    def extract_chunks(self, file_path: str) -> List[CodeChunk]:
        """
        Process a file based on its type and extract appropriate chunks.
        Now with improved handling for large files.
        """
        start_time = time.time()
        
        # Check if file exists
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return []

        # Get file size
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            logger.error(f"Cannot access file size: {file_path}")
            return []
            
        # Use efficient processing for very large files
        very_large_file = file_size > 10 * 1024 * 1024  # 10MB threshold
        
        # Determine encoding and read file
        try:
            encoding = self._determine_encoding(file_path)
            with open(file_path, 'r', encoding=encoding) as f:
                source = f.read()
        except Exception as e:
            logger.error(f"Error reading {file_path}: {str(e)}")
            return []
            
        # Special handling for very large files
        if very_large_file:
            logger.info(f"Large file detected ({file_size/1024/1024:.1f}MB): {file_path}, using stream processing")
            chunks = self._process_large_file(file_path, source)
            elapsed = time.time() - start_time
            logger.debug(f"Large file '{os.path.basename(file_path)}' processed: {len(chunks)} chunks in {elapsed:.3f}s")
            return chunks
        
        # Standard processing for regular files
        # Get file extension and determine processor
        file_ext = os.path.splitext(file_path)[1].lower()
        processor = self.supported_extensions.get(file_ext, self._process_simple_file)
        
        try:
            # Process file based on type
            chunks = processor(file_path, source)
            
            elapsed = time.time() - start_time
            logger.debug(f"File '{os.path.basename(file_path)}' processed: {len(chunks)} chunks in {elapsed:.3f}s")
            return chunks
            
        except Exception as e:
            # Log error but try to continue with a simple file chunk if possible
            logger.error(f"Error processing {file_path}: {str(e)}")
            try:
                # Fall back to simple processing
                chunks = self._process_simple_file(file_path, source)
                logger.info(f"Fallback processing for {file_path} extracted {len(chunks)} chunks")
                return chunks
            except Exception as fallback_error:
                logger.error(f"Fallback processing failed for {file_path}: {str(fallback_error)}")
                return []

    def _process_python_file(self, file_path: str, source: str) -> List[CodeChunk]:
        """
        Process Python files with AST to extract functions, classes, and methods.
        """
        lines = source.splitlines()
        rel_path = os.path.relpath(file_path, self.repo_path)
        total_lines = len(lines)
        chunks: List[CodeChunk] = []

        # 1) Whole-file chunk
        chunks.append(CodeChunk(
            path=rel_path,
            name=rel_path,
            start_line=1,
            end_line=total_lines,
            code=source
        ))

        # 2) AST-based chunks
        try:
            tree = ast.parse(source)
            
            # Top-level functions and async functions
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    start = node.lineno
                    end = max(getattr(child, 'lineno', start) for child in ast.walk(node))
                    snippet = '\n'.join(lines[start-1:end])
                    chunks.append(CodeChunk(rel_path, node.name, start, end, snippet))

            # Classes and their methods
            for node in tree.body:
                if isinstance(node, ast.ClassDef):
                    # Class-level chunk
                    start = node.lineno
                    end = max(getattr(child, 'lineno', start) for child in ast.walk(node))
                    class_code = '\n'.join(lines[start-1:end])
                    chunks.append(CodeChunk(rel_path, node.name, start, end, class_code))
                    
                    # Methods inside class
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            m_start = item.lineno
                            m_end = max(getattr(child, 'lineno', m_start) for child in ast.walk(item))
                            method_code = '\n'.join(lines[m_start-1:m_end])
                            method_name = f"{node.name}.{item.name}"
                            chunks.append(CodeChunk(rel_path, method_name, m_start, m_end, method_code))
        except SyntaxError as e:
            logger.warning(f"Syntax error in Python file {file_path}: {str(e)}")
            # We already have the whole-file chunk, so don't add anything else
        
        return chunks

    def _process_json_file(self, file_path: str, source: str) -> List[CodeChunk]:
        """
        Process JSON files, extracting top-level keys as separate chunks.
        """
        lines = source.splitlines()
        rel_path = os.path.relpath(file_path, self.repo_path)
        total_lines = len(lines)
        chunks: List[CodeChunk] = []

        # 1) Whole-file chunk
        chunks.append(CodeChunk(
            path=rel_path,
            name=rel_path,
            start_line=1,
            end_line=total_lines,
            code=source
        ))

        # 2) Try to parse JSON and extract top-level keys
        try:
            data = json.loads(source)
            if isinstance(data, dict):
                for key in data:
                    # Create a chunk for each top-level key
                    key_json = json.dumps({key: data[key]}, indent=2)
                    chunks.append(CodeChunk(
                        path=rel_path,
                        name=f"{rel_path}#{key}",
                        start_line=1,  # We don't know the exact line numbers
                        end_line=1,
                        code=key_json
                    ))
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in {file_path}")
            # We already have the whole-file chunk, so continue

        return chunks

    def _process_simple_file(self, file_path: str, source: str) -> List[CodeChunk]:
        """
        Process files simply by creating a single chunk for the entire file.
        Used for non-Python files or when detailed parsing fails.
        """
        lines = source.splitlines()
        rel_path = os.path.relpath(file_path, self.repo_path)
        total_lines = len(lines)
        
        # Just return a whole-file chunk
        return [CodeChunk(
            path=rel_path,
            name=rel_path,
            start_line=1,
            end_line=total_lines,
            code=source
        )]

    def process(self) -> List[Dict[str, str]]:
        """
        Scan all files and extract code chunks for the entire repo.
        Returns a list of dictionaries for each chunk.
        """
        start_time = time.time()
        
        logger.info("Starting processing of all files")
        progress.start(desc="Scanning files", total=3, unit="steps")
        
        # Step 1: Find all processable files
        py_files = self.scan_files()
        progress.update(1)
        
        if not py_files:
            logger.warning("No files found to process")
            progress.finish("No files found to process")
            return []
        
        # Step 2: Extract chunks from each file
        logger.info(f"Extracting code chunks from {len(py_files)} files")
        all_chunks: List[Dict[str, str]] = []
        
        sub_progress = tqdm(py_files, desc="Processing files", unit="files")
        total_chunks = 0
        
        for file_path in sub_progress:
            file_name = os.path.basename(file_path)
            sub_progress.set_description(f"Processing {file_name}")
            
            chunks = self.extract_chunks(file_path)
            total_chunks += len(chunks)
            
            for chunk in chunks:
                all_chunks.append(chunk.to_dict())
                
            sub_progress.set_description(f"Extracted {total_chunks} chunks so far")
        
        progress.update(1)
        
        elapsed = time.time() - start_time
        avg_per_file = total_chunks / len(py_files) if py_files else 0
        
        # Step 3: Finished
        logger.info(f"Processing complete: {len(all_chunks)} chunks extracted from {len(py_files)} files")
        progress.finish(f"{len(all_chunks)} chunks extracted (avg {avg_per_file:.1f} per file) in {elapsed:.2f}s")
        
        return all_chunks

# Usage example:
# if __name__ == '__main__':
#     proc = Preprocessor(repo_path='app')  # only scan your application code
#     for c in proc.process():
#         print(c)