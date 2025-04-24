import os
import sys
import subprocess
import webview
import json
import threading
import time
import signal
from pathlib import Path

class ProcessManager:
    """Stellt Methoden zum Starten und Stoppen von Prozessen bereit."""
    
    def __init__(self):
        self.processes = {}
        self.process_counter = 0
    
    def startProcess(self, program, args=None):
        """
        Startet einen Prozess und gibt eine ID zurück.
        
        Args:
            program: Der Pfad zum ausführbaren Programm
            args: Eine Liste von Argumenten (optional)
            
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
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                else:
                    # Linux/Mac
                    process = subprocess.Popen(
                        [program] + args, 
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
            else:
                # Ohne Argumente
                if sys.platform == 'win32':
                    process = subprocess.Popen(
                        program,
                        env=env,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
                else:
                    process = subprocess.Popen(
                        program,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE
                    )
            
            # Prozess speichern
            self.process_counter += 1
            process_id = str(self.process_counter)
            self.processes[process_id] = process
            
            print(f"Prozess {process_id} gestartet: {program} {' '.join(args) if args else ''}")
            
            # Ausgabe im Hintergrund lesen
            def read_output(proc):
                for line in proc.stdout:
                    print(f"[Prozess {process_id}] {line.decode('utf-8', errors='replace').strip()}")
            
            threading.Thread(target=read_output, args=(process,), daemon=True).start()
            
            return process_id
        except Exception as e:
            print(f"Fehler beim Starten des Prozesses: {str(e)}")
            return None
    
    def stopProcess(self, process_id):
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
                print(f"Prozess {process_id} gestoppt")
                return True
            except Exception as e:
                print(f"Fehler beim Stoppen des Prozesses {process_id}: {str(e)}")
                return False
        else:
            print(f"Prozess-ID {process_id} nicht gefunden")
            return False
    
    def killProcess(self, name):
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
                print(f"Prozesse beendet: {output.decode('utf-8', errors='replace')}")
                count = 1  # Genaue Anzahl nicht bekannt
            else:
                # Linux/Mac: killall
                output = subprocess.check_output(['killall', '-9', name], stderr=subprocess.STDOUT)
                print(f"Prozesse beendet: {output.decode('utf-8', errors='replace')}")
                count = 1  # Genaue Anzahl nicht bekannt
        except subprocess.CalledProcessError as e:
            # Befehl fehlgeschlagen, wahrscheinlich kein Prozess gefunden
            print(f"Keine Prozesse mit Namen '{name}' gefunden: {e.output.decode('utf-8', errors='replace')}")
        except Exception as e:
            print(f"Fehler beim Beenden von Prozessen mit Namen '{name}': {str(e)}")
        
        return count

    def cleanupAllProcesses(self):
        """Beendet alle gestarteten Prozesse."""
        for process_id in list(self.processes.keys()):
            self.stopProcess(process_id)
        
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
    def __init__(self):
        self.html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agent_ui.html')
        self.process_manager = ProcessManager()
        
        # Stelle sicher, dass die HTML-Datei existiert
        if not os.path.exists(self.html_path):
            self._create_html_file()
    
    def _create_html_file(self):
        """Erstellt die HTML-Datei, falls sie nicht existiert."""
        print("HTML-UI-Datei wird erstellt...")
        
        html_content = """<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CodeContextAI Control Panel</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
            color: #333;
        }
        .header {
            background-color: #2c3e50;
            color: white;
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 {
            margin: 0;
            font-size: 24px;
        }
        .status-badge {
            padding: 5px 10px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: bold;
        }
        .status-online {
            background-color: #27ae60;
        }
        .status-offline {
            background-color: #e74c3c;
        }
        .card {
            background-color: white;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            padding: 20px;
            margin-bottom: 20px;
        }
        .card h2 {
            margin-top: 0;
            font-size: 18px;
            border-bottom: 1px solid #eee;
            padding-bottom: 10px;
            color: #2c3e50;
        }
        .action-button {
            background-color: #3498db;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            cursor: pointer;
            font-weight: 600;
            transition: background-color 0.2s;
        }
        .action-button:disabled {
            background-color: #95a5a6;
            cursor: not-allowed;
        }
        .action-button:hover:not(:disabled) {
            background-color: #2980b9;
        }
        .action-button.danger {
            background-color: #e74c3c;
        }
        .action-button.danger:hover:not(:disabled) {
            background-color: #c0392b;
        }
        .action-button.success {
            background-color: #27ae60;
        }
        .action-button.success:hover:not(:disabled) {
            background-color: #219651;
        }
        .button-group {
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }
        .log-panel {
            background-color: #2c3e50;
            color: #ecf0f1;
            border-radius: 4px;
            padding: 15px;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 14px;
            height: 200px;
            overflow-y: auto;
            margin-top: 15px;
        }
        .log-entry {
            margin: 5px 0;
            line-height: 1.4;
        }
        .log-info {
            color: #3498db;
        }
        .log-success {
            color: #2ecc71;
        }
        .log-error {
            color: #e74c3c;
        }
        .spinner {
            width: 20px;
            height: 20px;
            border: 3px solid rgba(255, 255, 255, 0.3);
            border-radius: 50%;
            border-top-color: white;
            animation: spin 1s ease-in-out infinite;
            display: inline-block;
            vertical-align: middle;
            margin-right: 10px;
        }
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        .hidden {
            display: none;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>CodeContextAI Controller</h1>
        <div class="status-badge status-offline" id="agent-status">Offline</div>
    </div>

    <div class="card">
        <h2>Agent Steuerung</h2>
        <div class="button-group">
            <button id="start-agent" class="action-button success">Agent starten</button>
            <button id="stop-agent" class="action-button danger" disabled>Agent stoppen</button>
        </div>
        
        <div class="log-panel" id="log-panel">
            <div class="log-entry">Bereit. Starten Sie den Agenten, um zu beginnen.</div>
        </div>
    </div>

    <div class="card">
        <h2>Ngrok Tunnel</h2>
        <div id="tunnel-info">
            <p>Kein aktiver Tunnel vorhanden.</p>
        </div>
        <div class="button-group">
            <button id="start-tunnel" class="action-button success" disabled>Tunnel starten</button>
            <button id="stop-tunnel" class="action-button danger" disabled>Tunnel stoppen</button>
        </div>
    </div>

    <div class="card">
        <h2>Synchronisation</h2>
        <p>Synchronisieren Sie das Projektverzeichnis mit dem Index.</p>
        <div class="button-group">
            <button id="sync-project" class="action-button" disabled>Jetzt synchronisieren</button>
            <button id="rebuild-index" class="action-button danger" disabled>Index neu aufbauen</button>
        </div>
    </div>

    <script>
        // Status-Variablen
        let agentRunning = false;
        let tunnelRunning = false;
        let agentProcess = null;
        let tunnelProcess = null;
        
        // Elemente
        const agentStatusBadge = document.getElementById('agent-status');
        const startAgentBtn = document.getElementById('start-agent');
        const stopAgentBtn = document.getElementById('stop-agent');
        const logPanel = document.getElementById('log-panel');
        
        const startTunnelBtn = document.getElementById('start-tunnel');
        const stopTunnelBtn = document.getElementById('stop-tunnel');
        const tunnelInfo = document.getElementById('tunnel-info');
        
        const syncProjectBtn = document.getElementById('sync-project');
        const rebuildIndexBtn = document.getElementById('rebuild-index');
        
        // API URL
        const API_URL = 'http://localhost:8000';
        
        // Helper
        function logMessage(message, type = 'normal') {
            const entry = document.createElement('div');
            entry.className = `log-entry log-${type}`;
            entry.textContent = message;
            logPanel.appendChild(entry);
            logPanel.scrollTop = logPanel.scrollHeight;
        }
        
        function updateAgentStatus(running) {
            agentRunning = running;
            
            if (running) {
                agentStatusBadge.textContent = 'Online';
                agentStatusBadge.className = 'status-badge status-online';
                startAgentBtn.disabled = true;
                stopAgentBtn.disabled = false;
                startTunnelBtn.disabled = false;
                syncProjectBtn.disabled = false;
                rebuildIndexBtn.disabled = false;
            } else {
                agentStatusBadge.textContent = 'Offline';
                agentStatusBadge.className = 'status-badge status-offline';
                startAgentBtn.disabled = false;
                stopAgentBtn.disabled = true;
                startTunnelBtn.disabled = true;
                stopTunnelBtn.disabled = true;
                syncProjectBtn.disabled = true;
                rebuildIndexBtn.disabled = true;
            }
        }
        
        function updateTunnelStatus(running, url = '') {
            tunnelRunning = running;
            
            if (running && url) {
                startTunnelBtn.disabled = true;
                stopTunnelBtn.disabled = false;
                tunnelInfo.innerHTML = `
                    <p>Aktiver Tunnel:</p>
                    <div style="background: #f1f1f1; padding: 10px; border-radius: 4px; font-family: monospace;">
                        <a href="${url}" target="_blank">${url}</a>
                    </div>
                `;
            } else {
                startTunnelBtn.disabled = !agentRunning;
                stopTunnelBtn.disabled = true;
                tunnelInfo.innerHTML = '<p>Kein aktiver Tunnel vorhanden.</p>';
            }
        }
        
        // API Funktionen
        async function checkApiStatus() {
            try {
                const response = await fetch(`${API_URL}/`);
                
                if (response.ok) {
                    updateAgentStatus(true);
                    return true;
                } else {
                    updateAgentStatus(false);
                    return false;
                }
            } catch (error) {
                updateAgentStatus(false);
                return false;
            }
        }
        
        // Event Listeners
        startAgentBtn.addEventListener('click', async function() {
            logMessage('Starte CodeContextAI Agent...', 'info');
            
            try {
                // Starte den Agenten mit Python-Funktion
                agentProcess = await pywebview.api.startProcess('uvicorn', ['main:app', '--host', '0.0.0.0', '--port', '8000']);
                
                // Warte kurz und prüfe den Status
                setTimeout(async function() {
                    const isRunning = await checkApiStatus();
                    
                    if (isRunning) {
                        logMessage('Agent erfolgreich gestartet!', 'success');
                    } else {
                        logMessage('Agent konnte nicht gestartet werden.', 'error');
                    }
                }, 3000);
                
            } catch (error) {
                logMessage(`Fehler beim Starten des Agenten: ${error}`, 'error');
                updateAgentStatus(false);
            }
        });
        
        stopAgentBtn.addEventListener('click', async function() {
            logMessage('Stoppe CodeContextAI Agent...', 'info');
            
            try {
                if (tunnelRunning) {
                    await pywebview.api.killProcess('ngrok');
                    updateTunnelStatus(false);
                }
                
                if (agentProcess) {
                    await pywebview.api.stopProcess(agentProcess);
                    agentProcess = null;
                } else {
                    await pywebview.api.killProcess('uvicorn');
                }
                
                updateAgentStatus(false);
                logMessage('Agent gestoppt.', 'success');
            } catch (error) {
                logMessage(`Fehler beim Stoppen des Agenten: ${error}`, 'error');
            }
        });
        
        startTunnelBtn.addEventListener('click', async function() {
            logMessage('Starte ngrok Tunnel...', 'info');
            
            try {
                tunnelProcess = await pywebview.api.startProcess('ngrok', ['http', '8000']);
                
                // Warte kurz, dann hole die Tunnel-URL
                setTimeout(async function() {
                    try {
                        const response = await fetch('http://localhost:4040/api/tunnels');
                        if (response.ok) {
                            const data = await response.json();
                            if (data.tunnels && data.tunnels.length > 0) {
                                const httpsUrl = data.tunnels.find(tunnel => 
                                    tunnel.public_url.startsWith('https://')
                                )?.public_url;
                                
                                if (httpsUrl) {
                                    updateTunnelStatus(true, httpsUrl);
                                    logMessage(`Tunnel erfolgreich erstellt: ${httpsUrl}`, 'success');
                                } else {
                                    logMessage('Keine HTTPS-URL im Tunnel gefunden.', 'error');
                                    updateTunnelStatus(false);
                                }
                            } else {
                                logMessage('Keine Tunnel gefunden.', 'error');
                                updateTunnelStatus(false);
                            }
                        } else {
                            logMessage('Konnte Tunnel-Informationen nicht abrufen.', 'error');
                            updateTunnelStatus(false);
                        }
                    } catch (error) {
                        logMessage('Fehler beim Abrufen der Tunnel-URL.', 'error');
                        updateTunnelStatus(true, 'Tunnel läuft, URL konnte nicht ermittelt werden');
                    }
                }, 2000);
                
            } catch (error) {
                logMessage(`Fehler beim Starten des Tunnels: ${error}`, 'error');
                updateTunnelStatus(false);
            }
        });
        
        stopTunnelBtn.addEventListener('click', async function() {
            logMessage('Stoppe ngrok Tunnel...', 'info');
            
            try {
                if (tunnelProcess) {
                    await pywebview.api.stopProcess(tunnelProcess);
                    tunnelProcess = null;
                } else {
                    await pywebview.api.killProcess('ngrok');
                }
                
                updateTunnelStatus(false);
                logMessage('Tunnel gestoppt.', 'success');
            } catch (error) {
                logMessage(`Fehler beim Stoppen des Tunnels: ${error}`, 'error');
            }
        });
        
        syncProjectBtn.addEventListener('click', async function() {
            if (!agentRunning) return;
            
            logMessage('Starte Projektsynchronisation...', 'info');
            
            try {
                const response = await fetch(`${API_URL}/sync?wait=true`);
                
                if (response.ok) {
                    const data = await response.json();
                    if (data.status === 'success') {
                        const changedFiles = data.changed_files || [];
                        logMessage(`Synchronisation abgeschlossen: ${changedFiles.length} Dateien geändert.`, 'success');
                    } else {
                        logMessage(`Synchronisation fehlgeschlagen: ${data.message}`, 'error');
                    }
                } else {
                    logMessage('Synchronisation fehlgeschlagen: API-Fehler', 'error');
                }
            } catch (error) {
                logMessage(`Fehler bei der Synchronisation: ${error}`, 'error');
            }
        });
        
        rebuildIndexBtn.addEventListener('click', async function() {
            if (!agentRunning) return;
            
            if (!confirm('Soll der Index wirklich komplett neu aufgebaut werden? Dies kann bei großen Projekten einige Zeit dauern.')) {
                return;
            }
            
            logMessage('Starte kompletten Neuaufbau des Index...', 'info');
            
            try {
                const response = await fetch(`${API_URL}/rebuild?wait=true`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        force_rebuild: true
                    })
                });
                
                if (response.ok) {
                    const data = await response.json();
                    if (data.status === 'success') {
                        logMessage(`Index-Neuaufbau abgeschlossen: ${data.chunks_processed || 0} Chunks verarbeitet.`, 'success');
                    } else {
                        logMessage(`Index-Neuaufbau fehlgeschlagen: ${data.message}`, 'error');
                    }
                } else {
                    logMessage('Index-Neuaufbau fehlgeschlagen: API-Fehler', 'error');
                }
            } catch (error) {
                logMessage(`Fehler beim Index-Neuaufbau: ${error}`, 'error');
            }
        });
        
        // Initialisierung
        async function initialize() {
            logMessage('Prüfe API-Status...', 'info');
            const isRunning = await checkApiStatus();
            
            if (isRunning) {
                logMessage('Agent bereits gestartet.', 'success');
                
                // Prüfe, ob ein Tunnel läuft
                try {
                    const response = await fetch('http://localhost:4040/api/tunnels');
                    if (response.ok) {
                        const data = await response.json();
                        if (data.tunnels && data.tunnels.length > 0) {
                            const httpsUrl = data.tunnels.find(tunnel => 
                                tunnel.public_url.startsWith('https://')
                            )?.public_url;
                            
                            if (httpsUrl) {
                                updateTunnelStatus(true, httpsUrl);
                                logMessage(`Aktiver Tunnel gefunden: ${httpsUrl}`, 'success');
                            }
                        }
                    }
                } catch (error) {
                    // Kein Tunnel aktiv, ignorieren
                }
            } else {
                logMessage('Agent ist nicht gestartet. Verwenden Sie den "Agent starten" Button.', 'info');
            }
        }
        
        // Führe Initialisierung aus, wenn DOM geladen ist
        document.addEventListener('DOMContentLoaded', initialize);
        
        // Prüfe regelmäßig den Status
        setInterval(checkApiStatus, 5000);
        
        // Log, dass das UI geladen wurde
        logMessage('UI geladen und bereit.', 'info');
    </script>
