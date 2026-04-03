#!/usr/bin/env python3
"""
Docker Package Updater - Web Interface
=======================================
A Flask web app to scan, update, and rollback packages across Docker projects.
"""

import os
import json
import shutil
import subprocess
import re
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
from flask import Flask, render_template, jsonify, request, send_from_directory

app = Flask(__name__)

# Enable debug logging
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Configuration
CONFIG_FILE = os.environ.get('CONFIG_FILE', '/app/config.json')
DEFAULT_PROJECTS_PATH = os.environ.get('PROJECTS_PATH', '/projects')
BACKUP_DIR = os.environ.get('BACKUP_DIR', '/app/data/backups')


def load_config() -> dict:
    """Load configuration from file."""
    default_config = {
        "projects_path": DEFAULT_PROJECTS_PATH,
        "scan_timeout": 30,
        "auto_backup": True
    }
    
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                saved_config = json.load(f)
                default_config.update(saved_config)
    except Exception as e:
        logger.warning(f"Failed to load config: {e}")
    
    return default_config


def save_config(config: dict) -> bool:
    """Save configuration to file."""
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return False


# Load initial config
app_config = load_config()


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
            from datetime import timedelta
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


class DockerPackageUpdater:
    """Main class for managing Docker project packages."""
    
    def __init__(self, workspace_path: str):
        self.workspace_path = Path(workspace_path)
        self.backup_dir = Path(BACKUP_DIR)
        self.projects: List[Project] = []
    
    def discover_projects(self) -> List[Project]:
        """Discover all Docker projects."""
        projects = []
        
        for pattern in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
            for compose_file in self.workspace_path.rglob(pattern):
                # Skip nested/vendor directories
                skip_dirs = ["node_modules", "vendor", ".git", ".package-backups", "logging-proxy"]
                if any(skip in str(compose_file) for skip in skip_dirs):
                    continue
                
                project_path = compose_file.parent
                project_name = project_path.name
                
                # Skip duplicates
                if any(p.path == str(project_path) for p in projects):
                    continue
                
                project = Project(
                    name=project_name,
                    path=str(project_path),
                    docker_compose_file=str(compose_file)
                )
                
                # Detect package manager
                if (project_path / "requirements.txt").exists():
                    project.package_manager = PackageManager.PIP.value
                    project.dependency_file = str(project_path / "requirements.txt")
                elif (project_path / "package.json").exists():
                    project.package_manager = PackageManager.NPM.value
                    project.dependency_file = str(project_path / "package.json")
                elif (project_path / "composer.json").exists():
                    project.package_manager = PackageManager.COMPOSER.value
                    project.dependency_file = str(project_path / "composer.json")
                elif (project_path / "go.mod").exists():
                    project.package_manager = PackageManager.GO.value
                    project.dependency_file = str(project_path / "go.mod")
                elif (project_path / "Gemfile").exists():
                    project.package_manager = PackageManager.BUNDLER.value
                    project.dependency_file = str(project_path / "Gemfile")
                elif (project_path / "Cargo.toml").exists():
                    project.package_manager = PackageManager.CARGO.value
                    project.dependency_file = str(project_path / "Cargo.toml")
                elif (project_path / "pom.xml").exists():
                    project.package_manager = PackageManager.MAVEN.value
                    project.dependency_file = str(project_path / "pom.xml")
                elif (project_path / "build.gradle").exists():
                    project.package_manager = PackageManager.GRADLE.value
                    project.dependency_file = str(project_path / "build.gradle")
                elif list(project_path.glob("*.csproj")):
                    project.package_manager = PackageManager.NUGET.value
                    project.dependency_file = str(list(project_path.glob("*.csproj"))[0])
                
                # If no package manager detected, check for docker images
                if project.package_manager is None:
                    project.package_manager = PackageManager.DOCKER.value
                    project.dependency_file = str(compose_file)
                
                projects.append(project)
        
        self.projects = sorted(projects, key=lambda p: (p.package_manager is None, p.name.lower()))
        return self.projects
    
    def check_pip_outdated(self, project: Project) -> List[Package]:
        """Check for outdated pip packages."""
        packages = []
        
        if not project.dependency_file:
            logger.warning(f"No dependency file for {project.name}")
            return packages
        
        try:
            with open(project.dependency_file, 'r') as f:
                lines = f.readlines()
            logger.info(f"Read {len(lines)} lines from {project.dependency_file}")
        except IOError as e:
            logger.error(f"Failed to read {project.dependency_file}: {e}")
            return packages
        
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('-'):
                continue
            
            # Parse package name and version - handle various formats
            # flask==2.0.0, flask>=2.0, flask~=2.0, flask, flask[async]>=2.0
            match = re.match(r'^([a-zA-Z0-9_-]+)(?:\[.*\])?(?:([=<>!~]+)(.+))?$', line)
            if match:
                name = match.group(1)
                current = match.group(3).strip() if match.group(3) else "any"
                
                # Get latest version from PyPI
                latest, last_updated = self._get_pip_latest(name)
                logger.debug(f"Package {name}: current={current}, latest={latest}")
                
                packages.append(Package(
                    name=name,
                    current_version=current,
                    latest_version=latest,
                    package_manager=PackageManager.PIP.value,
                    last_updated=last_updated
                ))
            else:
                logger.warning(f"Could not parse line: {line}")
        
        logger.info(f"Found {len(packages)} packages in {project.name}")
        return packages
    
    def _get_pip_latest(self, package_name: str) -> tuple:
        """Get latest version and release date from PyPI."""
        try:
            url = f"https://pypi.org/pypi/{package_name}/json"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                version = data.get('info', {}).get('version', 'unknown')
                last_updated = None
                releases = data.get('releases', {}).get(version, [])
                if releases:
                    last_updated = releases[0].get('upload_time', '')[:10]
                return version, last_updated
        except urllib.error.HTTPError as e:
            logger.warning(f"PyPI HTTP error for {package_name}: {e.code}")
            return "unknown", None
        except Exception as e:
            logger.warning(f"Failed to get latest version for {package_name}: {e}")
            return "unknown", None
    
    def check_npm_outdated(self, project: Project) -> List[Package]:
        """Check for outdated npm packages."""
        packages = []
        
        if not project.dependency_file:
            logger.warning(f"No dependency file for {project.name}")
            return packages
        
        try:
            with open(project.dependency_file, 'r') as f:
                pkg_json = json.load(f)
            logger.info(f"Loaded package.json for {project.name}")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to read package.json: {e}")
            return packages
        
        all_deps = {}
        all_deps.update(pkg_json.get('dependencies', {}))
        all_deps.update(pkg_json.get('devDependencies', {}))
        
        for name, version in all_deps.items():
            current = version.lstrip('^~>=<')
            prefix = '^' if version.startswith('^') else ('~' if version.startswith('~') else '')
            latest, last_updated = self._get_npm_latest(name)
            logger.debug(f"NPM Package {name}: current={current}, latest={latest}")
            
            packages.append(Package(
                name=name,
                current_version=current,
                latest_version=latest,
                package_manager=PackageManager.NPM.value,
                last_updated=last_updated,
                version_prefix=prefix
            ))
        
        logger.info(f"Found {len(packages)} npm packages in {project.name}")
        return packages
    
    def _get_npm_latest(self, package_name: str) -> tuple:
        """Get latest version and publish date from npm registry."""
        try:
            url = f"https://registry.npmjs.org/{package_name}"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                version = data.get('dist-tags', {}).get('latest', 'unknown')
                last_updated = None
                time_map = data.get('time', {})
                if version in time_map:
                    last_updated = time_map[version][:10]
                return version, last_updated
        except urllib.error.HTTPError as e:
            logger.warning(f"npm registry HTTP error for {package_name}: {e.code}")
            return "unknown", None
        except Exception as e:
            logger.warning(f"Failed to get npm version for {package_name}: {e}")
            return "unknown", None
    
    def check_composer_outdated(self, project: Project) -> List[Package]:
        """Check for outdated composer packages."""
        packages = []
        
        if not project.dependency_file:
            logger.warning(f"No dependency file for {project.name}")
            return packages
        
        try:
            with open(project.dependency_file, 'r') as f:
                composer_json = json.load(f)
            logger.info(f"Loaded composer.json for {project.name}")
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to read composer.json: {e}")
            return packages
        
        all_deps = {}
        all_deps.update(composer_json.get('require', {}))
        all_deps.update(composer_json.get('require-dev', {}))
        
        for name, version in all_deps.items():
            # Skip php version and extensions
            if name == 'php' or name.startswith('ext-'):
                continue
            
            current = version.lstrip('^~>=<')
            prefix = '^' if version.startswith('^') else ('~' if version.startswith('~') else '')
            latest, last_updated = self._get_composer_latest(name)
            logger.debug(f"Composer Package {name}: current={current}, latest={latest}")
            
            packages.append(Package(
                name=name,
                current_version=current,
                latest_version=latest,
                package_manager=PackageManager.COMPOSER.value,
                last_updated=last_updated,
                version_prefix=prefix
            ))
        
        logger.info(f"Found {len(packages)} composer packages in {project.name}")
        return packages
    
    def _get_composer_latest(self, package_name: str) -> tuple:
        """Get latest version and date from Packagist API."""
        try:
            url = f"https://repo.packagist.org/p2/{package_name}.json"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                packages = data.get('packages', {}).get(package_name, [])
                if packages:
                    version = packages[0].get('version', 'unknown').lstrip('v')
                    last_updated = None
                    time_str = packages[0].get('time', '')
                    if time_str:
                        last_updated = time_str[:10]
                    return version, last_updated
        except urllib.error.HTTPError as e:
            logger.warning(f"Packagist HTTP error for {package_name}: {e.code}")
        except Exception as e:
            logger.warning(f"Failed to get composer version for {package_name}: {e}")
        return "unknown", None
    
    # ---- Go modules ----
    
    def check_go_outdated(self, project: Project) -> List[Package]:
        """Check for outdated Go modules."""
        packages = []
        if not project.dependency_file:
            return packages
        try:
            with open(project.dependency_file, 'r') as f:
                content = f.read()
        except IOError as e:
            logger.error(f"Failed to read go.mod: {e}")
            return packages
        
        # Parse require block
        in_require = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith('require ('):
                in_require = True
                continue
            if stripped == ')':
                in_require = False
                continue
            if in_require or stripped.startswith('require '):
                dep = stripped.replace('require ', '').strip()
                # skip indirect
                if '// indirect' in dep:
                    dep = dep.split('//')[0].strip()
                parts = dep.split()
                if len(parts) >= 2:
                    name, version = parts[0], parts[1].lstrip('v')
                    latest, last_updated = self._get_go_latest(name)
                    packages.append(Package(name=name, current_version=version, latest_version=latest, package_manager=PackageManager.GO.value, last_updated=last_updated))
        
        logger.info(f"Found {len(packages)} Go modules in {project.name}")
        return packages
    
    def _get_go_latest(self, module_name: str) -> tuple:
        """Get latest version and date from Go proxy."""
        try:
            url = f"https://proxy.golang.org/{module_name}/@latest"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                version = data.get('Version', 'unknown').lstrip('v')
                last_updated = None
                time_str = data.get('Time', '')
                if time_str:
                    last_updated = time_str[:10]
                return version, last_updated
        except Exception as e:
            logger.warning(f"Go proxy error for {module_name}: {e}")
            return "unknown", None
    
    # ---- Ruby Bundler ----
    
    def check_bundler_outdated(self, project: Project) -> List[Package]:
        """Check for outdated Ruby gems."""
        packages = []
        if not project.dependency_file:
            return packages
        try:
            with open(project.dependency_file, 'r') as f:
                lines = f.readlines()
        except IOError as e:
            logger.error(f"Failed to read Gemfile: {e}")
            return packages
        
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            # Match: gem 'name', '~> 1.0' or gem "name", ">= 2.0"
            match = re.match(r"gem\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", stripped)
            if match:
                name = match.group(1)
                version_str = match.group(2) or "any"
                current = re.sub(r'[~>=<!\s]', '', version_str)
                latest, last_updated = self._get_gem_latest(name)
                packages.append(Package(name=name, current_version=current, latest_version=latest, package_manager=PackageManager.BUNDLER.value, last_updated=last_updated))
        
        logger.info(f"Found {len(packages)} gems in {project.name}")
        return packages
    
    def _get_gem_latest(self, gem_name: str) -> tuple:
        """Get latest version and date from RubyGems API."""
        try:
            url = f"https://rubygems.org/api/v1/gems/{gem_name}.json"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                version = data.get('version', 'unknown')
                last_updated = None
                time_str = data.get('version_created_at', '')
                if time_str:
                    last_updated = time_str[:10]
                return version, last_updated
        except Exception as e:
            logger.warning(f"RubyGems error for {gem_name}: {e}")
            return "unknown", None
    
    # ---- Rust Cargo ----
    
    def check_cargo_outdated(self, project: Project) -> List[Package]:
        """Check for outdated Rust crates."""
        packages = []
        if not project.dependency_file:
            return packages
        try:
            with open(project.dependency_file, 'r') as f:
                content = f.read()
        except IOError as e:
            logger.error(f"Failed to read Cargo.toml: {e}")
            return packages
        
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if re.match(r'\[(.*dependencies.*)\]', stripped):
                in_deps = True
                continue
            if stripped.startswith('[') and in_deps:
                in_deps = False
                continue
            if in_deps and '=' in stripped and not stripped.startswith('#'):
                # name = "1.0" or name = { version = "1.0", ... }
                match = re.match(r'^([a-zA-Z0-9_-]+)\s*=\s*"([^"]+)"', stripped)
                if match:
                    name, current = match.group(1), match.group(2).lstrip('^~>=<')
                else:
                    match = re.match(r'^([a-zA-Z0-9_-]+)\s*=\s*\{.*version\s*=\s*"([^"]+)"', stripped)
                    if match:
                        name, current = match.group(1), match.group(2).lstrip('^~>=<')
                    else:
                        continue
                latest, last_updated = self._get_crate_latest(name)
                packages.append(Package(name=name, current_version=current, latest_version=latest, package_manager=PackageManager.CARGO.value, last_updated=last_updated))
        
        logger.info(f"Found {len(packages)} crates in {project.name}")
        return packages
    
    def _get_crate_latest(self, crate_name: str) -> tuple:
        """Get latest version and date from crates.io API."""
        try:
            url = f"https://crates.io/api/v1/crates/{crate_name}"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                version = data.get('crate', {}).get('max_stable_version', 'unknown')
                last_updated = None
                time_str = data.get('crate', {}).get('updated_at', '')
                if time_str:
                    last_updated = time_str[:10]
                return version, last_updated
        except Exception as e:
            logger.warning(f"crates.io error for {crate_name}: {e}")
            return "unknown", None
    
    # ---- Maven ----
    
    def check_maven_outdated(self, project: Project) -> List[Package]:
        """Check for outdated Maven dependencies."""
        packages = []
        if not project.dependency_file:
            return packages
        try:
            with open(project.dependency_file, 'r') as f:
                content = f.read()
        except IOError as e:
            logger.error(f"Failed to read pom.xml: {e}")
            return packages
        
        # Simple XML parsing without external deps
        dep_pattern = re.compile(
            r'<dependency>\s*'
            r'<groupId>([^<]+)</groupId>\s*'
            r'<artifactId>([^<]+)</artifactId>\s*'
            r'(?:<version>([^<$]+)</version>)?',
            re.DOTALL
        )
        for match in dep_pattern.finditer(content):
            group_id, artifact_id, version = match.group(1), match.group(2), match.group(3)
            if not version or version.startswith('$'):
                continue
            name = f"{group_id}:{artifact_id}"
            latest, last_updated = self._get_maven_latest(group_id, artifact_id)
            packages.append(Package(name=name, current_version=version, latest_version=latest, package_manager=PackageManager.MAVEN.value, last_updated=last_updated))
        
        logger.info(f"Found {len(packages)} Maven deps in {project.name}")
        return packages
    
    def _get_maven_latest(self, group_id: str, artifact_id: str) -> tuple:
        """Get latest version and date from Maven Central."""
        try:
            url = f"https://search.maven.org/solrsearch/select?q=g:{group_id}+AND+a:{artifact_id}&rows=1&wt=json"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                docs = data.get('response', {}).get('docs', [])
                if docs:
                    version = docs[0].get('latestVersion', 'unknown')
                    last_updated = None
                    ts = docs[0].get('timestamp')
                    if ts:
                        last_updated = datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d')
                    return version, last_updated
        except Exception as e:
            logger.warning(f"Maven Central error for {group_id}:{artifact_id}: {e}")
        return "unknown", None
    
    # ---- Gradle ----
    
    def check_gradle_outdated(self, project: Project) -> List[Package]:
        """Check for outdated Gradle dependencies."""
        packages = []
        if not project.dependency_file:
            return packages
        try:
            with open(project.dependency_file, 'r') as f:
                content = f.read()
        except IOError as e:
            logger.error(f"Failed to read build.gradle: {e}")
            return packages
        
        # Match: implementation 'group:artifact:version' or "group:artifact:version"
        dep_pattern = re.compile(
            r"(?:implementation|api|compile|testImplementation|runtimeOnly|compileOnly)\s+['\"]([^:]+):([^:]+):([^'\"]+)['\"]"
        )
        for match in dep_pattern.finditer(content):
            group_id, artifact_id, version = match.group(1), match.group(2), match.group(3)
            if version.startswith('$'):
                continue
            name = f"{group_id}:{artifact_id}"
            latest, last_updated = self._get_maven_latest(group_id, artifact_id)
            packages.append(Package(name=name, current_version=version, latest_version=latest, package_manager=PackageManager.GRADLE.value, last_updated=last_updated))
        
        logger.info(f"Found {len(packages)} Gradle deps in {project.name}")
        return packages
    
    # ---- NuGet (.NET) ----
    
    def check_nuget_outdated(self, project: Project) -> List[Package]:
        """Check for outdated NuGet packages."""
        packages = []
        if not project.dependency_file:
            return packages
        try:
            with open(project.dependency_file, 'r') as f:
                content = f.read()
        except IOError as e:
            logger.error(f"Failed to read .csproj: {e}")
            return packages
        
        # Match: <PackageReference Include="Name" Version="1.0.0" />
        ref_pattern = re.compile(
            r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"',
            re.IGNORECASE
        )
        for match in ref_pattern.finditer(content):
            name, version = match.group(1), match.group(2)
            latest, last_updated = self._get_nuget_latest(name)
            packages.append(Package(name=name, current_version=version, latest_version=latest, package_manager=PackageManager.NUGET.value, last_updated=last_updated))
        
        logger.info(f"Found {len(packages)} NuGet packages in {project.name}")
        return packages
    
    def _get_nuget_latest(self, package_name: str) -> tuple:
        """Get latest version and date from NuGet API."""
        try:
            url = f"https://api.nuget.org/v3-flatcontainer/{package_name.lower()}/index.json"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                versions = data.get('versions', [])
                if versions:
                    stable = [v for v in versions if '-' not in v]
                    version = stable[-1] if stable else versions[-1]
                    # Try to get publish date from registration API
                    last_updated = self._get_nuget_date(package_name, version)
                    return version, last_updated
        except Exception as e:
            logger.warning(f"NuGet error for {package_name}: {e}")
        return "unknown", None
    
    def _get_nuget_date(self, package_name: str, version: str) -> Optional[str]:
        """Get publish date for a specific NuGet package version."""
        try:
            url = f"https://api.nuget.org/v3/registration5-gz-semver2/{package_name.lower()}/index.json"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                items = data.get('items', [])
                if items:
                    # Last page has latest versions
                    last_page = items[-1]
                    page_items = last_page.get('items', [])
                    for item in reversed(page_items):
                        cat = item.get('catalogEntry', {})
                        if cat.get('version') == version:
                            published = cat.get('published', '')
                            if published:
                                return published[:10]
        except Exception:
            pass
        return None
    
    def check_docker_outdated(self, project: Project) -> List[Package]:
        """Check for outdated Docker images in docker-compose file."""
        packages = []
        compose_file = project.docker_compose_file
        if not compose_file:
            return packages
        try:
            with open(compose_file, 'r') as f:
                content = f.read()
        except IOError as e:
            logger.error(f"Failed to read compose file: {e}")
            return packages

        # Match image: lines like  image: nginx:1.25.3  or  image: mariadb:latest
        image_pattern = re.compile(r'^\s*image:\s*([^\s#]+)', re.MULTILINE)
        seen = set()
        for match in image_pattern.finditer(content):
            image_ref = match.group(1).strip().strip('"').strip("'")
            if image_ref in seen:
                continue
            seen.add(image_ref)

            # Parse image name and tag
            if ':' in image_ref and not image_ref.startswith('sha256'):
                parts = image_ref.rsplit(':', 1)
                image_name, current_tag = parts[0], parts[1]
            else:
                image_name = image_ref
                current_tag = 'latest'

            latest_tag, last_updated = self._get_docker_latest(image_name, current_tag)

            packages.append(Package(
                name=image_ref,
                current_version=current_tag,
                latest_version=latest_tag,
                package_manager=PackageManager.DOCKER.value,
                last_updated=last_updated
            ))

        logger.info(f"Found {len(packages)} Docker images in {project.name}")
        return packages

    def _get_docker_latest(self, image_name: str, current_tag: str) -> tuple:
        """Get latest version tag and push date from Docker Hub."""
        try:
            # Determine the Docker Hub API path
            if '/' not in image_name:
                # Official image (e.g. nginx, mariadb)
                api_path = f"library/{image_name}"
            elif '.' in image_name.split('/')[0]:
                # Third-party registry (ghcr.io, etc.) — skip
                logger.info(f"Skipping non-Docker Hub image: {image_name}")
                return current_tag, None
            else:
                api_path = image_name

            # For floating tags (latest, stable, lts, alpine, etc.), get the push date
            if not re.match(r'^v?\d+', current_tag):
                url = f"https://hub.docker.com/v2/repositories/{api_path}/tags/{current_tag}"
                req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
                with urllib.request.urlopen(req, timeout=10) as response:
                    data = json.loads(response.read().decode())
                    last_updated = data.get('last_updated', '')[:10] if data.get('last_updated') else None
                    return current_tag, last_updated

            # For versioned tags (e.g. 1.25.3), find the latest stable version
            url = f"https://hub.docker.com/v2/repositories/{api_path}/tags/?page_size=100&ordering=-last_updated"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode())
                results = data.get('results', [])

                # Extract the version pattern from the current tag
                # e.g. current_tag=1.25.3 → look for major.minor.patch pattern
                dot_count = current_tag.replace('v', '').count('.')
                prefix = 'v' if current_tag.startswith('v') else ''

                best_version = current_tag.lstrip('v')
                best_date = None

                for tag_info in results:
                    tag_name = tag_info.get('name', '')
                    raw = tag_name.lstrip('v')

                    # Must match same version format (same number of dots)
                    if raw.count('.') != dot_count:
                        continue
                    # Must be numeric segments only
                    if not all(part.isdigit() for part in raw.split('.')):
                        continue

                    # Compare as version tuples
                    try:
                        tag_tuple = tuple(int(x) for x in raw.split('.'))
                        best_tuple = tuple(int(x) for x in best_version.split('.'))
                        if tag_tuple > best_tuple:
                            best_version = raw
                            best_date = tag_info.get('last_updated', '')[:10] if tag_info.get('last_updated') else None
                    except ValueError:
                        continue

                if best_version == current_tag.lstrip('v'):
                    # Current is the latest, just get its date
                    for tag_info in results:
                        if tag_info.get('name') == current_tag:
                            best_date = tag_info.get('last_updated', '')[:10] if tag_info.get('last_updated') else None
                            break
                    return current_tag, best_date

                return f"{prefix}{best_version}", best_date

        except urllib.error.HTTPError as e:
            logger.warning(f"Docker Hub HTTP error for {image_name}: {e.code}")
        except Exception as e:
            logger.warning(f"Failed to check Docker Hub for {image_name}: {e}")
        return "unknown", None

    def scan_project(self, project: Project) -> Project:
        """Scan a single project for outdated packages."""
        logger.info(f"Scanning project: {project.name} (manager: {project.package_manager})")
        
        scan_map = {
            PackageManager.PIP.value: self.check_pip_outdated,
            PackageManager.NPM.value: self.check_npm_outdated,
            PackageManager.COMPOSER.value: self.check_composer_outdated,
            PackageManager.GO.value: self.check_go_outdated,
            PackageManager.BUNDLER.value: self.check_bundler_outdated,
            PackageManager.CARGO.value: self.check_cargo_outdated,
            PackageManager.MAVEN.value: self.check_maven_outdated,
            PackageManager.GRADLE.value: self.check_gradle_outdated,
            PackageManager.NUGET.value: self.check_nuget_outdated,
            PackageManager.DOCKER.value: self.check_docker_outdated,
        }
        
        checker = scan_map.get(project.package_manager)
        if checker:
            project.packages = checker(project)
        
        # For non-docker projects, also scan docker images from compose file
        if project.package_manager != PackageManager.DOCKER.value:
            docker_pkgs = self.check_docker_outdated(project)
            if docker_pkgs:
                project.packages.extend(docker_pkgs)
        
        project.status = "scanned"
        project.last_scan = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"Scan complete for {project.name}: {len(project.packages)} packages, {project.outdated_count} outdated")
        return project
    
    def create_backup(self, project: Project) -> Optional[str]:
        """Create a backup of the dependency file."""
        if not project.dependency_file:
            return None
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_subdir = self.backup_dir / project.name
        backup_subdir.mkdir(parents=True, exist_ok=True)
        
        dep_file = Path(project.dependency_file)
        backup_file = backup_subdir / f"{dep_file.name}.{timestamp}.bak"
        shutil.copy2(project.dependency_file, backup_file)
        
        # Save metadata
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
    
    def update_pip_packages(self, project: Project) -> bool:
        """Update pip packages."""
        if not project.dependency_file:
            return False
        
        with open(project.dependency_file, 'r') as f:
            lines = f.readlines()
        
        pkg_lookup = {p.name.lower(): p for p in project.packages if p.is_outdated}
        
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith('#') or stripped.startswith('-'):
                new_lines.append(line)
                continue
            
            match = re.match(r'^([a-zA-Z0-9_-]+)', stripped)
            if match:
                name = match.group(1).lower()
                if name in pkg_lookup:
                    pkg = pkg_lookup[name]
                    new_lines.append(f"{pkg.name}=={pkg.latest_version}\n")
                    continue
            new_lines.append(line)
        
        with open(project.dependency_file, 'w') as f:
            f.writelines(new_lines)
        
        return True
    
    def update_npm_packages(self, project: Project) -> bool:
        """Update npm packages."""
        if not project.dependency_file:
            return False
        
        try:
            with open(project.dependency_file, 'r') as f:
                pkg_json = json.load(f)
        except (json.JSONDecodeError, IOError):
            return False
        
        pkg_lookup = {p.name: p for p in project.packages if p.is_outdated}
        
        for dep_type in ['dependencies', 'devDependencies']:
            if dep_type in pkg_json:
                for name in pkg_json[dep_type]:
                    if name in pkg_lookup:
                        pkg = pkg_lookup[name]
                        current = pkg_json[dep_type][name]
                        prefix = '^' if current.startswith('^') else ('~' if current.startswith('~') else '')
                        # Respect semver: ^ allows minor/patch, ~ allows patch only
                        # No prefix (exact pin) also blocks major bumps
                        cur_ver = current.lstrip('^~')
                        new_ver = pkg.latest_version
                        cur_parts = cur_ver.split('.')
                        new_parts = new_ver.split('.')
                        if len(cur_parts) >= 1 and len(new_parts) >= 1:
                            if cur_parts[0] != new_parts[0]:
                                continue  # Never cross major versions
                            if prefix == '~' and (len(cur_parts) > 1 and len(new_parts) > 1 and cur_parts[1] != new_parts[1]):
                                continue  # ~ also blocks minor version bumps
                        pkg_json[dep_type][name] = f"{prefix}{pkg.latest_version}"
        
        with open(project.dependency_file, 'w') as f:
            json.dump(pkg_json, f, indent=2)
        
        return True
    
    def update_composer_packages(self, project: Project) -> bool:
        """Update composer packages."""
        if not project.dependency_file:
            return False
        try:
            with open(project.dependency_file, 'r') as f:
                composer_json = json.load(f)
        except (json.JSONDecodeError, IOError):
            return False
        
        pkg_lookup = {p.name: p for p in project.packages if p.is_outdated}
        for dep_type in ['require', 'require-dev']:
            if dep_type in composer_json:
                for name in composer_json[dep_type]:
                    if name in pkg_lookup:
                        pkg = pkg_lookup[name]
                        current = composer_json[dep_type][name]
                        prefix = '^' if current.startswith('^') else ('~' if current.startswith('~') else '')
                        # Never cross major versions regardless of prefix
                        cur_ver = current.lstrip('^~')
                        new_ver = pkg.latest_version
                        cur_parts = cur_ver.split('.')
                        new_parts = new_ver.split('.')
                        if len(cur_parts) >= 1 and len(new_parts) >= 1:
                            if cur_parts[0] != new_parts[0]:
                                continue
                            if prefix == '~' and (len(cur_parts) > 1 and len(new_parts) > 1 and cur_parts[1] != new_parts[1]):
                                continue
                        composer_json[dep_type][name] = f"{prefix}{pkg.latest_version}"
        
        with open(project.dependency_file, 'w') as f:
            json.dump(composer_json, f, indent=4)
        return True
    
    def update_go_packages(self, project: Project) -> bool:
        """Update go.mod packages."""
        if not project.dependency_file:
            return False
        with open(project.dependency_file, 'r') as f:
            content = f.read()
        
        pkg_lookup = {p.name: p for p in project.packages if p.is_outdated}
        for name, pkg in pkg_lookup.items():
            # Replace version in require lines
            pattern = re.compile(re.escape(name) + r'\s+v[\d.]+')
            content = pattern.sub(f"{name} v{pkg.latest_version}", content)
        
        with open(project.dependency_file, 'w') as f:
            f.write(content)
        return True
    
    def update_bundler_packages(self, project: Project) -> bool:
        """Update Gemfile packages."""
        if not project.dependency_file:
            return False
        with open(project.dependency_file, 'r') as f:
            lines = f.readlines()
        
        pkg_lookup = {p.name: p for p in project.packages if p.is_outdated}
        new_lines = []
        for line in lines:
            match = re.match(r"(gem\s+['\"])([^'\"]+)(['\"])\s*,\s*['\"][^'\"]*['\"]", line)
            if match and match.group(2) in pkg_lookup:
                pkg = pkg_lookup[match.group(2)]
                new_lines.append(f"{match.group(1)}{pkg.name}{match.group(3)}, '~> {pkg.latest_version}'\n")
            else:
                new_lines.append(line)
        
        with open(project.dependency_file, 'w') as f:
            f.writelines(new_lines)
        return True
    
    def update_cargo_packages(self, project: Project) -> bool:
        """Update Cargo.toml packages."""
        if not project.dependency_file:
            return False
        with open(project.dependency_file, 'r') as f:
            content = f.read()
        
        for pkg in project.packages:
            if not pkg.is_outdated:
                continue
            # Simple version: name = "1.0.0"
            pattern = re.compile(
                r'(' + re.escape(pkg.name) + r'\s*=\s*")' + r'[^"]+(")'
            )
            content = pattern.sub(f'\\g<1>{pkg.latest_version}\\g<2>', content)
            # Table version: name = { version = "1.0.0"
            pattern2 = re.compile(
                r'(' + re.escape(pkg.name) + r'\s*=\s*\{.*?version\s*=\s*")' + r'[^"]+(")',
                re.DOTALL
            )
            content = pattern2.sub(f'\\g<1>{pkg.latest_version}\\g<2>', content)
        
        with open(project.dependency_file, 'w') as f:
            f.write(content)
        return True
    
    def update_maven_packages(self, project: Project) -> bool:
        """Update pom.xml packages."""
        if not project.dependency_file:
            return False
        with open(project.dependency_file, 'r') as f:
            content = f.read()
        
        for pkg in project.packages:
            if not pkg.is_outdated:
                continue
            group_id, artifact_id = pkg.name.split(':', 1)
            # Replace version in dependency block
            pattern = re.compile(
                r'(<dependency>\s*'
                r'<groupId>' + re.escape(group_id) + r'</groupId>\s*'
                r'<artifactId>' + re.escape(artifact_id) + r'</artifactId>\s*'
                r'<version>)[^<]+(</version>)',
                re.DOTALL
            )
            content = pattern.sub(f'\\g<1>{pkg.latest_version}\\g<2>', content)
        
        with open(project.dependency_file, 'w') as f:
            f.write(content)
        return True
    
    def update_gradle_packages(self, project: Project) -> bool:
        """Update build.gradle packages."""
        if not project.dependency_file:
            return False
        with open(project.dependency_file, 'r') as f:
            content = f.read()
        
        for pkg in project.packages:
            if not pkg.is_outdated:
                continue
            group_id, artifact_id = pkg.name.split(':', 1)
            # Match both single and double quotes
            pattern = re.compile(
                r'([\'"])' + re.escape(group_id) + r':' + re.escape(artifact_id) + r':[^\'"]+([\'"])'
            )
            content = pattern.sub(
                f'\\g<1>{group_id}:{artifact_id}:{pkg.latest_version}\\g<2>', content
            )
        
        with open(project.dependency_file, 'w') as f:
            f.write(content)
        return True
    
    def update_nuget_packages(self, project: Project) -> bool:
        """Update .csproj packages."""
        if not project.dependency_file:
            return False
        with open(project.dependency_file, 'r') as f:
            content = f.read()
        
        for pkg in project.packages:
            if not pkg.is_outdated:
                continue
            pattern = re.compile(
                r'(<PackageReference\s+Include="' + re.escape(pkg.name) + r'"\s+Version=")[^"]+(">)',
                re.IGNORECASE
            )
            content = pattern.sub(f'\\g<1>{pkg.latest_version}\\g<2>', content)
        
        with open(project.dependency_file, 'w') as f:
            f.write(content)
        return True
    
    def update_docker_packages(self, project: Project) -> bool:
        """Update Docker image tags in docker-compose file."""
        compose_file = project.docker_compose_file
        if not compose_file:
            return False
        try:
            with open(compose_file, 'r') as f:
                content = f.read()
        except IOError:
            return False

        docker_pkgs = {p.name: p for p in project.packages
                       if p.package_manager == PackageManager.DOCKER.value and p.is_outdated}
        if not docker_pkgs:
            return True

        for old_ref, pkg in docker_pkgs.items():
            # old_ref is the full image:tag string (e.g. "nginx:1.25.3" or "mariadb:10.5")
            image_name = old_ref.rsplit(':', 1)[0] if ':' in old_ref else old_ref
            new_ref = f"{image_name}:{pkg.latest_version}"
            # Replace in image: lines only
            pattern = re.compile(
                r'(image:\s*)' + re.escape(old_ref) + r'(\s*(?:#.*)?$)',
                re.MULTILINE
            )
            content = pattern.sub(f'\\g<1>{new_ref}\\g<2>', content)

        with open(compose_file, 'w') as f:
            f.write(content)
        return True

    def _get_update_map(self):
        return {
            PackageManager.PIP.value: self.update_pip_packages,
            PackageManager.NPM.value: self.update_npm_packages,
            PackageManager.COMPOSER.value: self.update_composer_packages,
            PackageManager.GO.value: self.update_go_packages,
            PackageManager.BUNDLER.value: self.update_bundler_packages,
            PackageManager.CARGO.value: self.update_cargo_packages,
            PackageManager.MAVEN.value: self.update_maven_packages,
            PackageManager.GRADLE.value: self.update_gradle_packages,
            PackageManager.NUGET.value: self.update_nuget_packages,
            PackageManager.DOCKER.value: self.update_docker_packages,
        }

    def update_project(self, project: Project) -> bool:
        """Update a project's packages."""
        if not project.package_manager or not any(p.is_outdated for p in project.packages):
            return True
        
        self.create_backup(project)
        
        updater_fn = self._get_update_map().get(project.package_manager)
        result = updater_fn(project) if updater_fn else True
        
        # Also update docker images for non-docker projects
        if project.package_manager != PackageManager.DOCKER.value:
            docker_outdated = [p for p in project.packages if p.package_manager == PackageManager.DOCKER.value and p.is_outdated]
            if docker_outdated:
                self.update_docker_packages(project)
        
        return result

    def update_single_package(self, project: Project, package_name: str) -> bool:
        """Update a single package in a project."""
        pkg = next((p for p in project.packages if p.name == package_name and p.is_outdated), None)
        if not pkg:
            return False

        self.create_backup(project)

        # Temporarily scope to just the target package
        original_packages = project.packages
        project.packages = [pkg]

        # Use docker updater if this is a docker image package, else use project's manager
        if pkg.package_manager == PackageManager.DOCKER.value:
            updater_fn = self.update_docker_packages
        else:
            updater_fn = self._get_update_map().get(project.package_manager)
        result = updater_fn(project) if updater_fn else False

        # Restore full list and mark the updated package
        if result:
            for p in original_packages:
                if p.name == package_name:
                    p.current_version = p.latest_version
        project.packages = original_packages

        return result
    
    def list_backups(self, project_name: Optional[str] = None) -> Dict[str, List[Dict]]:
        """List available backups."""
        backups = {}
        
        if not self.backup_dir.exists():
            return backups
        
        for project_dir in self.backup_dir.iterdir():
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
    
    def rollback_project(self, project_name: str, timestamp: Optional[str] = None) -> bool:
        """Rollback a project to a previous state."""
        backup_dir = self.backup_dir / project_name
        
        if not backup_dir.exists():
            return False
        
        metadata_files = sorted(backup_dir.glob("metadata.*.json"), reverse=True)
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
        
        backup_file = Path(metadata['backup_file'])
        original_file = Path(metadata['original_file'])
        
        if not backup_file.exists():
            return False
        
        shutil.copy2(backup_file, original_file)
        return True
    
    def _has_build_directive(self, project: Project) -> bool:
        """Check if the docker-compose file uses 'build:' (vs only 'image:')."""
        try:
            with open(project.docker_compose_file, 'r') as f:
                content = f.read()
            return bool(re.search(r'^\s+build:', content, re.MULTILINE))
        except Exception:
            return False

    def _is_self(self, project: Project) -> bool:
        """Check if this project is Duptator itself."""
        try:
            hostname = os.environ.get('HOSTNAME', '')
            if not hostname:
                return False
            # List containers belonging to THIS project
            compose_file = str(project.docker_compose_file or "")
            if not compose_file:
                return False
            result = subprocess.run(
                ["docker", "compose", "-f", compose_file, "ps", "-q"],
                capture_output=True, text=True, timeout=10,
                cwd=str(project.path)
            )
            if result.returncode == 0:
                for cid in result.stdout.strip().splitlines():
                    if cid.startswith(hostname) or hostname.startswith(cid[:12]):
                        return True
        except Exception:
            pass
        return False

    def _self_rebuild(self, project: Project) -> Dict:
        """Rebuild Duptator itself using a detached helper container."""
        compose_dir = str(project.path)
        compose_file = str(project.docker_compose_file)
        uses_build = self._has_build_directive(project)

        if uses_build:
            build_step = "docker compose -f {cf} build --no-cache &&".format(cf=compose_file)
        else:
            build_step = "docker compose -f {cf} pull &&".format(cf=compose_file)

        script = (
            "sleep 2 && "
            "{build} "
            "docker compose -f {cf} down --remove-orphans; "
            "docker rm -f docker-updater 2>/dev/null; "
            "docker compose -f {cf} up -d"
        ).format(build=build_step, cf=compose_file)

        try:
            result = subprocess.run(
                [
                    "docker", "run", "-d", "--rm",
                    "--name", "duptator-rebuilder",
                    "-v", "/var/run/docker.sock:/var/run/docker.sock",
                    "-v", "{d}:{d}".format(d=compose_dir),
                    "-w", compose_dir,
                    "docker:cli", "sh", "-c", script
                ],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return {
                    "success": True,
                    "self_rebuild": True,
                    "message": "Self-rebuild initiated. Duptator will restart in ~30 seconds. The page will reload automatically.",
                    "log": "Spawned helper container for self-rebuild.\n" + result.stdout
                }
            else:
                return {"success": False, "message": "Failed to start rebuild helper", "log": result.stderr}
        except Exception as e:
            return {"success": False, "message": str(e), "log": ""}

    def rebuild_container(self, project: Project) -> Dict:
        """Rebuild a Docker container."""
        if not project.docker_compose_file:
            return {"success": False, "message": "No docker-compose file found", "log": ""}

        # Self-rebuild: delegate to a detached helper container
        if self._is_self(project):
            return self._self_rebuild(project)

        uses_build = self._has_build_directive(project)
        log_lines = []
        
        def run_step(cmd, args, timeout=600):
            """Run a command and capture output."""
            full_cmd = cmd + args
            log_lines.append(f"$ {' '.join(full_cmd)}\n")
            result = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=project.path
            )
            if result.stdout:
                log_lines.append(result.stdout)
            if result.stderr:
                log_lines.append(result.stderr)
            return result
        
        try:
            for cmd in [["docker", "compose"], ["docker-compose"]]:
                if uses_build:
                    log_lines.append(f"=== Building {project.name} (Dockerfile) ===\n")
                    build_result = run_step(cmd, ["-f", project.docker_compose_file, "build", "--no-cache"])
                    if build_result.returncode != 0:
                        if "is not a docker command" in (build_result.stderr or ""):
                            log_lines.clear()
                            continue
                        return {"success": False, "message": "Build failed", "log": "\n".join(log_lines)}
                else:
                    log_lines.append(f"=== Pulling latest images for {project.name} ===\n")
                    pull_result = run_step(cmd, ["-f", project.docker_compose_file, "pull"], timeout=300)
                    if pull_result.returncode != 0:
                        if "is not a docker command" in (pull_result.stderr or ""):
                            log_lines.clear()
                            continue
                        return {"success": False, "message": "Pull failed", "log": "\n".join(log_lines)}
                
                log_lines.append(f"\n=== Restarting {project.name} ===\n")
                down_result = run_step(cmd, ["-f", project.docker_compose_file, "down", "--remove-orphans"], timeout=120)
                # Force-remove any stale containers that survived 'down'
                ps_result = subprocess.run(
                    cmd + ["-f", project.docker_compose_file, "ps", "-a", "-q"],
                    capture_output=True, text=True, timeout=15, cwd=project.path
                )
                if ps_result.returncode == 0 and ps_result.stdout.strip():
                    for cid in ps_result.stdout.strip().splitlines():
                        run_step(["docker"], ["rm", "-f", cid], timeout=15)
                up_result = run_step(cmd, ["-f", project.docker_compose_file, "up", "-d"], timeout=120)
                if up_result.returncode == 0:
                    action = "rebuilt" if uses_build else "pulled & restarted"
                    log_lines.append(f"\n=== Done — {action} successfully ===\n")
                    return {"success": True, "message": f"Container {action} successfully", "log": "\n".join(log_lines)}
                else:
                    return {"success": False, "message": "Restart failed", "log": "\n".join(log_lines)}
            
            return {"success": False, "message": "Docker compose not available", "log": "No docker compose command found on this system."}
        except subprocess.TimeoutExpired:
            log_lines.append("\n=== TIMEOUT ===\n")
            return {"success": False, "message": "Operation timed out", "log": "\n".join(log_lines)}
        except Exception as e:
            log_lines.append(f"\n=== ERROR: {e} ===\n")
            return {"success": False, "message": str(e), "log": "\n".join(log_lines)}


