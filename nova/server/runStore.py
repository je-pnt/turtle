"""
NOVA Run Store

Per-user run/replay storage.
Runs are NOT truth - they are user artifacts for export windows + UI convenience.

Phase 11 (phase9-11Updated.md):
- Runs drive UI clamp + bundle export only
- Creating/editing runs emits no truth events
- Last write wins for concurrent edits
- Always regenerate bundle on download (no reuse)

Storage Layout:
- User runs: data/users/<username>/runs/{runNumber}. {sanitizedRunName}/run.json
- Bundle: data/users/<username>/runs/{runNumber}. {sanitizedRunName}/bundle.zip

Run JSON Schema (v2 - manifest-driven):
Core fields (always present):
{
  "schemaVersion": 2,
  "runNumber": 1,
  "runName": "string",
  "runType": "string",           // Manifest-defined run type
  "timebase": "source" | "canonical",
  "startTimeSec": 0,
  "stopTimeSec": 0,
  "analystNotes": "",
  ...                            // Additional fields defined by runType manifest
}

Run types are discovered via plugin manifests in nova/core/manifests/runs/*.runManifest.py
"""

import json
import re
import shutil
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

from sdk.logging import getLogger


# Schema version
RUN_SCHEMA_VERSION = 2

# Valid timebases (core contract)
VALID_TIMEBASES = {'source', 'canonical'}

# Core fields that every run must have
CORE_FIELDS = {'schemaVersion', 'runNumber', 'runName', 'runType', 'timebase', 'startTimeSec', 'stopTimeSec', 'analystNotes'}


def sanitizeRunName(name: str) -> str:
    """
    Sanitize run name for filesystem safety.
    
    Rules:
    - Trim whitespace
    - Replace / \\ : * ? " < > | with _
    """
    if not name:
        return "Untitled"
    
    # Trim whitespace
    sanitized = name.strip()
    
    # Replace forbidden characters with _
    sanitized = re.sub(r'[/\\:*?"<>|]', '_', sanitized)
    
    # Collapse multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    
    # Ensure not empty after sanitization
    if not sanitized:
        return "Untitled"
    
    return sanitized


def buildRunFolderName(runNumber: int, runName: str) -> str:
    """Build folder name: {runNumber}. {sanitizedRunName}"""
    sanitized = sanitizeRunName(runName)
    return f"{runNumber}. {sanitized}"


@dataclass
class Run:
    """
    Run definition - manifest-driven, schema-agnostic.
    
    Core fields are always present:
    - schemaVersion, runNumber, runName, runType, timebase, startTimeSec, stopTimeSec, analystNotes
    
    Additional fields are stored in 'data' dict and defined by the runType's manifest.
    """
    schemaVersion: int
    runNumber: int
    runName: str
    runType: str
    timebase: str
    startTimeSec: float
    stopTimeSec: float
    analystNotes: str = ""
    data: Dict[str, Any] = field(default_factory=dict)  # Manifest-defined fields
    
    def toDict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        result = {
            'schemaVersion': self.schemaVersion,
            'runNumber': self.runNumber,
            'runName': self.runName,
            'runType': self.runType,
            'timebase': self.timebase,
            'startTimeSec': self.startTimeSec,
            'stopTimeSec': self.stopTimeSec,
            'analystNotes': self.analystNotes,
        }
        # Merge in manifest-defined fields at top level
        result.update(self.data)
        return result
    
    @classmethod
    def fromDict(cls, rawData: Dict[str, Any]) -> 'Run':
        """Create from dict, separating core fields from manifest data."""
        # Extract core fields
        core = {
            'schemaVersion': rawData.get('schemaVersion', RUN_SCHEMA_VERSION),
            'runNumber': rawData.get('runNumber', 1),
            'runName': rawData.get('runName', ''),
            'runType': rawData.get('runType', 'generic'),
            'timebase': rawData.get('timebase', 'canonical'),
            'startTimeSec': rawData.get('startTimeSec', 0),
            'stopTimeSec': rawData.get('stopTimeSec', 0),
            'analystNotes': rawData.get('analystNotes', ''),
        }
        
        # Everything else goes into data dict
        data = {k: v for k, v in rawData.items() if k not in CORE_FIELDS}
        
        return cls(**core, data=data)
    
    def validate(self) -> Optional[str]:
        """Validate core run fields. Returns error message if invalid, None if valid."""
        if self.timebase not in VALID_TIMEBASES:
            return f"Invalid timebase: {self.timebase}"
        if self.runNumber < 1:
            return "runNumber must be >= 1"
        # Note: runType is not validated here - manifest registry handles that
        return None


