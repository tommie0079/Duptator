"""
Data models for Docker Package Updater.
"""

from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class PackageManager(Enum):
    PIP = "pip"
    NPM = "npm"
    COMPOSER = "composer"
    GO = "go"
    BUNDLER = "bundler"
    CARGO = "cargo"
    MAVEN = "maven"
    GRADLE = "gradle"
    NUGET = "nuget"
    DOCKER = "docker"


STALE_MONTHS = 6


@dataclass
class Package:
    name: str
    current_version: str
    latest_version: str
    package_manager: str
    last_updated: Optional[str] = None  # ISO date string e.g. "2024-06-15"
    version_prefix: str = ""  # ^, ~, or "" (exact)

    @property
    def is_outdated(self) -> bool:
        if self.latest_version == "unknown" or self.current_version == self.latest_version:
            return False
        # Never flag as outdated if it's a major version bump
        cur_parts = self.current_version.split('.')
        new_parts = self.latest_version.split('.')
        if len(cur_parts) >= 1 and len(new_parts) >= 1 and cur_parts[0] != new_parts[0]:
            return False
        # ~ prefix: also skip minor bumps
        if self.version_prefix == '~' and len(cur_parts) > 1 and len(new_parts) > 1 and cur_parts[1] != new_parts[1]:
            return False
        return True

    @property
    def is_stale(self) -> bool:
        """Package hasn't been updated in STALE_MONTHS months."""
        if not self.last_updated:
            return False
        try:
            updated = datetime.strptime(self.last_updated[:10], "%Y-%m-%d")
            cutoff = datetime.now() - timedelta(days=STALE_MONTHS * 30)
            return updated < cutoff
        except (ValueError, TypeError):
            return False

    def to_dict(self):
        return {
            "name": self.name,
            "current_version": self.current_version,
            "latest_version": self.latest_version,
            "package_manager": self.package_manager,
            "is_outdated": self.is_outdated,
            "last_updated": self.last_updated,
            "is_stale": self.is_stale
        }


@dataclass
class Project:
    name: str
    path: str
    package_manager: Optional[str] = None
    dependency_file: Optional[str] = None
    docker_compose_file: Optional[str] = None
    packages: List[Package] = field(default_factory=list)
    status: str = "unknown"
    last_scan: Optional[str] = None

    @property
    def outdated_count(self) -> int:
        return len([p for p in self.packages if p.is_outdated])

    @property
    def stale_count(self) -> int:
        return len([p for p in self.packages if p.is_stale])

    def to_dict(self):
        return {
            "name": self.name,
            "path": self.path,
            "package_manager": self.package_manager,
            "dependency_file": self.dependency_file,
            "docker_compose_file": self.docker_compose_file,
            "packages": [p.to_dict() for p in self.packages],
            "outdated_count": self.outdated_count,
            "stale_count": self.stale_count,
            "total_packages": len(self.packages),
            "status": self.status,
            "last_scan": self.last_scan
        }