</body>
</html>"""
        
        # Speichern der HTML-Datei
        with open(self.html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"HTML-UI-Datei erstellt unter: {self.html_path}")
    
    def run(self):
        """Startet die Anwendung."""
        # Erstelle das Webview-Fenster erst hier für bessere Kompatibilität
        window = webview.create_window(
            'CodeContextAI Controller',
            self.html_path,
            js_api=self.process_manager,
            width=900,
            height=800,
            min_size=(800, 600)
        )
        
        def clean_up():
            print("Anwendung wird beendet, stoppe alle Prozesse...")
            self.process_manager.cleanupAllProcesses()
            
        # Verwende das on_closing-Event, wenn verfügbar
        try:
            window.events.closing += clean_up
            print("Event-Handler für Fenster-Schließen registriert")
        except AttributeError:
            print("Fenster-Schließen-Event nicht verfügbar, verwende alternativen Ansatz")
        
        # Starte das Webview mit Cleanup bei Beendigung
        try:
            webview.start(debug=True)
        finally:
            # Stelle sicher, dass Prozesse bereinigt werden, auch wenn kein Event verfügbar ist
            clean_up()


def main():
    # Stelle sicher, dass die erforderlichen Abhängigkeiten installiert sind
    try:
        import webview
    except ImportError:
        print("Fehler: Das Modul 'webview' ist nicht installiert.")
        print("Bitte installieren Sie es mit 'pip install pywebview'.")
        return
    
    # Prüfe, ob uvicorn und ngrok verfügbar sind
    try:
        if sys.platform == 'win32':
            # Windows: where Befehl
            subprocess.check_output(['where', 'uvicorn'], stderr=subprocess.STDOUT)
            subprocess.check_output(['where', 'ngrok'], stderr=subprocess.STDOUT)
        else:
            # Linux/Mac: which Befehl
            subprocess.check_output(['which', 'uvicorn'], stderr=subprocess.STDOUT)
            subprocess.check_output(['which', 'ngrok'], stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError:
        print("Fehler: 'uvicorn' oder 'ngrok' wurden nicht gefunden.")
        print("Bitte stellen Sie sicher, dass beide Programme installiert sind und im PATH verfügbar sind.")
        print("Installation:")
        print("  uvicorn: pip install uvicorn")
        print("  ngrok: Herunterladen von https://ngrok.com/download und im PATH platzieren")
        return
    
    # Starte den Controller
    controller = AgentController()
    controller.run()


if __name__ == "__main__":
    main()