# Initialize updater with config
updater = None

def get_updater():
    """Get or create the updater instance with current config."""
    global updater
    if updater is None or updater.workspace_path != Path(app_config['projects_path']):
        updater = DockerPackageUpdater(app_config['projects_path'])
    return updater


# Routes
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/config', methods=['GET'])
def get_config():
    """Get current configuration."""
    return jsonify({
        "projects_path": app_config.get('projects_path', DEFAULT_PROJECTS_PATH),
        "scan_timeout": app_config.get('scan_timeout', 30),
        "auto_backup": app_config.get('auto_backup', True)
    })


@app.route('/api/config', methods=['POST'])
def set_config():
    """Update configuration."""
    global updater
    
    data = request.json
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    # Validate projects path
    new_path = data.get('projects_path')
    if new_path:
        if not os.path.isdir(new_path):
            return jsonify({"error": f"Path does not exist: {new_path}"}), 400
        app_config['projects_path'] = new_path
        # Reset updater to use new path
        updater = None
    
    # Update other settings
    if 'scan_timeout' in data:
        app_config['scan_timeout'] = int(data['scan_timeout'])
    if 'auto_backup' in data:
        app_config['auto_backup'] = bool(data['auto_backup'])
    
    # Save config
    if save_config(app_config):
        logger.info(f"Config updated: {app_config}")
        return jsonify({"success": True, "config": app_config})
    else:
        return jsonify({"error": "Failed to save config"}), 500


