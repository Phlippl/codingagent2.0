# app/__init__.py
# Package initialization
import logging
import sys
import time
from tqdm import tqdm
import colorlog
from logging.handlers import RotatingFileHandler

# Formatierung für farbige Konsolenausgaben
color_formatter = colorlog.ColoredFormatter(
    "%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    log_colors={
        'DEBUG': 'cyan',
        'INFO': 'green',
        'WARNING': 'yellow',
        'ERROR': 'red',
        'CRITICAL': 'red,bg_white',
    }
)

# Formatter für Datei-Logs
file_formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Root Logger konfigurieren
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)

# Handler für Konsole
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(color_formatter)
root_logger.addHandler(console_handler)

# Handler für Datei
file_handler = RotatingFileHandler(
    "app.log", 
    maxBytes=10*1024*1024,  # 10 MB
    backupCount=5
)
file_handler.setFormatter(file_formatter)
root_logger.addHandler(file_handler)

# Progress Bar Klasse
class ProgressTracker:
    """Utility Klasse für Fortschrittsbalken und Zeitverfolgung"""
    
    def __init__(self, desc="Processing", total=100, unit="items"):
        self.desc = desc
        self.total = total
        self.unit = unit
        self.pbar = None
        self.start_time = None
        
    def start(self, total=None, desc=None, unit=None):
        """Startet den Fortschrittsbalken"""
        if total is not None:
            self.total = total
        if desc is not None:
            self.desc = desc
        if unit is not None:
            self.unit = unit
            
        self.start_time = time.time()
        self.pbar = tqdm(total=self.total, desc=self.desc, unit=self.unit)
        return self
        
    def update(self, n=1):
        """Aktualisiert den Fortschrittsbalken"""
        if self.pbar:
            self.pbar.update(n)
            
    def set_description(self, desc):
        """Aktualisiert die Beschreibung"""
        if self.pbar:
            self.pbar.set_description(desc)
            
    def finish(self, message=None):
        """Beendet den Fortschrittsbalken und gibt die Gesamtzeit aus"""
        if self.pbar:
            self.pbar.close()
            
        if self.start_time:
            elapsed = time.time() - self.start_time
            if message:
                logging.info(f"{message} (Ausgeführt in {elapsed:.2f} Sekunden)")
            else:
                logging.info(f"Verarbeitung abgeschlossen in {elapsed:.2f} Sekunden")
                
        self.pbar = None
        self.start_time = None

# Globale Progress Tracker Instanz
progress = ProgressTracker()