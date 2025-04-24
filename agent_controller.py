import os
import sys
import subprocess
import webview
import json
import threading
import time
import signal
import webbrowser
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
        
        # Erstelle das Webview-Fenster
        self.window = webview.create_window(
            'CodeContextAI Controller',
            self.html_path,
            js_api=self.process_manager,
            width=900,
            height=800,
            min_size=(800, 600)
        )
    
    def _create_html_file(self):
        """Erstellt die HTML-Datei, falls sie nicht existiert."""
        print("HTML-UI-Datei wird erstellt...")
        
        # Holen Sie den HTML-Inhalt aus dem Artefakt oder definieren Sie ihn direkt hier
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
        .tunnel-url {
            font-family: 'Consolas', 'Courier New', monospace;
            background-color: #f1f1f1;
            padding: 10px;
            border-radius: 4px;
            word-break: break-all;
        }
        .tunnel-url a {
            color: #3498db;
            text-decoration: none;
        }
        .tunnel-url a:hover {
            text-decoration: underline;
        }
        .info-box {
            background-color: #d6eaf8;
            border-left: 4px solid #3498db;
            padding: 10px 15px;
            margin: 15px 0;
            border-radius: 0 4px 4px 0;
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
        .settings-row {
            display: flex;
            align-items: center;
            margin-bottom: 10px;
        }
        .settings-row label {
            flex: 0 0 180px;
            font-weight: 600;
        }
        .settings-row input[type="text"],
        .settings-row input[type="number"] {
            flex: 1;
            padding: 8px;
            border: 1px solid #ddd;
            border-radius: 4px;
        }
        .settings-row input[type="checkbox"] {
            width: 18px;
            height: 18px;
        }
        .system-info {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
            margin-top: 15px;
        }
        .info-item {
            background-color: #f1f1f1;
            padding: 10px;
            border-radius: 4px;
        }
        .info-item-label {
            font-weight: 600;
            margin-bottom: 5px;
            color: #7f8c8d;
            font-size: 12px;
        }
        .info-item-value {
            font-family: 'Consolas', 'Courier New', monospace;
            word-break: break-all;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>CodeContextAI Control Panel</h1>
        <div class="status-badge status-offline" id="agent-status">Offline</div>
    </div>

    <div class="card">
        <h2>Agent Steuerung</h2>
        <div class="button-group">
            <button id="start-agent" class="action-button success">
                <span id="start-spinner" class="spinner hidden"></span>
                Agent starten
            </button>
            <button id="stop-agent" class="action-button danger" disabled>Agent stoppen</button>
            <button id="restart-agent" class="action-button" disabled>Neustart</button>
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
            <button id="start-tunnel" class="action-button success" disabled>
                <span id="tunnel-spinner" class="spinner hidden"></span>
                Tunnel starten
            </button>
            <button id="stop-tunnel" class="action-button danger" disabled>Tunnel stoppen</button>
        </div>
    </div>

    <div class="card">
        <h2>Synchronisation</h2>
        <p>Synchronisieren Sie das Projektverzeichnis mit dem Index.</p>
        <div class="button-group">
            <button id="sync-project" class="action-button" disabled>
                <span id="sync-spinner" class="spinner hidden"></span>
                Jetzt synchronisieren
            </button>
            <button id="rebuild-index" class="action-button danger" disabled>
                <span id="rebuild-spinner" class="spinner hidden"></span>
                Index neu aufbauen
            </button>
        </div>
        
        <div class="settings-row" style="margin-top: 15px;">
            <label for="auto-sync-toggle">Automatische Synchronisation:</label>
            <input type="checkbox" id="auto-sync-toggle" disabled>
        </div>
        
        <div class="settings-row">
            <label for="sync-interval">Sync-Intervall (Sekunden):</label>
            <input type="number" id="sync-interval" min="30" value="300" disabled>
        </div>
        
        <div class="button-group">
            <button id="save-sync-settings" class="action-button" disabled>Einstellungen speichern</button>
        </div>
    </div>

    <div class="card">
        <h2>System Informationen</h2>
        <div class="system-info">
            <div class="info-item">
                <div class="info-item-label">Projektpfad</div>
                <div class="info-item-value" id="project-path">-</div>
            </div>
            <div class="info-item">
                <div class="info-item-label">Letzte Synchronisation</div>
                <div class="info-item-value" id="last-sync">-</div>
            </div>
            <div class="info-item">
                <div class="info-item-label">Index Status</div>
                <div class="info-item-value" id="index-status">-</div>
            </div>
            <div class="info-item">
                <div class="info-item-label">API Status</div>
                <div class="info-item-value" id="api-status">-</div>
            </div>
        </div>
    </div>

    <script>
        // Konfiguration
        const API_URL = 'http://localhost:8000';  // URL der FastAPI
        const API_KEY = 'your-secret-api-key';    // API Key aus der .env Datei
        
        // Status-Variablen
        let agentRunning = false;
        let tunnelRunning = false;
        let agentProcess = null;
        let tunnelProcess = null;
        let tunnelUrl = '';
        
        // Elemente
        const agentStatusBadge = document.getElementById('agent-status');
        const startAgentBtn = document.getElementById('start-agent');
        const stopAgentBtn = document.getElementById('stop-agent');
        const restartAgentBtn = document.getElementById('restart-agent');
        const logPanel = document.getElementById('log-panel');
        
        const startTunnelBtn = document.getElementById('start-tunnel');
        const stopTunnelBtn = document.getElementById('stop-tunnel');
        const tunnelInfo = document.getElementById('tunnel-info');
        
        const syncProjectBtn = document.getElementById('sync-project');
        const rebuildIndexBtn = document.getElementById('rebuild-index');
        const autoSyncToggle = document.getElementById('auto-sync-toggle');
        const syncIntervalInput = document.getElementById('sync-interval');
        const saveSyncSettingsBtn = document.getElementById('save-sync-settings');
        
        const projectPathInfo = document.getElementById('project-path');
        const lastSyncInfo = document.getElementById('last-sync');
        const indexStatusInfo = document.getElementById('index-status');
        const apiStatusInfo = document.getElementById('api-status');
        
        // Spinner
        const startSpinner = document.getElementById('start-spinner');
        const tunnelSpinner = document.getElementById('tunnel-spinner');
        const syncSpinner = document.getElementById('sync-spinner');
        const rebuildSpinner = document.getElementById('rebuild-spinner');
        
        // Helper
        function showSpinner(spinner) {
            spinner.classList.remove('hidden');
        }
        
        function hideSpinner(spinner) {
            spinner.classList.add('hidden');
        }
        
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
                restartAgentBtn.disabled = false;
                startTunnelBtn.disabled = false;
                syncProjectBtn.disabled = false;
                rebuildIndexBtn.disabled = false;
                autoSyncToggle.disabled = false;
                syncIntervalInput.disabled = false;
                saveSyncSettingsBtn.disabled = false;
                apiStatusInfo.textContent = 'Online';
            } else {
                agentStatusBadge.textContent = 'Offline';
                agentStatusBadge.className = 'status-badge status-offline';
                startAgentBtn.disabled = false;
                stopAgentBtn.disabled = true;
                restartAgentBtn.disabled = true;
                startTunnelBtn.disabled = true;
                stopTunnelBtn.disabled = true;
                syncProjectBtn.disabled = true;
                rebuildIndexBtn.disabled = true;
                autoSyncToggle.disabled = true;
                syncIntervalInput.disabled = true;
                saveSyncSettingsBtn.disabled = true;
                apiStatusInfo.textContent = 'Offline';
            }
        }
        
        function updateTunnelStatus(running, url = '') {
            tunnelRunning = running;
            tunnelUrl = url;
            
            if (running && url) {
                startTunnelBtn.disabled = true;
                stopTunnelBtn.disabled = false;
                tunnelInfo.innerHTML = `
                    <p>Aktiver Tunnel:</p>
                    <div class="tunnel-url">
                        <a href="${url}" target="_blank">${url}</a>
                    </div>
                    <div class="info-box">
                        Klicken Sie auf die URL, um die API in einem neuen Tab zu öffnen.
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
                const response = await fetch(`${API_URL}/`, {
                    headers: {
                        'X-API-Key': API_KEY
                    }
                });
                
                if (response.ok) {
                    const data = await response.json();
                    updateAgentStatus(true);
                    projectPathInfo.textContent = data.local_path || '-';
                    lastSyncInfo.textContent = data.last_sync ? new Date(data.last_sync).toLocaleString() : 'Noch nie';
                    
                    // Auto-Sync Einstellungen
                    autoSyncToggle.checked = data.auto_sync;
                    syncIntervalInput.value = data.sync_interval || 300;
                    
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
        
        async function startAgent() {
            showSpinner(startSpinner);
            logMessage('Starte CodeContextAI Agent...', 'info');
            
            try {
                // Windows-spezifischer Befehl zum Starten des Agenten
                agentProcess = await window.chrome.webview.hostObjects.processManager.startProcess(
                    'uvicorn', 
                    ['main:app', '--host', '0.0.0.0', '--port', '8000', '--reload']
                );
                
                // Warte kurz und prüfe den Status
                await new Promise(resolve => setTimeout(resolve, 3000));
                const isRunning = await checkApiStatus();
                
                if (isRunning) {
                    logMessage('Agent erfolgreich gestartet!', 'success');
                    updateIndexStatus();
                } else {
                    logMessage('Agent konnte nicht gestartet werden.', 'error');
                }
            } catch (error) {
                logMessage(`Fehler beim Starten des Agenten: ${error.message}`, 'error');
                updateAgentStatus(false);
            } finally {
                hideSpinner(startSpinner);
            }
        }
        
        async function stopAgent() {
            logMessage('Stoppe CodeContextAI Agent...', 'info');
            
            try {
                if (tunnelRunning) {
                    await stopTunnel();
                }
                
                // Windows-spezifischer Befehl zum Stoppen des Agenten
                if (agentProcess) {
                    await window.chrome.webview.hostObjects.processManager.stopProcess(agentProcess);
                    agentProcess = null;
                } else {
                    await window.chrome.webview.hostObjects.processManager.killProcess('uvicorn');
                }
                
                updateAgentStatus(false);
                logMessage('Agent gestoppt.', 'success');
            } catch (error) {
                logMessage(`Fehler beim Stoppen des Agenten: ${error.message}`, 'error');
            }
        }
        
        async function startTunnel() {
            showSpinner(tunnelSpinner);
            logMessage('Starte ngrok Tunnel...', 'info');
            
            try {
                // Windows-spezifischer Befehl zum Starten von ngrok
                tunnelProcess = await window.chrome.webview.hostObjects.processManager.startProcess(
                    'ngrok', 
                    ['http', '8000']
                );
                
                // Warte kurz, dann hole die Tunnel-URL
                await new Promise(resolve => setTimeout(resolve, 2000));
                
                try {
                    const response = await fetch('http://localhost:4040/api/tunnels');
                    if (response.ok) {
                        const data = await response.json();
                        if (data.tunnels && data.tunnels.length > 0) {
                            // Suche die HTTPS URL
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
                
            } catch (error) {
                logMessage(`Fehler beim Starten des Tunnels: ${error.message}`, 'error');
                updateTunnelStatus(false);
            } finally {
                hideSpinner(tunnelSpinner);
            }
        }
        
        async function stopTunnel() {
            logMessage('Stoppe ngrok Tunnel...', 'info');
            
            try {
                // Windows-spezifischer Befehl zum Stoppen von ngrok
                if (tunnelProcess) {
                    await window.chrome.webview.hostObjects.processManager.stopProcess(tunnelProcess);
                    tunnelProcess = null;
                } else {
                    await window.chrome.webview.hostObjects.processManager.killProcess('ngrok');
                }
                
                updateTunnelStatus(false);
                logMessage('Tunnel gestoppt.', 'success');
            } catch (error) {
                logMessage(`Fehler beim Stoppen des Tunnels: ${error.message}`, 'error');
            }
        }
        
        async function syncProject() {
            if (!agentRunning) return;
            
            showSpinner(syncSpinner);
            logMessage('Starte Projektsynchronisation...', 'info');
            
            try {
                const response = await fetch(`${API_URL}/sync?wait=true`, {
                    headers: {
                        'X-API-Key': API_KEY
                    }
                });
                
                if (response.ok) {
                    const data = await response.json();
                    if (data.status === 'success') {
                        const changedFiles = data.changed_files || [];
                        logMessage(`Synchronisation abgeschlossen: ${changedFiles.length} Dateien geändert.`, 'success');
                        
                        // Aktualisiere Last-Sync Info
                        lastSyncInfo.textContent = new Date().toLocaleString();
                    } else {
                        logMessage(`Synchronisation fehlgeschlagen: ${data.message}`, 'error');
                    }
                } else {
                    logMessage('Synchronisation fehlgeschlagen: API-Fehler', 'error');
                }
            } catch (error) {
                logMessage(`Fehler bei der Synchronisation: ${error.message}`, 'error');
            } finally {
                hideSpinner(syncSpinner);
            }
        }
        
        async function rebuildIndex() {
            if (!agentRunning) return;
            
            if (!confirm('Soll der Index wirklich komplett neu aufgebaut werden? Dies kann bei großen Projekten einige Zeit dauern.')) {
            return;
            }
            
            showSpinner(rebuildSpinner);
            logMessage('Starte kompletten Neuaufbau des Index...', 'info');
            
            try {
                const response = await fetch(`${API_URL}/rebuild?wait=true`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-API-Key': API_KEY
                    },
                    body: JSON.stringify({
                        force_rebuild: true
                    })
                });
                
                if (response.ok) {
                    const data = await response.json();
                    if (data.status === 'success') {
                        logMessage(`Index-Neuaufbau abgeschlossen: ${data.chunks_processed || 0} Chunks verarbeitet.`, 'success');
                        
                        // Aktualisiere Last-Sync Info
                        lastSyncInfo.textContent = new Date().toLocaleString();
                        
                        // Aktualisiere Index-Status
                        await updateIndexStatus();
                    } else {
                        logMessage(`Index-Neuaufbau fehlgeschlagen: ${data.message}`, 'error');
                    }
                } else {
                    logMessage('Index-Neuaufbau fehlgeschlagen: API-Fehler', 'error');
                }
            } catch (error) {
                logMessage(`Fehler beim Index-Neuaufbau: ${error.message}`, 'error');
            } finally {
                hideSpinner(rebuildSpinner);
            }
        }
        
        async function updateIndexStatus() {
            if (!agentRunning) return;
            
            try {
                const response = await fetch(`${API_URL}/dependencies`, {
                    headers: {
                        'X-API-Key': API_KEY
                    }
                });
                
                if (response.ok) {
                    const data = await response.json();
                    if (data.status === 'success') {
                        indexStatusInfo.textContent = `${data.node_count || 0} Knoten, ${data.edge_count || 0} Kanten`;
                    } else {
                        indexStatusInfo.textContent = 'Fehler beim Abrufen';
                    }
                } else {
                    indexStatusInfo.textContent = 'Fehler beim Abrufen';
                }
            } catch (error) {
                indexStatusInfo.textContent = 'Fehler beim Abrufen';
            }
        }
        
        async function updateAutoSyncSettings() {
            if (!agentRunning) return;
            
            const enabled = autoSyncToggle.checked;
            const interval = parseInt(syncIntervalInput.value, 10);
            
            if (isNaN(interval) || interval < 30) {
                alert('Das Sync-Intervall muss mindestens 30 Sekunden betragen.');
                return;
            }
            
            logMessage(`Aktualisiere Auto-Sync Einstellungen: ${enabled ? 'aktiviert' : 'deaktiviert'}, Intervall: ${interval}s`, 'info');
            
            try {
                const response = await fetch(`${API_URL}/auto_sync?enabled=${enabled}&interval=${interval}`, {
                    method: 'POST',
                    headers: {
                        'X-API-Key': API_KEY
                    }
                });
                
                if (response.ok) {
                    const data = await response.json();
                    if (data.status === 'success' || data.status === 'info') {
                        logMessage(`Auto-Sync Einstellungen aktualisiert: ${data.message}`, 'success');
                    } else {
                        logMessage(`Fehler beim Aktualisieren der Auto-Sync Einstellungen: ${data.message}`, 'error');
                    }
                } else {
                    logMessage('Fehler beim Aktualisieren der Auto-Sync Einstellungen: API-Fehler', 'error');
                }
            } catch (error) {
                logMessage(`Fehler beim Aktualisieren der Auto-Sync Einstellungen: ${error.message}`, 'error');
            }
        }
        
        // Event Listeners
        startAgentBtn.addEventListener('click', startAgent);
        stopAgentBtn.addEventListener('click', stopAgent);
        restartAgentBtn.addEventListener('click', async () => {
            await stopAgent();
            setTimeout(startAgent, 1000);
        });
        
        startTunnelBtn.addEventListener('click', startTunnel);
        stopTunnelBtn.addEventListener('click', stopTunnel);
        
        syncProjectBtn.addEventListener('click', syncProject);
        rebuildIndexBtn.addEventListener('click', rebuildIndex);
        saveSyncSettingsBtn.addEventListener('click', updateAutoSyncSettings);
        
        // Initialisierung
        async function initialize() {
            logMessage('Starte Initialisierung...', 'info');
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
                
                // Index-Status aktualisieren
                await updateIndexStatus();
            } else {
                logMessage('Agent ist nicht gestartet. Verwenden Sie den "Agent starten" Button.', 'info');
            }
        }
        
        // Starte Initialisierung
        initialize();
        
        // Status-Update alle 30 Sekunden
        setInterval(async () => {
            if (agentRunning) {
                await checkApiStatus();
            }
        }, 30000);
    </script>
</body>
</html>"""
        
        # Speichern der HTML-Datei
        with open(self.html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"HTML-UI-Datei erstellt unter: {self.html_path}")
    
    def run(self):
        """Startet die Anwendung."""
        # Event-Handler für das Schließen des Fensters
        def on_closing():
            print("Anwendung wird beendet, stoppe alle Prozesse...")
            self.process_manager.cleanupAllProcesses()
        
        self.window.closing += on_closing
        
        # Starte das Webview
        webview.start(debug=True)


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