class RunStore:
    """
    Manages run storage.
    
    Not truth - user artifacts for export/UI convenience.
    Last write wins.
    """
    
    def __init__(self, dataPath: str = './nova/data'):
        self.log = getLogger()
        self.dataPath = Path(dataPath)
        self.usersPath = self.dataPath / 'users'
        
        # Ensure base directory exists
        self.usersPath.mkdir(parents=True, exist_ok=True)
    
    def _getUserRunsPath(self, username: str) -> Path:
        """Get user's runs directory path."""
        return self.usersPath / username / 'runs'
    
    def _getRunFolderPath(self, username: str, runNumber: int, runName: str) -> Path:
        """Get specific run folder path."""
        folderName = buildRunFolderName(runNumber, runName)
        return self._getUserRunsPath(username) / folderName
    
    def _findRunFolder(self, username: str, runNumber: int) -> Optional[Path]:
        """Find run folder by runNumber (folder name starts with '{runNumber}. ')."""
        runsPath = self._getUserRunsPath(username)
        if not runsPath.exists():
            return None
        
        prefix = f"{runNumber}. "
        for folder in runsPath.iterdir():
            if folder.is_dir() and folder.name.startswith(prefix):
                return folder
        return None
    
    # =========================================================================
    # CRUD Operations
    # =========================================================================
    
    def listRuns(self, username: str) -> List[Dict[str, Any]]:
        """
        List all runs for a user.
        
        Returns: List of run summaries (runNumber, runName, runType, timebase)
        """
        runsPath = self._getUserRunsPath(username)
        if not runsPath.exists():
            return []
        
        runs = []
        for folder in sorted(runsPath.iterdir()):
            if not folder.is_dir():
                continue
            
            runJsonPath = folder / 'run.json'
            if not runJsonPath.exists():
                continue
            
            try:
                with open(runJsonPath, 'r') as f:
                    data = json.load(f)
                runs.append({
                    'runNumber': data.get('runNumber'),
                    'runName': data.get('runName'),
                    'runType': data.get('runType'),
                    'timebase': data.get('timebase'),
                    'startTimeSec': data.get('startTimeSec'),
                    'stopTimeSec': data.get('stopTimeSec'),
                    'hasBundleZip': (folder / 'bundle.zip').exists()
                })
            except (json.JSONDecodeError, IOError) as e:
                self.log.warning(f"[RunStore] Failed to read {runJsonPath}: {e}")
                continue
        
        return runs
    
    def getRun(self, username: str, runNumber: int) -> Optional[Run]:
        """Get a specific run by number."""
        folder = self._findRunFolder(username, runNumber)
        if not folder:
            return None
        
        runJsonPath = folder / 'run.json'
        if not runJsonPath.exists():
            return None
        
        try:
            with open(runJsonPath, 'r') as f:
                data = json.load(f)
            return Run.fromDict(data)
        except (json.JSONDecodeError, IOError) as e:
            self.log.warning(f"[RunStore] Failed to read run {runNumber}: {e}")
            return None
    
    def createRun(self, username: str, runData: Dict[str, Any]) -> Run:
        """
        Create a new run.
        
        Args:
            username: User creating the run
            runData: Run data dict (core fields + manifest-defined fields)
                Required: runName, runType
                Optional: runNumber (auto-assigned if not provided), startTimeSec, stopTimeSec, etc.
        
        Server assigns runNumber (next available) if not provided or if collision.
        Timebase is set by server based on node mode (not client-controlled).
        """
        runsPath = self._getUserRunsPath(username)
        runsPath.mkdir(parents=True, exist_ok=True)
        
        # Find existing run numbers
        existingNumbers = []
        for folder in runsPath.iterdir():
            if folder.is_dir():
                match = re.match(r'^(\d+)\. ', folder.name)
                if match:
                    existingNumbers.append(int(match.group(1)))
        
        # Determine run number
        requestedNumber = runData.get('runNumber')
        if requestedNumber is None or requestedNumber in existingNumbers:
            runNumber = max(existingNumbers, default=0) + 1
        else:
            runNumber = requestedNumber
        
        # Build run with core fields + extra data
        runName = runData.get('runName') or f"Run {runNumber}"
        runType = runData.get('runType', 'generic')
        timebase = runData.get('timebase', 'canonical')
        if timebase not in VALID_TIMEBASES:
            timebase = 'canonical'
        
        # Separate core fields from manifest data
        coreKeys = {'runNumber', 'runName', 'runType', 'timebase', 'startTimeSec', 'stopTimeSec', 'analystNotes', 'schemaVersion'}
        extraData = {k: v for k, v in runData.items() if k not in coreKeys}
        
        run = Run(
            schemaVersion=RUN_SCHEMA_VERSION,
            runNumber=runNumber,
            runName=runName,
            runType=runType,
            timebase=timebase,
            startTimeSec=runData.get('startTimeSec', 0),
            stopTimeSec=runData.get('stopTimeSec', 0),
            analystNotes=runData.get('analystNotes', ''),
            data=extraData
        )
        
        # Create folder and save
        folderPath = self._getRunFolderPath(username, runNumber, run.runName)
        folderPath.mkdir(parents=True, exist_ok=True)
        
        runJsonPath = folderPath / 'run.json'
        with open(runJsonPath, 'w') as f:
            json.dump(run.toDict(), f, indent=2)
        
        self.log.info(f"[RunStore] Created run {runNumber} for {username}")
        return run
    
    def updateRun(self, username: str, runNumber: int, updateData: Dict[str, Any]) -> Optional[Run]:
        """
        Update a run (merge and overwrite run.json).
        
        If runName changes, folder is renamed (delete-then-rename on conflict).
        Last write wins. Schema-agnostic: merges all fields.
        """
        folder = self._findRunFolder(username, runNumber)
        if not folder:
            return None
        
        # Load existing run data
        runJsonPath = folder / 'run.json'
        try:
            with open(runJsonPath, 'r') as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing = {}
        
        oldRunName = existing.get('runName', '')
        
        # Merge: existing + updateData (updateData wins on conflict)
        # Preserve runNumber and schemaVersion
        merged = dict(existing)
        merged.update(updateData)
        merged['schemaVersion'] = RUN_SCHEMA_VERSION
        merged['runNumber'] = runNumber
        
        run = Run.fromDict(merged)
        
        # Validate core fields
        error = run.validate()
        if error:
            self.log.warning(f"[RunStore] Validation failed: {error}")
            return None
        
        newRunName = run.runName
        
        # Check if folder rename is needed
        if newRunName != oldRunName:
            newFolderPath = self._getRunFolderPath(username, runNumber, newRunName)
            
            if newFolderPath != folder:
                # Delete destination if exists (delete-then-rename)
                if newFolderPath.exists():
                    self.log.info(f"[RunStore] Deleting existing folder for rename: {newFolderPath}")
                    shutil.rmtree(newFolderPath)
                
                # Rename folder
                folder.rename(newFolderPath)
                folder = newFolderPath
                self.log.info(f"[RunStore] Renamed run folder to: {folder.name}")
        
        # Save run.json
        runJsonPath = folder / 'run.json'
        with open(runJsonPath, 'w') as f:
            json.dump(run.toDict(), f, indent=2)
        
        self.log.info(f"[RunStore] Updated run {runNumber} for {username}")
        return run
    
    def deleteRun(self, username: str, runNumber: int) -> bool:
        """Delete a run folder entirely."""
        folder = self._findRunFolder(username, runNumber)
        if not folder:
            return False
        
        try:
            shutil.rmtree(folder)
            self.log.info(f"[RunStore] Deleted run {runNumber} for {username}")
            return True
        except IOError as e:
            self.log.error(f"[RunStore] Failed to delete run {runNumber}: {e}")
            return False
    
    # =========================================================================
    # Bundle Operations
    # =========================================================================
    
    def getBundlePath(self, username: str, runNumber: int) -> Optional[Path]:
        """Get path to bundle.zip for a run (may not exist)."""
        folder = self._findRunFolder(username, runNumber)
        if not folder:
            return None
        return folder / 'bundle.zip'
    
    def setBundlePath(self, username: str, runNumber: int) -> Optional[Path]:
        """
        Get path where bundle.zip should be written.
        
        Returns the path (file may not exist yet).
        Caller is responsible for writing the zip.
        """
        folder = self._findRunFolder(username, runNumber)
        if not folder:
            return None
        return folder / 'bundle.zip'
    
    # =========================================================================
    # User Settings (run defaults)
    # =========================================================================
    
    def getUserRunSettings(self, username: str) -> Dict[str, Any]:
        """Get user's run settings (default runType, last runName, etc.)."""
        settingsPath = self._getUserRunsPath(username) / 'settings.json'
        if not settingsPath.exists():
            return {}
        
        try:
            with open(settingsPath, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    
    def setUserRunSettings(self, username: str, settings: Dict[str, Any]) -> None:
        """Save user's run settings."""
        runsPath = self._getUserRunsPath(username)
        runsPath.mkdir(parents=True, exist_ok=True)
        
        settingsPath = runsPath / 'settings.json'
        with open(settingsPath, 'w') as f:
            json.dump(settings, f, indent=2)
