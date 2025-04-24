import os
import pickle
import logging
from typing import List, Dict, Any, Optional, Union, Set
from datetime import datetime
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer
from app import progress
from tqdm import tqdm

logger = logging.getLogger(__name__)

class EmbeddingManager:
    """
    Verbesserte Version des Embedding-Managers für Code-Chunks mit erweiterter Funktionalität.
    Verwendet SentenceTransformer für Embeddings und FAISS als Vektordatenbank.
    """
    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        index_file: str = "index.faiss",
        meta_file: str = "metadata.pkl",
        hash_file: str = "chunk_hashes.pkl",
        index_type: str = "flat"
    ):
        """
        Initialisiert das Embedding-Modell und lädt oder erstellt einen FAISS-Index + Metadaten.

        Args:
            model_name: SentenceTransformer-Modellname.
            index_file: Pfad zur FAISS-Indexdatei.
            meta_file: Pfad zur Metadaten-Pickle-Datei.
            hash_file: Pfad zur Datei mit Chunk-Hashes für Änderungserkennung.
            index_type: FAISS-Indextyp ('flat' oder 'ivf' für größere Datasets).
        """
        self.model_name = model_name
        self.index_file = index_file
        self.meta_file = meta_file
        self.hash_file = hash_file
        self.index_type = index_type
        self.metadata: List[Dict[str, Any]] = []
        self.chunk_hashes: Dict[str, str] = {}
        self.last_modified = datetime.now()
        
        # Modell laden
        try:
            logger.info(f"Lade SentenceTransformer-Modell '{model_name}'...")
            progress.start(desc=f"Lade Modell {model_name}", total=1, unit="models")
            self.model = SentenceTransformer(model_name)
            progress.finish(f"Modell '{model_name}' erfolgreich geladen")
        except Exception as e:
            logger.error(f"Fehler beim Laden des SentenceTransformer-Modells: {str(e)}")
            progress.finish(f"Fehler beim Laden des Modells")
            raise
        
        # Index laden oder initialisieren
        self._load_or_init_index()
        
    def _load_or_init_index(self) -> None:
        """Lädt den vorhandenen Index und Metadaten oder initialisiert neue Strukturen."""
        if os.path.exists(self.index_file) and os.path.exists(self.meta_file):
            try:
                logger.info(f"Lade bestehenden FAISS-Index aus {self.index_file}...")
                progress.start(desc="Lade FAISS-Index", total=3, unit="steps")
                
                self.index = faiss.read_index(self.index_file)
                progress.update(1)
                
                logger.info(f"Lade Metadaten aus {self.meta_file}...")
                with open(self.meta_file, "rb") as f:
                    self.metadata = pickle.load(f)
                progress.update(1)
                    
                logger.info(f"Lade Chunk-Hashes aus {self.hash_file} (falls vorhanden)...")
                if os.path.exists(self.hash_file):
                    with open(self.hash_file, "rb") as f:
                        self.chunk_hashes = pickle.load(f)
                progress.update(1)
                        
                logger.info(f"Index mit {self.index.ntotal} Einträgen erfolgreich geladen.")
                progress.finish(f"FAISS-Index mit {self.index.ntotal} Vektoren geladen")
            except Exception as e:
                logger.error(f"Fehler beim Laden des Index/der Metadaten: {str(e)}")
                logger.info("Initialisiere neuen Index...")
                progress.finish("Fehler beim Laden, initialisiere neuen Index")
                self._initialize_new_index()
        else:
            logger.info("Keine bestehenden Indexdateien gefunden, initialisiere neuen Index...")
            progress.finish("Keine Indexdateien gefunden")
            self._initialize_new_index()
            
    def _initialize_new_index(self) -> None:
        """Initialisiert einen neuen FAISS-Index basierend auf dem konfigurierten Typ."""
        dim = self.model.get_sentence_embedding_dimension()
        logger.info(f"Initialisiere neuen FAISS-{self.index_type}-Index mit Dimension {dim}...")
        progress.start(desc=f"Initialisiere neuen FAISS-{self.index_type}-Index", total=1, unit="index")
        
        if self.index_type == "flat":
            # Einfacher Flat-Index für kleinere bis mittlere Datasets
            self.index = faiss.IndexFlatL2(dim)
        elif self.index_type == "ivf":
            # IVF-Index für größere Datasets (schneller bei der Suche, langsamer beim Hinzufügen)
            quantizer = faiss.IndexFlatL2(dim)
            nlist = 100  # Anzahl der Voronoi-Zellen
            self.index = faiss.IndexIVFFlat(quantizer, dim, nlist)
            self.index.train(np.random.random((1000, dim)).astype('float32'))
            self.index.nprobe = 10  # Anzahl der zu durchsuchenden Voronoi-Zellen
        else:
            logger.warning(f"Unbekannter Index-Typ '{self.index_type}', verwende 'flat'")
            self.index = faiss.IndexFlatL2(dim)
            
        self.metadata = []
        self.chunk_hashes = {}
        logger.info("Neuer Index erfolgreich initialisiert.")
        progress.finish("Neuer Index erfolgreich initialisiert")
    
    def _compute_hash(self, chunk: Dict[str, Any]) -> str:
        """
        Berechnet einen eindeutigen Hash für einen Code-Chunk.
        
        Args:
            chunk: Dict mit path, name, code, etc.
            
        Returns:
            Eindeutiger Hash-String
        """
        # Kombiniere relevante Felder für den Hash
        hash_input = f"{chunk['path']}::{chunk['name']}::{chunk['code']}"
        import hashlib
        return hashlib.sha256(hash_input.encode('utf-8')).hexdigest()
        
    def _get_chunk_key(self, chunk: Dict[str, Any]) -> str:
        """
        Erstellt einen eindeutigen Schlüssel für einen Chunk.
        
        Args:
            chunk: Dict mit path, name, etc.
            
        Returns:
            Eindeutiger Schlüssel-String
        """
        return f"{chunk['path']}::{chunk['name']}"
        
    def upsert_chunks(
        self,
        chunks: List[Dict[str, Any]],
        batch_size: int = 32
    ) -> Dict[str, Any]:
        """
        Bettet Code-Chunks ein und fügt sie dem FAISS-Vektorindex hinzu.
        Aktualisiert nur geänderte Chunks durch Hash-Vergleich.

        Args:
            chunks: Liste von Dicts mit den Schlüsseln: path, name, start_line, end_line, code.
            batch_size: Größe der Verarbeitungsbatches für bessere Performance.
            
        Returns:
            Dict mit Statusinformationen zum Upsert-Vorgang
        """
        if not chunks:
            return {"status": "success", "added": 0, "updated": 0, "unchanged": 0, "message": "Keine Chunks zum Verarbeiten"}
            
        # Sammle zu verarbeitende Chunks
        progress.start(desc="Analysiere Chunks", total=len(chunks), unit="chunks")
        chunks_to_process = []
        chunk_indexes_to_update = []
        unchanged_chunks = 0
        
        for chunk in chunks:
            progress.update(1)
            chunk_key = self._get_chunk_key(chunk)
            chunk_hash = self._compute_hash(chunk)
            
            # Prüfe, ob der Chunk bereits existiert und sich geändert hat
            if chunk_key in self.chunk_hashes:
                # Suche den Index in den Metadaten
                existing_index = None
                for idx, meta in enumerate(self.metadata):
                    if self._get_chunk_key(meta) == chunk_key:
                        existing_index = idx
                        break
                
                if existing_index is not None:
                    # Prüfe, ob der Inhalt sich geändert hat
                    if self.chunk_hashes[chunk_key] != chunk_hash:
                        # Inhalt hat sich geändert - für Update markieren
                        chunks_to_process.append(chunk)
                        chunk_indexes_to_update.append(existing_index)
                        self.chunk_hashes[chunk_key] = chunk_hash
                    else:
                        # Chunk unverändert
                        unchanged_chunks += 1
                else:
                    # Chunk-Key existiert, aber nicht in Metadaten - hinzufügen
                    chunks_to_process.append(chunk)
                    self.chunk_hashes[chunk_key] = chunk_hash
            else:
                # Neuer Chunk - hinzufügen
                chunks_to_process.append(chunk)
                self.chunk_hashes[chunk_key] = chunk_hash
        
        progress.finish(f"Chunk-Analyse: {len(chunks_to_process)} zu verarbeiten, {unchanged_chunks} unverändert")
        
        if not chunks_to_process:
            logger.info("Keine Chunks haben sich geändert, Index bleibt unverändert.")
            return {"status": "success", "added": 0, "updated": 0, "unchanged": unchanged_chunks, "message": "Keine Änderungen an Chunks"}
        
        # Verarbeite Chunks in Batches für bessere Performance
        total_processed = 0
        total_batches = (len(chunks_to_process) + batch_size - 1) // batch_size
        
        # Fortschrittsbalken für alle Batches
        progress.start(desc="Erzeuge Embeddings", total=len(chunks_to_process), unit="chunks")
        
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min((batch_num + 1) * batch_size, len(chunks_to_process))
            batch_chunks = chunks_to_process[start_idx:end_idx]
            
            logger.info(f"Verarbeite Batch {batch_num+1}/{total_batches} ({len(batch_chunks)} Chunks)")
            
            # Extrahiere Texte für Embedding
            texts = [chunk["code"] for chunk in batch_chunks]
            
            # Erstelle Embeddings
            try:
                embeddings = self.model.encode(texts, show_progress_bar=False)
                embeddings = embeddings.astype(np.float32)  # Stelle sicher, dass der Typ richtig ist
                progress.update(len(batch_chunks))
            except Exception as e:
                logger.error(f"Fehler bei der Erstellung von Embeddings: {str(e)}")
                continue
                
            # Aktualisiere den Index und die Metadaten für den aktuellen Batch
            batch_updates = 0
            batch_additions = 0
            
            for i, (chunk, vector) in enumerate(zip(batch_chunks, embeddings)):
                chunk_idx = start_idx + i
                
                if chunk_idx < len(chunk_indexes_to_update) and chunk_indexes_to_update[chunk_idx] is not None:
                    # Update eines bestehenden Chunks - löschen und neu hinzufügen
                    update_idx = chunk_indexes_to_update[chunk_idx]
                    
                    # HINWEIS: FAISS unterstützt kein direktes Update von Vektoren
                    # Stattdessen müsste man den ganzen Index neu erstellen oder
                    # spezielle Strategien anwenden, wie ein separaten Update-Index
                    # Hier behandeln wir es als neue Addition und behalten die Metadaten-Referenz
                    
                    # Metadaten aktualisieren
                    self.metadata[update_idx] = chunk
                    batch_updates += 1
                else:
                    # Neuer Chunk
                    reshaped_vector = vector.reshape(1, -1)
                    self.index.add(reshaped_vector)
                    self.metadata.append(chunk)
                    batch_additions += 1
            
            total_processed += len(batch_chunks)
            logger.info(f"Batch {batch_num+1} verarbeitet: {batch_additions} hinzugefügt, {batch_updates} aktualisiert")
        
        progress.finish(f"Embeddings erzeugt: {total_processed} Chunks verarbeitet")
        
        # Persistiere Index und Metadaten
        try:
            logger.info("Speichere Index und Metadaten...")
            progress.start(desc="Speichere Index", total=3, unit="schritte")
            
            faiss.write_index(self.index, self.index_file)
            progress.update(1)
            
            with open(self.meta_file, "wb") as f:
                pickle.dump(self.metadata, f)
            progress.update(1)
            
            with open(self.hash_file, "wb") as f:
                pickle.dump(self.chunk_hashes, f)
            progress.update(1)
            
            self.last_modified = datetime.now()
            logger.info(f"Index persistiert: {self.index.ntotal} Vektoren insgesamt")
            progress.finish(f"Index gespeichert: {self.index.ntotal} Vektoren insgesamt")
        except Exception as e:
            logger.error(f"Fehler beim Persistieren des Index: {str(e)}")
            progress.finish("Fehler beim Speichern des Index")
        
        return {
            "status": "success",
            "added": total_processed - len(chunk_indexes_to_update),
            "updated": len(chunk_indexes_to_update),
            "unchanged": unchanged_chunks,
            "total_vectors": self.index.ntotal,
            "timestamp": self.last_modified.isoformat()
        }
    
    def query(
        self,
        query_text: str,
        top_k: int = 5,
        threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Fragt den FAISS-Index mit einem Natural-Language-String ab und gibt die top-k Chunks zurück.

        Args:
            query_text: String mit der Benutzeranfrage.
            top_k: Anzahl der zurückzugebenden Ergebnisse.
            threshold: Optionaler Schwellenwert für die maximale Distanz (kleinere Werte sind besser).
            
        Returns:
            Liste von Match-Dicts mit 'score' und 'metadata'.
        """
        if self.index.ntotal == 0:
            logger.warning("FAISS-Index ist leer, keine Ergebnisse verfügbar")
            return []
            
        try:
            progress.start(desc="Suche im Vektorindex", total=2, unit="steps")
            
            # Encode query
            q_vec = self.model.encode([query_text])
            q_vec = q_vec.astype(np.float32)
            progress.update(1)
            
            # Search in the index
            distances, indices = self.index.search(q_vec, min(top_k, self.index.ntotal))
            
            results = []
            for score, idx in zip(distances[0], indices[0]):
                # Bei FAISS ist eine geringere Distanz besser (L2-Distanz)
                # Konvertiere zu Ähnlichkeitsscore (höher = besser) für konsistente API
                similarity = 1.0 / (1.0 + score)
                
                # Skip results below threshold if provided
                if threshold is not None and similarity < threshold:
                    continue
                    
                if idx >= 0 and idx < len(self.metadata):  # Sicherstellen, dass der Index gültig ist
                    meta = self.metadata[idx]
                    results.append({
                        "score": float(similarity),
                        "raw_distance": float(score),
                        "metadata": meta
                    })
            
            progress.update(1)
            progress.finish(f"{len(results)} relevante Code-Chunks gefunden")
                    
            return results
        except Exception as e:
            logger.error(f"Fehler bei der FAISS-Suche: {str(e)}")
            progress.finish("Fehler bei der Suche im Vektorindex")
            return []
    
    def get_index_stats(self) -> Dict[str, Any]:
        """
        Liefert Statistiken und Metadaten über den aktuellen Index.
        
        Returns:
            Dict mit Indexstatistiken
        """
        try:
            progress.start(desc="Sammle Index-Statistiken", total=1, unit="stats")
            
            # Sammle grundlegende Statistiken
            file_counts = {}
            extension_counts = {}
            
            for meta in self.metadata:
                path = meta.get("path", "")
                # Zähle Dateien
                file_counts[path] = file_counts.get(path, 0) + 1
                
                # Zähle Dateierweiterungen
                ext = os.path.splitext(path)[1]
                if ext:
                    extension_counts[ext] = extension_counts.get(ext, 0) + 1
            
            stats = {
                "status": "success",
                "total_vectors": self.index.ntotal,
                "total_chunks": len(self.metadata),
                "unique_files": len(file_counts),
                "file_extensions": extension_counts,
                "model_name": self.model_name,
                "index_type": self.index_type,
                "dimension": self.model.get_sentence_embedding_dimension(),
                "last_modified": self.last_modified.isoformat() if self.last_modified else None,
            }
            
            progress.finish(f"Statistiken gesammelt: {self.index.ntotal} Vektoren, {len(file_counts)} Dateien")
            return stats
        except Exception as e:
            logger.error(f"Fehler beim Generieren der Indexstatistiken: {str(e)}")
            progress.finish("Fehler beim Sammeln der Statistiken")
            return {"status": "error", "message": str(e)}
            
    def remove_chunks(self, file_paths: List[str]) -> Dict[str, Any]:
        """
        Entfernt Chunks für bestimmte Dateipfade aus dem Index.
        
        Args:
            file_paths: Liste von Dateipfaden, deren Chunks entfernt werden sollen
            
        Returns:
            Dict mit Informationen zum Löschvorgang
        """
        if not file_paths:
            return {"status": "success", "removed": 0, "message": "Keine Dateien zum Entfernen angegeben"}
            
        logger.info(f"Entferne Chunks für {len(file_paths)} Dateien aus dem Index")
        progress.start(desc=f"Entferne Chunks für {len(file_paths)} Dateien", total=2, unit="steps")
        
        file_path_set = set(file_paths)
        chunks_to_keep = []
        metadata_to_keep = []
        removed_chunks = 0
        removed_hashes = []
        
        # Identifiziere zu entfernende Chunks
        for i, meta in enumerate(self.metadata):
            path = meta.get("path", "")
            chunk_key = self._get_chunk_key(meta)
            
            if path in file_path_set:
                # Dieser Chunk soll entfernt werden
                removed_chunks += 1
                if chunk_key in self.chunk_hashes:
                    del self.chunk_hashes[chunk_key]
                removed_hashes.append(chunk_key)
            else:
                # Behalte diesen Chunk
                chunks_to_keep.append(i)
                metadata_to_keep.append(meta)
        
        progress.update(1)
        
        if removed_chunks == 0:
            progress.finish("Keine passenden Chunks gefunden")
            return {"status": "success", "removed": 0, "message": "Keine passenden Chunks gefunden"}
            
        # Da FAISS kein direktes Entfernen von Vektoren unterstützt,
        # erstellen wir einen neuen Index mit den verbleibenden Chunks
        logger.info(f"Erstelle neuen Index mit {len(chunks_to_keep)} von {len(self.metadata)} Chunks")
        
        # Initialisiere neuen Index
        dim = self.model.get_sentence_embedding_dimension()
        if self.index_type == "flat":
            new_index = faiss.IndexFlatL2(dim)
        elif self.index_type == "ivf":
            quantizer = faiss.IndexFlatL2(dim)
            nlist = 100
            new_index = faiss.IndexIVFFlat(quantizer, dim, nlist)
            # Training ist erforderlich für IVF
            if len(chunks_to_keep) >= 1000:
                # Extrahiere bestehende Vektoren für Training
                vectors = []
                for idx in chunks_to_keep[:1000]:  # Verwende maximal 1000 für Training
                    vector = faiss.vector_to_array(self.index.reconstruct(idx))
                    vectors.append(vector)
                train_vectors = np.vstack(vectors).astype('float32')
                new_index.train(train_vectors)
            else:
                # Nicht genug Daten, verwende zufällige Vektoren
                new_index.train(np.random.random((1000, dim)).astype('float32'))
            new_index.nprobe = 10
        
        # Kopiere die verbleibenden Vektoren in den neuen Index
        sub_progress = tqdm(chunks_to_keep, desc="Kopiere Vektoren", unit="vektoren")
        for idx in sub_progress:
            vector = faiss.vector_to_array(self.index.reconstruct(idx)).reshape(1, -1)
            new_index.add(vector)
        
        # Ersetze den alten Index und die Metadaten
        self.index = new_index
        self.metadata = metadata_to_keep
        
        progress.update(1)
        
        # Speichere den aktualisierten Index und die Metadaten
        try:
            logger.info("Speichere aktualisierten Index...")
            sub_progress = tqdm(total=3, desc="Speichere Index", unit="steps")
            
            faiss.write_index(self.index, self.index_file)
            sub_progress.update(1)
            
            with open(self.meta_file, "wb") as f:
                pickle.dump(self.metadata, f)
            sub_progress.update(1)
            
            with open(self.hash_file, "wb") as f:
                pickle.dump(self.chunk_hashes, f)
            sub_progress.update(1)
            sub_progress.close()
            
            self.last_modified = datetime.now()
            
            progress.finish(f"{removed_chunks} Chunks entfernt, {len(self.metadata)} verbleiben")
            return {
                "status": "success",
                "removed": removed_chunks,
                "remaining": len(self.metadata),
                "removed_files": list(file_paths),
                "timestamp": self.last_modified.isoformat()
            }
        except Exception as e:
            logger.error(f"Fehler beim Speichern des aktualisierten Index: {str(e)}")
            progress.finish("Fehler beim Speichern des aktualisierten Index")
            return {"status": "error", "message": str(e)}