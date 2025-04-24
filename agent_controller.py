"""
agent_controller.py - Controller für Benutzeroberfläche mit Logging-Anzeige und Fortschrittsbalken

Integriert die Logging- und Fortschrittsfunktionen der bestehenden Anwendung und 
zeigt sie in einem Echtzeit-Interface an. Ermöglicht Starten des Agenten, ngrok-Tunnel
und andere Verwaltungsfunktionen.
"""

import os
import sys
import time
import queue
import signal
import threading
import logging
import tkinter as tk
import subprocess
from tkinter import ttk, scrolledtext, filedialog, messagebox
from typing import Optional, Callable, Dict, Any, List
import io
import json
import socket
import requests
from urllib.parse import urlparse

# Eigene Imports
from app import progress
from app.rag_manager import RAGManager

# Eigene Logging-Handler-Klasse, die Logs in die UI-Queue schreibt
class QueueHandler(logging.Handler):
    """
    Logging-Handler, der Logs in eine Queue schreibt für Thread-sichere Anzeige in der UI
    """
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue
        
    def emit(self, record):
        self.log_queue.put(record)


class ProcessManager:
    """Stellt Methoden zum Starten und Stoppen von Prozessen bereit."""
    
    def __init__(self):
        self.processes = {}
        self.process_counter = 0
    
    def start_process(self, program, args=None, cwd=None):
        """
        Startet einen Prozess und gibt eine ID zurück.
        
        Args:
            program: Der Pfad zum ausführbaren Programm
            args: Eine Liste von Argumenten (optional)
            cwd: Arbeitsverzeichnis (optional)
            
        Returns:
            Eine eindeutige Prozess-ID
        """
        try:
            # Umgebung für den Kindprozess vorbereiten
            env = os.environ.copy()
            
            # Starte den Prozess
            if args:
                if sys.platform == 'win32':
                    # Windows erfordert shell=True für Befehle mit Argumenten
                    process = subprocess.Popen(
                        [program] + args, 
                        env=env,
                        cwd=cwd,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                else:
                    # Linux/Mac
                    process = subprocess.Popen(
                        [program] + args, 
                        env=env,
                        cwd=cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
            else:
                # Ohne Argumente
                if sys.platform == 'win32':
                    process = subprocess.Popen(
                        program,
                        env=env,
                        cwd=cwd,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                else:
                    process = subprocess.Popen(
                        program,
                        env=env,
                        cwd=cwd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
            
            # Prozess speichern
            self.process_counter += 1
            process_id = f"proc_{self.process_counter}"
            self.processes[process_id] = process
            
            logging.info(f"Prozess {process_id} gestartet: {program} {' '.join(args) if args else ''}")
            
            return {
                "id": process_id,
                "pid": process.pid,
                "program": program,
                "args": args
            }
        except Exception as e:
            logging.error(f"Fehler beim Starten des Prozesses: {str(e)}")
            return None
    
    def stop_process(self, process_id):
        """
        Stoppt einen Prozess anhand seiner ID.
        
        Args:
            process_id: Die Prozess-ID
            
        Returns:
            True wenn erfolgreich, sonst False
        """
        if process_id in self.processes:
            try:
                process = self.processes[process_id]
                if sys.platform == 'win32':
                    # Windows: taskkill /F /T /PID
                    subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], check=False)
                else:
                    # Linux/Mac: SIGTERM
                    os.kill(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
                
                # Aus der Liste entfernen
                del self.processes[process_id]
                logging.info(f"Prozess {process_id} gestoppt")
                return True
            except Exception as e:
                logging.error(f"Fehler beim Stoppen des Prozesses {process_id}: {str(e)}")
                return False
        else:
            logging.warning(f"Prozess-ID {process_id} nicht gefunden")
            return False
    
    def kill_process_by_name(self, name):
        """
        Beendet alle Prozesse mit dem angegebenen Namen.
        
        Args:
            name: Name des Prozesses (z.B. 'python', 'ngrok')
            
        Returns:
            Anzahl der beendeten Prozesse
        """
        count = 0
        try:
            if sys.platform == 'win32':
                # Windows: taskkill
                output = subprocess.check_output(['taskkill', '/F', '/IM', f"{name}.exe"], stderr=subprocess.STDOUT)
                logging.info(f"Prozesse beendet: {output.decode('utf-8', errors='replace')}")
                count = 1  # Genaue Anzahl nicht bekannt
            else:
                # Linux/Mac: killall
                output = subprocess.check_output(['killall', '-9', name], stderr=subprocess.STDOUT)
                logging.info(f"Prozesse beendet: {output.decode('utf-8', errors='replace')}")
                count = 1  # Genaue Anzahl nicht bekannt
        except subprocess.CalledProcessError as e:
            # Befehl fehlgeschlagen, wahrscheinlich kein Prozess gefunden
            logging.info(f"Keine Prozesse mit Namen '{name}' gefunden: {e.output.decode('utf-8', errors='replace')}")
        except Exception as e:
            logging.error(f"Fehler beim Beenden von Prozessen mit Namen '{name}': {str(e)}")
        
        return count

    def cleanup_all_processes(self):
        """Beendet alle gestarteten Prozesse."""
        for process_id in list(self.processes.keys()):
            self.stop_process(process_id)
        
        # Sicherstellen, dass alle Prozesse beendet sind
        try:
            if sys.platform == 'win32':
                subprocess.run(['taskkill', '/F', '/IM', 'ngrok.exe'], check=False)
                subprocess.run(['taskkill', '/F', '/IM', 'uvicorn.exe'], check=False)
            else:
                subprocess.run(['killall', '-9', 'ngrok'], check=False)
                subprocess.run(['killall', '-9', 'uvicorn'], check=False)
        except Exception:
            pass


class AgentController:
    """
    Controller für die Benutzeroberfläche, der Logging und Fortschrittsanzeigen integriert
    sowie die Steuerung des Agenten und ngrok-Tunnels ermöglicht.
    """
    def __init__(self, root: tk.Tk, rag_manager: Optional[RAGManager] = None):
        """
        Initialisiert den Controller mit einem tkinter-Root-Fenster
        
        Args:
            root: tkinter-Root-Fenster
            rag_manager: Optional vorhandene RAGManager-Instanz
        """
        self.root = root
        self.rag_manager = rag_manager
        self.log_queue = queue.Queue()
        self.progress_queue = queue.Queue()
        self.stop_event = threading.Event()
        
        # Prozess-Manager für externe Prozesse
        self.process_manager = ProcessManager()
        
        # Server-Status
        self.agent_process = None
        self.ngrok_process = None
        self.ngrok_url = None
        
        # Setup UI
        self._setup_ui()
        
        # Konfiguriere den Root-Logger, um unseren Queue-Handler zu verwenden
        self._setup_logging()
        
        # Verbinde mit dem globalen Progress-Tracker
        self._override_progress_tracker()
        
        # Starte Queue-Verarbeitung
        self.root.after(100, self._process_log_queue)
        self.root.after(100, self._process_progress_queue)
        
        # Überprüfe Server-Status
        self.root.after(1000, self._check_server_status)
        
    def _setup_ui(self):
        """Erstellt die Benutzeroberfläche mit Logging-Fenster und Fortschrittsbalken"""
        self.root.title("CodeContext AI Agent")
        self.root.geometry("950x750")
        self.root.minsize(800, 600)
        
        # Haupt-Frame mit Padding
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Oberer Bereich mit Steuerungselementen
        control_frame = ttk.LabelFrame(main_frame, text="Steuerung", padding="5")
        control_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Verzeichnis-Auswahl
        dir_frame = ttk.Frame(control_frame)
        dir_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(dir_frame, text="Projektverzeichnis:").pack(side=tk.LEFT, padx=(0, 5))
        self.dir_var = tk.StringVar(value=os.getenv("LOCAL_PROJECT_PATH", os.getcwd()))
        dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var, width=50)
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        
        browse_btn = ttk.Button(dir_frame, text="Durchsuchen...", command=self._browse_directory)
        browse_btn.pack(side=tk.LEFT)
        
        # Server-Status und Steuerung
        server_frame = ttk.Frame(control_frame)
        server_frame.pack(fill=tk.X, pady=5)
        
        # Status-Anzeige
        status_frame = ttk.Frame(server_frame)
        status_frame.pack(side=tk.LEFT, fill=tk.Y)
        
        ttk.Label(status_frame, text="Agent-Status:").pack(side=tk.LEFT, padx=(0, 5))
        self.status_var = tk.StringVar(value="Offline")
        status_label = ttk.Label(status_frame, textvariable=self.status_var, 
                                  foreground="red", font=("TkDefaultFont", 10, "bold"))
        status_label.pack(side=tk.LEFT, padx=(0, 10))
        
        # Server-Steuerung
        self.start_server_btn = ttk.Button(server_frame, text="Agent starten", 
                                           command=self._start_server)
        self.start_server_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.stop_server_btn = ttk.Button(server_frame, text="Agent stoppen", 
                                          command=self._stop_server, state=tk.DISABLED)
        self.stop_server_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        # Ngrok Tunnel
        tunnel_frame = ttk.Frame(control_frame)
        tunnel_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(tunnel_frame, text="Ngrok-Tunnel:").pack(side=tk.LEFT, padx=(0, 5))
        self.tunnel_var = tk.StringVar(value="Nicht aktiv")
        tunnel_label = ttk.Label(tunnel_frame, textvariable=self.tunnel_var)
        tunnel_label.pack(side=tk.LEFT, padx=(0, 10), fill=tk.X, expand=True)
        
        self.start_tunnel_btn = ttk.Button(tunnel_frame, text="Tunnel starten", 
                                          command=self._start_tunnel, state=tk.DISABLED)
        self.start_tunnel_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.stop_tunnel_btn = ttk.Button(tunnel_frame, text="Tunnel stoppen", 
                                         command=self._stop_tunnel, state=tk.DISABLED)
        self.stop_tunnel_btn.pack(side=tk.LEFT)
        
        # Aktions-Buttons
        actions_frame = ttk.LabelFrame(main_frame, text="Aktionen", padding="5")
        actions_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Buttons Frame
        buttons_frame = ttk.Frame(actions_frame)
        buttons_frame.pack(fill=tk.X, pady=5)
        
        self.scan_btn = ttk.Button(buttons_frame, text="Verzeichnis scannen", command=self._scan_directory)
        self.scan_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.build_btn = ttk.Button(buttons_frame, text="Index aufbauen", command=self._build_index)
        self.build_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        self.sync_btn = ttk.Button(buttons_frame, text="Synchronisieren", command=self._sync_directory)
        self.sync_btn.pack(side=tk.LEFT, padx=(0, 5))
        
        # Auto-Sync Optionen
        self.auto_sync_var = tk.BooleanVar(value=False)
        auto_sync_check = ttk.Checkbutton(
            buttons_frame, 
            text="Auto-Sync", 
            variable=self.auto_sync_var,
            command=self._toggle_auto_sync
        )
        auto_sync_check.pack(side=tk.LEFT, padx=(20, 5))
        
        ttk.Label(buttons_frame, text="Intervall (s):").pack(side=tk.LEFT)
        
        self.interval_var = tk.StringVar(value="90")
        interval_entry = ttk.Entry(buttons_frame, textvariable=self.interval_var, width=5)
        interval_entry.pack(side=tk.LEFT, padx=(5, 5))
        
        # Fortschrittsanzeige - Universeller Fortschrittsbalken
        progress_frame = ttk.LabelFrame(main_frame, text="Fortschritt", padding="5")
        progress_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Fortschrittsbeschreibung
        self.progress_desc_var = tk.StringVar(value="Bereit")
        progress_desc = ttk.Label(progress_frame, textvariable=self.progress_desc_var)
        progress_desc.pack(fill=tk.X, anchor=tk.W)
        
        # Fortschrittsbalken
        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate", length=100)
        self.progress_bar.pack(fill=tk.X, pady=(5, 0))
        
        # Fortschrittsdetails
        self.progress_details_var = tk.StringVar(value="")
        progress_details = ttk.Label(progress_frame, textvariable=self.progress_details_var)
        progress_details.pack(fill=tk.X, anchor=tk.W, pady=(5, 0))
        
        # Logging-Bereich
        log_frame = ttk.LabelFrame(main_frame, text="Logging", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        # Text-Widget für Logs mit Scrollbar
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, width=80, height=20)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.config(state=tk.DISABLED)  # Schreibgeschützt
        
        # Farb-Tags für verschiedene Log-Level definieren
        self.log_text.tag_config('DEBUG', foreground='gray')
        self.log_text.tag_config('INFO', foreground='green')
        self.log_text.tag_config('WARNING', foreground='orange')
        self.log_text.tag_config('ERROR', foreground='red')
        self.log_text.tag_config('CRITICAL', foreground='red', background='yellow')
        
        # Status-Leiste
        self.action_status_var = tk.StringVar(value="Bereit")
        status_bar = ttk.Label(self.root, textvariable=self.action_status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)
        
    def _browse_directory(self):
        """Öffnet einen Dialog zur Verzeichnisauswahl"""
        directory = filedialog.askdirectory(initialdir=self.dir_var.get())
        if directory:
            self.dir_var.set(directory)
    
    def _start_server(self):
        """Startet den FastAPI-Server mit uvicorn"""
        try:
            project_directory = self.dir_var.get()
            if not os.path.exists(project_directory):
                messagebox.showerror("Fehler", f"Verzeichnis {project_directory} existiert nicht.")
                return
            
            # Agent-Verzeichnis (wo die main.py ist)
            agent_directory = os.path.dirname(os.path.abspath(__file__))
            if os.path.basename(agent_directory) == "app":
                # Falls agent_controller.py in einem app/ Unterverzeichnis ist
                agent_directory = os.path.dirname(agent_directory)
                
            # Prüfen, ob die main.py existiert
            main_path = os.path.join(agent_directory, "main.py")
            if not os.path.exists(main_path):
                messagebox.showerror("Fehler", f"main.py nicht gefunden in {agent_directory}")
                return
            
            # .env-Datei aktualisieren oder erstellen im Agent-Verzeichnis
            self._update_env_file(agent_directory, project_directory)
            
            # Server starten im Agent-Verzeichnis
            logging.info(f"Starte FastAPI-Server für Projekt: {project_directory}")
            self.action_status_var.set("Starte Server...")
            
            # Ausführen im Agent-Verzeichnis
            self.agent_process = self.process_manager.start_process(
                "uvicorn", 
                ["main:app", "--host", "0.0.0.0", "--port", "8000"],
                cwd=agent_directory
            )
            
            # UI aktualisieren
            self._update_server_status(checking=True)
            
        except Exception as e:
            logging.error(f"Fehler beim Starten des Servers: {str(e)}")
            self.action_status_var.set(f"Fehler: {str(e)}")
            
    def _update_env_file(self, agent_directory, project_directory):
        """
        Aktualisiert oder erstellt die .env-Datei mit dem ausgewählten Projektpfad
        
        Args:
            agent_directory: Pfad zum Agent-Verzeichnis
            project_directory: Pfad zum zu analysierenden Projektverzeichnis
        """
        env_path = os.path.join(agent_directory, ".env")
        env_vars = {}
        
        # Bestehende Umgebungsvariablen einlesen, falls Datei existiert
        if os.path.exists(env_path):
            try:
                with open(env_path, 'r', encoding='utf-8') as file:
                    for line in file:
                        line = line.strip()
                        if line and not line.startswith('#') and '=' in line:
                            key, value = line.split('=', 1)
                            env_vars[key.strip()] = value.strip()
                logging.info(f"Bestehende .env-Datei eingelesen: {list(env_vars.keys())}")
            except Exception as e:
                logging.warning(f"Fehler beim Lesen der .env-Datei: {str(e)}")
        
        # LOCAL_PROJECT_PATH aktualisieren
        env_vars['LOCAL_PROJECT_PATH'] = project_directory
        
        # Datei schreiben
        try:
            with open(env_path, 'w', encoding='utf-8') as file:
                for key, value in env_vars.items():
                    file.write(f"{key}={value}\n")
            logging.info(f"LOCAL_PROJECT_PATH in .env-Datei aktualisiert: {project_directory}")
        except Exception as e:
            logging.error(f"Fehler beim Schreiben der .env-Datei: {str(e)}")
    
    def _stop_server(self):
        """Stoppt den FastAPI-Server"""
        try:
            logging.info("Stoppe FastAPI-Server")
            self.action_status_var.set("Stoppe Server...")
            
            # Zuerst Tunnel stoppen, falls aktiv
            if self.ngrok_process:
                self._stop_tunnel()
            
            # Server stoppen
            if self.agent_process:
                self.process_manager.stop_process(self.agent_process["id"])
                self.agent_process = None
            else:
                # Fallback: Versuche, uvicorn direkt zu beenden
                self.process_manager.kill_process_by_name("uvicorn")
                
            # UI aktualisieren
            self._update_server_status(is_running=False)
            self.action_status_var.set("Server gestoppt")
            
        except Exception as e:
            logging.error(f"Fehler beim Stoppen des Servers: {str(e)}")
            self.action_status_var.set(f"Fehler: {str(e)}")
    
    def _start_tunnel(self):
        """Startet einen ngrok-Tunnel zum FastAPI-Server"""
        try:
            logging.info("Starte ngrok-Tunnel")
            self.action_status_var.set("Starte Tunnel...")
            
            # Tunnel starten
            self.ngrok_process = self.process_manager.start_process("ngrok", ["http", "8000"])
            
            # Warte kurz und hole dann die URL
            self.root.after(2000, self._get_tunnel_url)
            
        except Exception as e:
            logging.error(f"Fehler beim Starten des Tunnels: {str(e)}")
            self.action_status_var.set(f"Fehler: {str(e)}")
    
    def _get_tunnel_url(self):
        """Holt die öffentliche URL vom ngrok-Tunnel"""
        try:
            # ngrok API abfragen
            response = requests.get("http://localhost:4040/api/tunnels")
            if response.status_code == 200:
                data = response.json()
                if data.get("tunnels"):
                    # Suche nach einer HTTPS-URL
                    https_url = None
                    for tunnel in data["tunnels"]:
                        url = tunnel.get("public_url", "")
                        if url.startswith("https://"):
                            https_url = url
                            break
                    
                    if https_url:
                        self.ngrok_url = https_url
                        logging.info(f"Ngrok-Tunnel erstellt: {https_url}")
                        self.tunnel_var.set(https_url)
                        self.action_status_var.set(f"Tunnel aktiv: {https_url}")
                        self.stop_tunnel_btn.config(state=tk.NORMAL)
                        return
            
            # Wenn wir hier ankommen, wurde kein Tunnel gefunden
            logging.warning("Konnte keine ngrok-Tunnel-URL ermitteln")
            self.action_status_var.set("Tunnel-URL konnte nicht ermittelt werden")
            
        except Exception as e:
            logging.error(f"Fehler beim Abrufen der Tunnel-URL: {str(e)}")
            self.action_status_var.set(f"Fehler bei Tunnel-URL: {str(e)}")
    
    def _stop_tunnel(self):
        """Stoppt den ngrok-Tunnel"""
        try:
            logging.info("Stoppe ngrok-Tunnel")
            self.action_status_var.set("Stoppe Tunnel...")
            
            # Tunnel stoppen
            if self.ngrok_process:
                self.process_manager.stop_process(self.ngrok_process["id"])
                self.ngrok_process = None
            else:
                # Fallback: Versuche, ngrok direkt zu beenden
                self.process_manager.kill_process_by_name("ngrok")
            
            # UI aktualisieren
            self.ngrok_url = None
            self.tunnel_var.set("Nicht aktiv")
            self.start_tunnel_btn.config(state=tk.NORMAL)
            self.stop_tunnel_btn.config(state=tk.DISABLED)
            self.action_status_var.set("Tunnel gestoppt")
            
        except Exception as e:
            logging.error(f"Fehler beim Stoppen des Tunnels: {str(e)}")
            self.action_status_var.set(f"Fehler: {str(e)}")
    
    def _check_server_status(self):
        """Überprüft den Status des FastAPI-Servers"""
        self._update_server_status()
        
        # Regelmäßige Überprüfung
        if not self.stop_event.is_set():
            self.root.after(5000, self._check_server_status)
    
    def _update_server_status(self, is_running=None, checking=False):
        """
        Aktualisiert die Server-Status-Anzeige
        
        Args:
            is_running: Optional vorgegebener Status (sonst wird geprüft)
            checking: Ob gerade eine Statusüberprüfung läuft
        """
        if checking:
            self.status_var.set("Prüfe...")
            self.root.update_idletasks()
            return
            
        # Status prüfen, wenn nicht vorgegeben
        if is_running is None:
            is_running = self._is_server_running()
        
        # UI aktualisieren
        if is_running:
            self.status_var.set("Online")
            # Direkter Zugriff auf das Label, ohne trace_vinfo
            for widget in self.root.winfo_children():
                if isinstance(widget, ttk.Label) and widget.cget("textvariable") == str(self.status_var):
                    widget.config(foreground="green")
                    break
            self.start_server_btn.config(state=tk.DISABLED)
            self.stop_server_btn.config(state=tk.NORMAL)
            self.start_tunnel_btn.config(state=tk.NORMAL)
        else:
            self.status_var.set("Offline")
            # Direkter Zugriff auf das Label, ohne trace_vinfo
            for widget in self.root.winfo_children():
                if isinstance(widget, ttk.Label) and widget.cget("textvariable") == str(self.status_var):
                    widget.config(foreground="red")
                    break
            self.start_server_btn.config(state=tk.NORMAL)
            self.stop_server_btn.config(state=tk.DISABLED)
            self.start_tunnel_btn.config(state=tk.DISABLED)
            self.stop_tunnel_btn.config(state=tk.DISABLED)
            
            # Tunnel-Status zurücksetzen
            if self.ngrok_url:
                self.ngrok_url = None
                self.tunnel_var.set("Nicht aktiv")
    
    def _is_server_running(self):
        """
        Überprüft, ob der FastAPI-Server läuft
        
        Returns:
            True wenn Server erreichbar, sonst False
        """
        try:
            response = requests.get("http://localhost:8000/", timeout=1)
            return response.status_code == 200
        except:
            return False
    
    def _scan_directory(self):
        """Scannt das ausgewählte Verzeichnis"""
        directory = self.dir_var.get()
        if not os.path.exists(directory):
            messagebox.showerror("Fehler", f"Verzeichnis {directory} existiert nicht.")
            return
            
        self.action_status_var.set(f"Scanne Verzeichnis {directory}...")
        self._run_in_thread(self._scan_directory_task)
        
    def _scan_directory_task(self):
        """Task zum Scannen des Verzeichnisses im Hintergrund"""
        try:
            project_directory = self.dir_var.get()
            logging.info(f"Starte Verzeichnisscan für {project_directory}")
            
            # Projekt-Hash für Datendirektion berechnen
            project_hash = self._get_project_hash(project_directory)
            # Agent-Verzeichnis ermitteln
            agent_directory = os.path.dirname(os.path.abspath(__file__))
            if os.path.basename(agent_directory) == "app":
                agent_directory = os.path.dirname(agent_directory)
                
            # Projektspezifisches Datenverzeichnis
            data_dir = os.path.join(agent_directory, "data", project_hash)
            os.makedirs(data_dir, exist_ok=True)
            
            # RAG-Manager initialisieren, falls noch nicht geschehen
            if self.rag_manager is None or self.rag_manager.local_path != project_directory:
                self.rag_manager = RAGManager(
                    local_path=project_directory,
                    auto_sync=self.auto_sync_var.get(),
                    sync_interval=int(self.interval_var.get()),
                    index_file=os.path.join(data_dir, "index.faiss"),
                    meta_file=os.path.join(data_dir, "metadata.pkl"),
                    hash_file=os.path.join(data_dir, "file_hashes.json"),
                    chunk_hashes_file=os.path.join(data_dir, "chunk_hashes.pkl"),
                    dependency_graph_file=os.path.join(data_dir, "dependency_graph.json")
                )
            
            # Verzeichnisstruktur abrufen
            self.rag_manager.get_file_structure(force_refresh=True)
            
            # UI aktualisieren
            self.root.after(0, lambda: self.action_status_var.set(f"Verzeichnisscan abgeschlossen"))
        except Exception as e:
            logging.error(f"Fehler beim Scannen des Verzeichnisses: {str(e)}")
            self.root.after(0, lambda: self.action_status_var.set(f"Fehler: {str(e)}"))
            
    def _build_index(self):
        """Baut den Index auf"""
        directory = self.dir_var.get()
        if not os.path.exists(directory):
            messagebox.showerror("Fehler", f"Verzeichnis {directory} existiert nicht.")
            return
            
        # Sicherheitsabfrage für Neuaufbau
        if messagebox.askyesno("Index neu aufbauen", 
                              "Möchten Sie wirklich den Index komplett neu aufbauen?\n"
                              "Dies kann bei großen Projekten einige Zeit dauern."):
            self.action_status_var.set("Baue Index neu auf...")
            self._run_in_thread(self._build_index_task)
        
    def _build_index_task(self):
        """Task zum Aufbau des Index im Hintergrund"""
        try:
            directory = self.dir_var.get()
            logging.info(f"Starte kompletten Index-Neuaufbau für {directory}")
            
            # RAG-Manager initialisieren, falls noch nicht geschehen
            if self.rag_manager is None or self.rag_manager.local_path != directory:
                self.rag_manager = RAGManager(
                    local_path=directory,
                    auto_sync=self.auto_sync_var.get(),
                    sync_interval=int(self.interval_var.get())
                )
            
            # Index aufbauen
            result = self.rag_manager.build_index()
            
            # UI aktualisieren
            if result.get("status") == "success":
                message = f"Index aufgebaut: {result.get('chunks_processed', 0)} Chunks in {result.get('time_taken', 0)}s"
            else:
                message = f"Fehler: {result.get('message', 'Unbekannter Fehler')}"
                
            self.root.after(0, lambda: self.action_status_var.set(message))
        except Exception as e:
            logging.error(f"Fehler beim Aufbau des Index: {str(e)}")
            self.root.after(0, lambda: self.action_status_var.set(f"Fehler: {str(e)}"))
            
    def _sync_directory(self):
        """Synchronisiert das Verzeichnis mit dem Index"""
        if self.rag_manager is None:
            messagebox.showinfo("Info", "Bitte zuerst den Index aufbauen.")
            return
            
        self.action_status_var.set("Synchronisiere Verzeichnis...")
        self._run_in_thread(self._sync_directory_task)
        
    def _sync_directory_task(self):
        """Task zur Synchronisierung im Hintergrund"""
        try:
            logging.info("Starte Verzeichnissynchronisierung")
            
            # Synchronisieren
            result = self.rag_manager.sync_directory()
            
            # UI aktualisieren
            if result.get("status") == "success":
                changed_files = len(result.get("changed_files", []))
                time_taken = result.get("execution_time_seconds", 0)
                message = f"Synchronisierung abgeschlossen: {changed_files} Dateien aktualisiert in {time_taken:.2f}s"
            else:
                message = f"Fehler: {result.get('message', 'Unbekannter Fehler')}"
                
            self.root.after(0, lambda: self.action_status_var.set(message))
        except Exception as e:
            logging.error(f"Fehler bei der Synchronisierung: {str(e)}")
            self.root.after(0, lambda: self.action_status_var.set(f"Fehler: {str(e)}"))
            
    def _toggle_auto_sync(self):
        """Schaltet Auto-Sync ein oder aus"""
        if self.rag_manager is None:
            if self.auto_sync_var.get():
                messagebox.showinfo("Info", "Bitte zuerst den Index aufbauen.")
                self.auto_sync_var.set(False)
            return
            
        try:
            auto_sync = self.auto_sync_var.get()
            interval = int(self.interval_var.get())
            
            if auto_sync:
                self.rag_manager.auto_sync = True
                self.rag_manager.sync_interval = interval
                self.rag_manager.start_auto_sync()
                logging.info(f"Auto-Sync aktiviert (Intervall: {interval}s)")
            else:
                self.rag_manager.stop_auto_sync()
                self.rag_manager.auto_sync = False
                logging.info("Auto-Sync deaktiviert")
                
            self.action_status_var.set(f"Auto-Sync {'aktiviert' if auto_sync else 'deaktiviert'}")
        except Exception as e:
            logging.error(f"Fehler beim Ändern des Auto-Sync-Status: {str(e)}")
            self.action_status_var.set(f"Fehler: {str(e)}")
    
    def _setup_logging(self):
        """Konfiguriert das Logging für die Anzeige in der UI"""
        # Handler für unsere Queue erstellen
        handler = QueueHandler(self.log_queue)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        
        # Zum Root-Logger hinzufügen
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        
        # Sicherstellen, dass wir alle Nachrichten bekommen
        if not root_logger.handlers:
            root_logger.setLevel(logging.INFO)
            
        # Initiale Nachricht
        logging.info("Logging-System initialisiert")
        
    def _override_progress_tracker(self):
        """Überschreibt die update-Methoden des globalen ProgressTrackers"""
        original_start = progress.start
        original_update = progress.update
        original_finish = progress.finish
        
        # Wrapper-Funktionen, die den originalen Aufruf durchführen und unsere Queue informieren
        def start_wrapper(*args, **kwargs):
            result = original_start(*args, **kwargs)
            # Extrahiere Informationen und sende an die Queue
            total = progress.total
            desc = progress.desc
            unit = progress.unit
            self.progress_queue.put(("start", desc, total, unit))
            return result
            
        def update_wrapper(*args, **kwargs):
            result = original_update(*args, **kwargs)
            # Aktuellen Fortschritt berechnen und senden
            if progress.pbar:
                value = progress.pbar.n
                total = progress.pbar.total
                self.progress_queue.put(("update", value, total))
            return result
            
        def finish_wrapper(*args, **kwargs):
            message = args[0] if args else ""
            result = original_finish(*args, **kwargs)
            # Fertigstellung signalisieren
            self.progress_queue.put(("finish", message))
            return result
            
        # Ersetze die Original-Methoden
        progress.start = start_wrapper
        progress.update = update_wrapper
        progress.finish = finish_wrapper
        
    def _process_log_queue(self):
        """Verarbeitet die Log-Queue und zeigt Nachrichten in der UI an"""
        try:
            while not self.log_queue.empty():
                record = self.log_queue.get(block=False)
                self._display_log(record)
        except queue.Empty:
            pass
        
        # Nächsten Verarbeitungszyklus planen, wenn nicht gestoppt
        if not self.stop_event.is_set():
            self.root.after(100, self._process_log_queue)
            
    def _process_progress_queue(self):
        """Verarbeitet die Fortschritts-Queue und aktualisiert die Fortschrittsanzeige"""
        try:
            while not self.progress_queue.empty():
                event = self.progress_queue.get(block=False)
                self._update_progress_display(event)
        except queue.Empty:
            pass
        
        # Nächsten Verarbeitungszyklus planen, wenn nicht gestoppt
        if not self.stop_event.is_set():
            self.root.after(100, self._process_progress_queue)
            
    def _display_log(self, record):
        """Zeigt einen Log-Eintrag im Text-Widget an"""
        msg = self.format_log_record(record)
        level = record.levelname
        
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + '\n', level)
        self.log_text.see(tk.END)  # Automatisch zum Ende scrollen
        self.log_text.config(state=tk.DISABLED)
        
    def format_log_record(self, record):
        """Formatiert einen Log-Eintrag für die Anzeige"""
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        return formatter.format(record)
        
    def _update_progress_display(self, event):
        """Aktualisiert die Fortschrittsanzeige basierend auf dem Event"""
        event_type = event[0]
        
        if event_type == "start":
            _, desc, total, unit = event
            self.progress_desc_var.set(desc)
            self.progress_bar["maximum"] = total
            self.progress_bar["value"] = 0
            self.progress_details_var.set(f"0/{total} {unit}")
            
        elif event_type == "update":
            _, value, total = event
            self.progress_bar["value"] = value
            percent = int((value / total) * 100) if total > 0 else 0
            self.progress_details_var.set(f"{value}/{total} ({percent}%)")
            
        elif event_type == "finish":
            _, message = event
            self.progress_desc_var.set("Abgeschlossen")
            self.progress_bar["value"] = self.progress_bar["maximum"]
            self.progress_details_var.set(message)
            
    def _run_in_thread(self, task_func):
        """Führt eine Funktion in einem separaten Thread aus"""
        thread = threading.Thread(target=task_func)
        thread.daemon = True
        thread.start()
        
    def stop(self):
        """Stoppt den Controller und alle laufenden Prozesse"""
        self.stop_event.set()
        if self.rag_manager and self.rag_manager.auto_sync:
            self.rag_manager.stop_auto_sync()
            
        # Alle externen Prozesse beenden
        self.process_manager.cleanup_all_processes()
        
        logging.info("Controller und alle Prozesse gestoppt")


# Hauptfunktion zum Starten der Anwendung
def main():
    root = tk.Tk()
    app = AgentController(root)
    
    # Event-Handler für das Schließen des Fensters
    def on_closing():
        app.stop()
        root.destroy()
        
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()
    
if __name__ == "__main__":
    main()