@app.route('/api/browse', methods=['GET'])
def browse_directory():
    """Browse directories for path selection."""
    path = request.args.get('path', '/')
    
    try:
        if not os.path.isdir(path):
            return jsonify({"error": "Invalid path"}), 400
        
        items = []
        for item in sorted(os.listdir(path)):
            full_path = os.path.join(path, item)
            if os.path.isdir(full_path):
                # Check if it looks like a docker project
                has_docker = os.path.exists(os.path.join(full_path, 'docker-compose.yml')) or \
                            os.path.exists(os.path.join(full_path, 'docker-compose.yaml'))
                items.append({
                    "name": item,
                    "path": full_path,
                    "is_docker_project": has_docker
                })
        
        return jsonify({
            "current_path": path,
            "parent_path": os.path.dirname(path) if path != '/' else None,
            "directories": items
        })
    except PermissionError:
        return jsonify({"error": "Permission denied"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/projects')
def get_projects():
    """Get all discovered projects."""
    projects = get_updater().discover_projects()
    return jsonify([p.to_dict() for p in projects])


@app.route('/api/scan/<project_name>')
def scan_project(project_name):
    """Scan a specific project for outdated packages."""
    try:
        logger.info(f"API: Scanning project {project_name}")
        upd = get_updater()
        if not upd.projects:
            upd.discover_projects()
        
        project = next((p for p in upd.projects if p.name == project_name), None)
        if not project:
            logger.error(f"Project not found: {project_name}")
            return jsonify({"error": "Project not found"}), 404
        
        if not project.package_manager:
            logger.warning(f"No package manager for: {project_name}")
            return jsonify({"error": "No package manager detected"}), 400
        
        upd.scan_project(project)
        logger.info(f"Scan complete for {project_name}: {len(project.packages)} packages")
        return jsonify(project.to_dict())
    except Exception as e:
        logger.exception(f"Error scanning {project_name}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/scan-all')
def scan_all():
    """Scan all projects."""
    try:
        logger.info("API: Scanning all projects")
        upd = get_updater()
        if not upd.projects:
            upd.discover_projects()
        
        results = []
        for project in upd.projects:
            if project.package_manager:
                upd.scan_project(project)
            results.append(project.to_dict())
        
        logger.info(f"Scan all complete: {len(results)} projects")
        return jsonify(results)
    except Exception as e:
        logger.exception("Error scanning all projects")
        return jsonify({"error": str(e)}), 500


@app.route('/api/update/<project_name>', methods=['POST'])
def update_project(project_name):
    """Update packages for a project."""
    upd = get_updater()
    if not upd.projects:
        upd.discover_projects()
    
    project = next((p for p in upd.projects if p.name == project_name), None)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    # Scan first if not already scanned
    if not project.packages:
        upd.scan_project(project)
    
    success = upd.update_project(project)
    return jsonify({
        "success": success,
        "project": project.to_dict()
    })


@app.route('/api/update-package/<project_name>', methods=['POST'])
def update_single_package(project_name):
    """Update a single package in a project."""
    upd = get_updater()
    if not upd.projects:
        upd.discover_projects()

    project = next((p for p in upd.projects if p.name == project_name), None)
    if not project:
        return jsonify({"error": "Project not found"}), 404

    data = request.json
    if not data or not data.get('package'):
        return jsonify({"error": "No package specified"}), 400

    package_name = data['package']

    # Scan first if not already scanned
    if not project.packages:
        upd.scan_project(project)

    success = upd.update_single_package(project, package_name)
    return jsonify({
        "success": success,
        "project": project.to_dict()
    })


@app.route('/api/rebuild/<project_name>', methods=['POST'])
def rebuild_project(project_name):
    """Rebuild Docker container for a project."""
    upd = get_updater()
    if not upd.projects:
        upd.discover_projects()
    
    project = next((p for p in upd.projects if p.name == project_name), None)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    result = upd.rebuild_container(project)
    return jsonify(result)


@app.route('/api/backups')
def get_backups():
    """Get all available backups."""
    project_name = request.args.get('project')
    backups = get_updater().list_backups(project_name)
    return jsonify(backups)


@app.route('/api/rollback/<project_name>', methods=['POST'])
def rollback_project(project_name):
    """Rollback a project to a previous state."""
    timestamp = request.json.get('timestamp') if request.json else None
    success = get_updater().rollback_project(project_name, timestamp)
    return jsonify({"success": success})


@app.route('/api/health')
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
