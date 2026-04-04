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

# Known compromised package versions (supply chain attacks)
KNOWN_MALWARE = {
    # Mar 2026 — RAT via plain-crypto-js (account compromise)
    "axios": {"1.14.1", "0.30.4"},
    "plain-crypto-js": {"4.2.0", "4.2.1"},
    # Sep 2025 — Qix account phished, crypto address swapper
    "chalk": {"5.6.1"},
    "debug": {"4.4.2"},
    "ansi-regex": {"6.2.1"},
    "ansi-styles": {"6.2.2"},
    "strip-ansi": {"7.1.1"},
    "wrap-ansi": {"9.0.1"},
    "slice-ansi": {"7.1.1"},
    "supports-color": {"10.2.1"},
    "supports-hyperlinks": {"4.1.1"},
    "chalk-template": {"1.1.1"},
    "color-convert": {"3.1.1"},
    "color-name": {"2.0.1"},
    "color-string": {"2.1.1"},
    "error-ex": {"1.3.3"},
    "has-ansi": {"6.0.1"},
    "is-arrayish": {"0.3.3"},
    "simple-swizzle": {"0.2.3"},
    "backslash": {"0.2.1"},
    # Mar 2022 — node-ipc protestware (file wiper for RU/BY IPs)
    "node-ipc": {"10.1.1", "10.1.2", "10.1.3"},
    # Jan 2022 — maintainer sabotage (infinite loop / repo wipe)
    "colors": {"1.4.1", "1.4.2", "1.4.44-liberty-2"},
    "faker": {"6.6.6"},
    # Nov 2021 — account hijack (credential stealer)
    "coa": {"2.0.3", "2.0.4", "2.1.1", "2.1.3", "3.0.1", "3.1.3"},
    "rc": {"1.2.9", "1.3.9", "2.3.9"},
    # Oct 2021 — ua-parser-js hijack (cryptominer + password stealer)
    "ua-parser-js": {"0.7.29", "0.8.0", "1.0.0"},
    # Nov 2018 — event-stream bitcoin stealer
    "event-stream": {"3.3.6"},
    "flatmap-stream": {"0.1.1"},
}


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
    def is_vulnerable(self) -> bool:
        """Package version is a known compromised release."""
        versions = KNOWN_MALWARE.get(self.name.lower())
        return self.current_version in versions if versions else False

    @property
    def vulnerability_note(self) -> Optional[str]:
        if self.is_vulnerable:
            return f"{self.name}@{self.current_version} is a known malware release — update immediately"
        return None

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
            "is_stale": self.is_stale,
            "is_vulnerable": self.is_vulnerable,
            "vulnerability_note": self.vulnerability_note
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

    @property
    def vulnerable_count(self) -> int:
        return len([p for p in self.packages if p.is_vulnerable])

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
            "vulnerable_count": self.vulnerable_count,
            "total_packages": len(self.packages),
            "status": self.status,
            "last_scan": self.last_scan
        }
