"""
Package scanning / outdated-detection for all supported package managers.
"""

import os
import re
import json
import logging
import tempfile
import urllib.request
import urllib.error
from datetime import datetime
from typing import List, Optional, Callable

from models import Package, PackageManager, Project, Host

logger = logging.getLogger(__name__)


# ---- PIP (Python) ----

def check_pip_outdated(project: Project, file_reader=None) -> List[Package]:
    """Check for outdated pip packages."""
    packages = []

    if not project.dependency_file:
        logger.warning(f"No dependency file for {project.name}")
        return packages

    content = _read_file(project.dependency_file, file_reader)
    if content is None:
        return packages
    lines = content.splitlines(keepends=True)
    logger.info(f"Read {len(lines)} lines from {project.dependency_file}")

    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('-'):
            continue

        match = re.match(r'^([a-zA-Z0-9_-]+)(?:\[.*\])?(?:([=<>!~]+)(.+))?$', line)
        if match:
            name = match.group(1)
            current = match.group(3).strip() if match.group(3) else "any"
            latest, last_updated = _get_pip_latest(name)
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


def _get_pip_latest(package_name: str) -> tuple:
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


# ---- NPM (JavaScript) ----

def check_npm_outdated(project: Project, file_reader=None) -> List[Package]:
    """Check for outdated npm packages."""
    packages = []

    if not project.dependency_file:
        logger.warning(f"No dependency file for {project.name}")
        return packages

    content = _read_file(project.dependency_file, file_reader)
    if content is None:
        return packages
    try:
        pkg_json = json.loads(content)
        logger.info(f"Loaded package.json for {project.name}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse package.json: {e}")
        return packages

    all_deps = {}
    all_deps.update(pkg_json.get('dependencies', {}))
    all_deps.update(pkg_json.get('devDependencies', {}))

    for name, version in all_deps.items():
        current = version.lstrip('^~>=<')
        prefix = '^' if version.startswith('^') else ('~' if version.startswith('~') else '')
        latest, last_updated = _get_npm_latest(name)
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


def _get_npm_latest(package_name: str) -> tuple:
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


# ---- Composer (PHP) ----

def check_composer_outdated(project: Project, file_reader=None) -> List[Package]:
    """Check for outdated composer packages."""
    packages = []

    if not project.dependency_file:
        logger.warning(f"No dependency file for {project.name}")
        return packages

    content = _read_file(project.dependency_file, file_reader)
    if content is None:
        return packages
    try:
        composer_json = json.loads(content)
        logger.info(f"Loaded composer.json for {project.name}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse composer.json: {e}")
        return packages

    all_deps = {}
    all_deps.update(composer_json.get('require', {}))
    all_deps.update(composer_json.get('require-dev', {}))

    for name, version in all_deps.items():
        if name == 'php' or name.startswith('ext-'):
            continue
        current = version.lstrip('^~>=<')
        prefix = '^' if version.startswith('^') else ('~' if version.startswith('~') else '')
        latest, last_updated = _get_composer_latest(name)
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


def _get_composer_latest(package_name: str) -> tuple:
    """Get latest version and date from Packagist API."""
    try:
        url = f"https://repo.packagist.org/p2/{package_name}.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            pkgs = data.get('packages', {}).get(package_name, [])
            if pkgs:
                version = pkgs[0].get('version', 'unknown').lstrip('v')
                last_updated = None
                time_str = pkgs[0].get('time', '')
                if time_str:
                    last_updated = time_str[:10]
                return version, last_updated
    except urllib.error.HTTPError as e:
        logger.warning(f"Packagist HTTP error for {package_name}: {e.code}")
    except Exception as e:
        logger.warning(f"Failed to get composer version for {package_name}: {e}")
    return "unknown", None


# ---- Go modules ----

def check_go_outdated(project: Project, file_reader=None) -> List[Package]:
    """Check for outdated Go modules."""
    packages = []
    if not project.dependency_file:
        return packages
    content = _read_file(project.dependency_file, file_reader)
    if content is None:
        return packages

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
            if '// indirect' in dep:
                dep = dep.split('//')[0].strip()
            parts = dep.split()
            if len(parts) >= 2:
                name, version = parts[0], parts[1].lstrip('v')
                latest, last_updated = _get_go_latest(name)
                packages.append(Package(name=name, current_version=version, latest_version=latest, package_manager=PackageManager.GO.value, last_updated=last_updated))

    logger.info(f"Found {len(packages)} Go modules in {project.name}")
    return packages


def _get_go_latest(module_name: str) -> tuple:
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

