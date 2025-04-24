# app/change_analyzer.py

import os
import json
import hashlib
import difflib
from datetime import datetime
from typing import Dict, List

class ChangeAnalyzer:
    def __init__(self, repo_path: str, history_file: str = ".change_history.json"):
        self.repo_path = repo_path
        self.history_path = os.path.join(repo_path, history_file)
        self.history: Dict[str, List[Dict]] = self._load_history()

    def track_change(self, file_path: str, content: str):
        sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        timestamp = datetime.now().isoformat()
        rel_path = os.path.relpath(file_path, self.repo_path)
        self.history.setdefault(rel_path, []).append({
            "timestamp": timestamp,
            "sha": sha,
            "content": content
        })
        self._save_history()

    def compare_versions(self, file_path: str, version_idx_1: int = -2, version_idx_2: int = -1) -> str:
        rel_path = os.path.relpath(file_path, self.repo_path)
        history = self.history.get(rel_path, [])
        if len(history) < 2:
            return "Not enough history to compare."
        a = history[version_idx_1]["content"].splitlines()
        b = history[version_idx_2]["content"].splitlines()
        diff = difflib.unified_diff(a, b, fromfile="before", tofile="after", lineterm='')
        return "\n".join(diff)

    def _load_history(self):
        if os.path.exists(self.history_path):
            with open(self.history_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_history(self):
        with open(self.history_path, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2)
