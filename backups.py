"""
Backup creation, listing, and rollback.
"""

import json
import shutil
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from models import Project
from config import BACKUP_DIR

logger = logging.getLogger(__name__)

backup_dir = Path(BACKUP_DIR)


def create_backup(project: Project) -> Optional[str]:
    """Create a backup of the dependency file."""
    if not project.dependency_file:
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_subdir = backup_dir / project.name
    backup_subdir.mkdir(parents=True, exist_ok=True)

    dep_file = Path(project.dependency_file)
    backup_file = backup_subdir / f"{dep_file.name}.{timestamp}.bak"
    shutil.copy2(project.dependency_file, backup_file)

    metadata = {
        "project": project.name,
        "timestamp": timestamp,
        "package_manager": project.package_manager,
        "project_path": project.path,
        "dependency_file": Path(project.dependency_file).name,
        "original_file": project.dependency_file,
        "backup_file": str(backup_file),
        "packages": [p.to_dict() for p in project.packages]
    }

    metadata_file = backup_subdir / f"metadata.{timestamp}.json"
    with open(metadata_file, 'w') as f:
        json.dump(metadata, f, indent=2)

    return str(backup_file)


def list_backups(project_name: Optional[str] = None) -> Dict[str, List[Dict]]:
    """List available backups."""
    backups = {}

    if not backup_dir.exists():
        return backups

    for project_dir in backup_dir.iterdir():
        if not project_dir.is_dir():
            continue

        if project_name and project_dir.name != project_name:
            continue

        project_backups = []
        for metadata_file in sorted(project_dir.glob("metadata.*.json"), reverse=True):
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                project_backups.append(metadata)
            except (json.JSONDecodeError, IOError):
                continue

        if project_backups:
            backups[project_dir.name] = project_backups

    return backups


def rollback_project(project_name: str, timestamp: Optional[str] = None) -> bool:
    """Rollback a project to a previous state."""
    proj_backup_dir = backup_dir / project_name

    if not proj_backup_dir.exists():
        return False

    metadata_files = sorted(proj_backup_dir.glob("metadata.*.json"), reverse=True)
    if not metadata_files:
        return False

    metadata_file = None
    if timestamp:
        for mf in metadata_files:
            if timestamp in mf.name:
                metadata_file = mf
                break
    else:
        metadata_file = metadata_files[0]

    if not metadata_file:
        return False

    try:
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False

    bak_file = Path(metadata['backup_file'])
    original_file = Path(metadata['original_file'])

    if not bak_file.exists():
        return False

    shutil.copy2(bak_file, original_file)
    return True