def check_bundler_outdated(project: Project, file_reader=None) -> List[Package]:
    """Check for outdated Ruby gems."""
    packages = []
    if not project.dependency_file:
        return packages
    content = _read_file(project.dependency_file, file_reader)
    if content is None:
        return packages
    lines = content.splitlines(keepends=True)

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        match = re.match(r"gem\s+['\"]([^'\"]+)['\"](?:\s*,\s*['\"]([^'\"]+)['\"])?", stripped)
        if match:
            name = match.group(1)
            version_str = match.group(2) or "any"
            current = re.sub(r'[~>=<!\s]', '', version_str)
            latest, last_updated = _get_gem_latest(name)
            packages.append(Package(name=name, current_version=current, latest_version=latest, package_manager=PackageManager.BUNDLER.value, last_updated=last_updated))

    logger.info(f"Found {len(packages)} gems in {project.name}")
    return packages


def _get_gem_latest(gem_name: str) -> tuple:
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

def check_cargo_outdated(project: Project, file_reader=None) -> List[Package]:
    """Check for outdated Rust crates."""
    packages = []
    if not project.dependency_file:
        return packages
    content = _read_file(project.dependency_file, file_reader)
    if content is None:
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
            match = re.match(r'^([a-zA-Z0-9_-]+)\s*=\s*"([^"]+)"', stripped)
            if match:
                name, current = match.group(1), match.group(2).lstrip('^~>=<')
            else:
                match = re.match(r'^([a-zA-Z0-9_-]+)\s*=\s*\{.*version\s*=\s*"([^"]+)"', stripped)
                if match:
                    name, current = match.group(1), match.group(2).lstrip('^~>=<')
                else:
                    continue
            latest, last_updated = _get_crate_latest(name)
            packages.append(Package(name=name, current_version=current, latest_version=latest, package_manager=PackageManager.CARGO.value, last_updated=last_updated))

    logger.info(f"Found {len(packages)} crates in {project.name}")
    return packages


def _get_crate_latest(crate_name: str) -> tuple:
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

def check_maven_outdated(project: Project, file_reader=None) -> List[Package]:
    """Check for outdated Maven dependencies."""
    packages = []
    if not project.dependency_file:
        return packages
    content = _read_file(project.dependency_file, file_reader)
    if content is None:
        return packages

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
        latest, last_updated = _get_maven_latest(group_id, artifact_id)
        packages.append(Package(name=name, current_version=version, latest_version=latest, package_manager=PackageManager.MAVEN.value, last_updated=last_updated))

    logger.info(f"Found {len(packages)} Maven deps in {project.name}")
    return packages


def _get_maven_latest(group_id: str, artifact_id: str) -> tuple:
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

def check_gradle_outdated(project: Project, file_reader=None) -> List[Package]:
    """Check for outdated Gradle dependencies."""
    packages = []
    if not project.dependency_file:
        return packages
    content = _read_file(project.dependency_file, file_reader)
    if content is None:
        return packages

    dep_pattern = re.compile(
        r"(?:implementation|api|compile|testImplementation|runtimeOnly|compileOnly)\s+['\"]([^:]+):([^:]+):([^'\"]+)['\"]"
    )
    for match in dep_pattern.finditer(content):
        group_id, artifact_id, version = match.group(1), match.group(2), match.group(3)
        if version.startswith('$'):
            continue
        name = f"{group_id}:{artifact_id}"
        latest, last_updated = _get_maven_latest(group_id, artifact_id)
        packages.append(Package(name=name, current_version=version, latest_version=latest, package_manager=PackageManager.GRADLE.value, last_updated=last_updated))

    logger.info(f"Found {len(packages)} Gradle deps in {project.name}")
    return packages


# ---- NuGet (.NET) ----

def check_nuget_outdated(project: Project, file_reader=None) -> List[Package]:
    """Check for outdated NuGet packages."""
    packages = []
    if not project.dependency_file:
        return packages
    content = _read_file(project.dependency_file, file_reader)
    if content is None:
        return packages

    ref_pattern = re.compile(
        r'<PackageReference\s+Include="([^"]+)"\s+Version="([^"]+)"',
        re.IGNORECASE
    )
    for match in ref_pattern.finditer(content):
        name, version = match.group(1), match.group(2)
        latest, last_updated = _get_nuget_latest(name)
        packages.append(Package(name=name, current_version=version, latest_version=latest, package_manager=PackageManager.NUGET.value, last_updated=last_updated))

    logger.info(f"Found {len(packages)} NuGet packages in {project.name}")
    return packages


def _get_nuget_latest(package_name: str) -> tuple:
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
                last_updated = _get_nuget_date(package_name, version)
                return version, last_updated
    except Exception as e:
        logger.warning(f"NuGet error for {package_name}: {e}")
    return "unknown", None


def _get_nuget_date(package_name: str, version: str) -> Optional[str]:
    """Get publish date for a specific NuGet package version."""
    try:
        url = f"https://api.nuget.org/v3/registration5-gz-semver2/{package_name.lower()}/index.json"
        req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            items = data.get('items', [])
            if items:
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


# ---- Docker images ----

def check_docker_outdated(project: Project, file_reader=None) -> List[Package]:
    """Check for outdated Docker images in docker-compose file."""
    packages = []
    compose_file = project.docker_compose_file
    if not compose_file:
        return packages
    content = _read_file(compose_file, file_reader)
    if content is None:
        return packages

    image_pattern = re.compile(r'^\s*image:\s*([^\s#]+)', re.MULTILINE)
    seen = set()
    for match in image_pattern.finditer(content):
        image_ref = match.group(1).strip().strip('"').strip("'")
        if image_ref in seen:
            continue
        seen.add(image_ref)

        if ':' in image_ref and not image_ref.startswith('sha256'):
            parts = image_ref.rsplit(':', 1)
            image_name, current_tag = parts[0], parts[1]
        else:
            image_name = image_ref
            current_tag = 'latest'

        latest_tag, last_updated = _get_docker_latest(image_name, current_tag)
        packages.append(Package(
            name=image_ref,
            current_version=current_tag,
            latest_version=latest_tag,
            package_manager=PackageManager.DOCKER.value,
            last_updated=last_updated
        ))

    logger.info(f"Found {len(packages)} Docker images in {project.name}")
    return packages


def _get_docker_latest(image_name: str, current_tag: str) -> tuple:
    """Get latest version tag and push date from Docker Hub."""
    try:
        if '/' not in image_name:
            api_path = f"library/{image_name}"
        elif '.' in image_name.split('/')[0]:
            logger.info(f"Skipping non-Docker Hub image: {image_name}")
            return current_tag, None
        else:
            api_path = image_name

        if not re.match(r'^v?\d+', current_tag):
            url = f"https://hub.docker.com/v2/repositories/{api_path}/tags/{current_tag}"
            req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode())
                last_updated = data.get('last_updated', '')[:10] if data.get('last_updated') else None
                return current_tag, last_updated

        url = f"https://hub.docker.com/v2/repositories/{api_path}/tags/?page_size=100&ordering=-last_updated"
        req = urllib.request.Request(url, headers={'User-Agent': 'DockerPackageUpdater/1.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode())
            results = data.get('results', [])

            dot_count = current_tag.replace('v', '').count('.')
            prefix = 'v' if current_tag.startswith('v') else ''

            best_version = current_tag.lstrip('v')
            best_date = None

            for tag_info in results:
                tag_name = tag_info.get('name', '')
                raw = tag_name.lstrip('v')

                if raw.count('.') != dot_count:
                    continue
                if not all(part.isdigit() for part in raw.split('.')):
                    continue

                try:
                    tag_tuple = tuple(int(x) for x in raw.split('.'))
                    best_tuple = tuple(int(x) for x in best_version.split('.'))
                    if tag_tuple > best_tuple:
                        best_version = raw
                        best_date = tag_info.get('last_updated', '')[:10] if tag_info.get('last_updated') else None
                except ValueError:
                    continue

            if best_version == current_tag.lstrip('v'):
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


# ---- Project discovery & scanning ----

def discover_projects(workspace_path, host: Host = None) -> List[Project]:
    """Discover all Docker projects. Uses SSH for remote hosts."""
    if host and not host.is_local:
        return _discover_projects_remote(host)
    return _discover_projects_local(workspace_path, host_id=host.id if host else None)


def _discover_projects_local(workspace_path, host_id: str = None) -> List[Project]:
    """Discover all Docker projects in the local workspace."""
    from pathlib import Path
    projects = []
    ws = Path(workspace_path)

    for pattern in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]:
        for compose_file in ws.rglob(pattern):
            skip_dirs = ["node_modules", "vendor", ".git", ".package-backups", "logging-proxy"]
            if any(skip in str(compose_file) for skip in skip_dirs):
                continue

            project_path = compose_file.parent
            project_name = project_path.name

            if any(p.path == str(project_path) for p in projects):
                continue

            project = Project(
                name=project_name,
                path=str(project_path),
                docker_compose_file=str(compose_file),
                host_id=host_id,
            )

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

            if project.package_manager is None:
                project.package_manager = PackageManager.DOCKER.value
                project.dependency_file = str(compose_file)

            projects.append(project)

    return sorted(projects, key=lambda p: (p.package_manager is None, p.name.lower()))


def _discover_projects_remote(host: Host) -> List[Project]:
    """Discover Docker projects on a remote host via SSH."""
    from ssh_client import ssh_find_compose_files, ssh_file_exists_in_dir, ssh_glob_in_dir

    compose_files = ssh_find_compose_files(host, host.projects_path)
    projects = []
    seen_paths = set()
    sep = "\\" if host.host_type == "windows" else "/"

    dep_file_map = [
        ("requirements.txt", PackageManager.PIP.value),
        ("package.json", PackageManager.NPM.value),
        ("composer.json", PackageManager.COMPOSER.value),
        ("go.mod", PackageManager.GO.value),
        ("Gemfile", PackageManager.BUNDLER.value),
        ("Cargo.toml", PackageManager.CARGO.value),
        ("pom.xml", PackageManager.MAVEN.value),
        ("build.gradle", PackageManager.GRADLE.value),
    ]

    for compose_file in compose_files:
        if host.host_type == "windows":
            project_path = compose_file.rsplit("\\", 1)[0]
            project_name = project_path.rsplit("\\", 1)[-1]
        else:
            project_path = compose_file.rsplit("/", 1)[0]
            project_name = project_path.rsplit("/", 1)[-1]

        if project_path in seen_paths:
            continue
        seen_paths.add(project_path)

        project = Project(
            name=project_name,
            path=project_path,
            docker_compose_file=compose_file,
            host_id=host.id,
        )

        # Detect package manager
        found = False
        for dep_filename, pm_value in dep_file_map:
            if ssh_file_exists_in_dir(host, project_path, dep_filename):
                project.package_manager = pm_value
                project.dependency_file = f"{project_path}{sep}{dep_filename}"
                found = True
                break

        if not found:
            # Check for .csproj
            csproj = ssh_glob_in_dir(host, project_path, "*.csproj")
            if csproj:
                project.package_manager = PackageManager.NUGET.value
                project.dependency_file = f"{project_path}{sep}{csproj[0]}"
                found = True

        if not found:
            project.package_manager = PackageManager.DOCKER.value
            project.dependency_file = compose_file

        projects.append(project)

    return sorted(projects, key=lambda p: (p.package_manager is None, p.name.lower()))


def scan_project(project: Project, host: Host = None) -> Project:
    """Scan a single project for outdated packages."""
    logger.info(f"Scanning project: {project.name} (manager: {project.package_manager})")

    # For remote hosts, download dependency file to temp and scan locally
    file_reader = None
    if host and not host.is_local:
        file_reader = _make_remote_reader(host)

    scan_map = {
        PackageManager.PIP.value: check_pip_outdated,
        PackageManager.NPM.value: check_npm_outdated,
        PackageManager.COMPOSER.value: check_composer_outdated,
        PackageManager.GO.value: check_go_outdated,
        PackageManager.BUNDLER.value: check_bundler_outdated,
        PackageManager.CARGO.value: check_cargo_outdated,
        PackageManager.MAVEN.value: check_maven_outdated,
        PackageManager.GRADLE.value: check_gradle_outdated,
        PackageManager.NUGET.value: check_nuget_outdated,
        PackageManager.DOCKER.value: check_docker_outdated,
    }

    checker = scan_map.get(project.package_manager)
    if checker:
        project.packages = checker(project, file_reader=file_reader)

    # For non-docker projects, also scan docker images from compose file
    if project.package_manager != PackageManager.DOCKER.value:
        docker_pkgs = check_docker_outdated(project, file_reader=file_reader)
        if docker_pkgs:
            project.packages.extend(docker_pkgs)

    project.status = "scanned"
    project.last_scan = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"Scan complete for {project.name}: {len(project.packages)} packages, {project.outdated_count} outdated")
    return project


def _make_remote_reader(host: Host):
    """Create a file reader function for remote hosts."""
    from ssh_client import ssh_read_file

    def reader(path):
        return ssh_read_file(host, path)
    return reader


def _read_file(path: str, file_reader=None) -> Optional[str]:
    """Read a file, locally or via file_reader."""
    if file_reader:
        return file_reader(path)
    try:
        with open(path, 'r') as f:
            return f.read()
    except IOError as e:
        logger.error(f"Failed to read {path}: {e}")
        return None
