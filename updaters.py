"""
Package update logic for all supported package managers.
"""

import re
import json
import logging
from typing import Dict, List

from models import Package, PackageManager, Project
from scanners import check_docker_outdated

logger = logging.getLogger(__name__)


def update_pip_packages(project: Project) -> bool:
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


def update_npm_packages(project: Project) -> bool:
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


def update_composer_packages(project: Project) -> bool:
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


def update_go_packages(project: Project) -> bool:
    """Update go.mod packages."""
    if not project.dependency_file:
        return False
    with open(project.dependency_file, 'r') as f:
        content = f.read()

    pkg_lookup = {p.name: p for p in project.packages if p.is_outdated}
    for name, pkg in pkg_lookup.items():
        pattern = re.compile(re.escape(name) + r'\s+v[\d.]+')
        content = pattern.sub(f"{name} v{pkg.latest_version}", content)

    with open(project.dependency_file, 'w') as f:
        f.write(content)
    return True


def update_bundler_packages(project: Project) -> bool:
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


def update_cargo_packages(project: Project) -> bool:
    """Update Cargo.toml packages."""
    if not project.dependency_file:
        return False
    with open(project.dependency_file, 'r') as f:
        content = f.read()

    for pkg in project.packages:
        if not pkg.is_outdated:
            continue
        pattern = re.compile(
            r'(' + re.escape(pkg.name) + r'\s*=\s*")' + r'[^"]+(")'
        )
        content = pattern.sub(f'\\g<1>{pkg.latest_version}\\g<2>', content)
        pattern2 = re.compile(
            r'(' + re.escape(pkg.name) + r'\s*=\s*\{.*?version\s*=\s*")' + r'[^"]+(")',
            re.DOTALL
        )
        content = pattern2.sub(f'\\g<1>{pkg.latest_version}\\g<2>', content)

    with open(project.dependency_file, 'w') as f:
        f.write(content)
    return True


def update_maven_packages(project: Project) -> bool:
    """Update pom.xml packages."""
    if not project.dependency_file:
        return False
    with open(project.dependency_file, 'r') as f:
        content = f.read()

    for pkg in project.packages:
        if not pkg.is_outdated:
            continue
        group_id, artifact_id = pkg.name.split(':', 1)
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


def update_gradle_packages(project: Project) -> bool:
    """Update build.gradle packages."""
    if not project.dependency_file:
        return False
    with open(project.dependency_file, 'r') as f:
        content = f.read()

    for pkg in project.packages:
        if not pkg.is_outdated:
            continue
        group_id, artifact_id = pkg.name.split(':', 1)
        pattern = re.compile(
            r'([\'"])' + re.escape(group_id) + r':' + re.escape(artifact_id) + r':[^\'"]+([\'"])'
        )
        content = pattern.sub(
            f'\\g<1>{group_id}:{artifact_id}:{pkg.latest_version}\\g<2>', content
        )

    with open(project.dependency_file, 'w') as f:
        f.write(content)
    return True


def update_nuget_packages(project: Project) -> bool:
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


def update_docker_packages(project: Project) -> bool:
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
        image_name = old_ref.rsplit(':', 1)[0] if ':' in old_ref else old_ref
        new_ref = f"{image_name}:{pkg.latest_version}"
        pattern = re.compile(
            r'(image:\s*)' + re.escape(old_ref) + r'(\s*(?:#.*)?$)',
            re.MULTILINE
        )
        content = pattern.sub(f'\\g<1>{new_ref}\\g<2>', content)

    with open(compose_file, 'w') as f:
        f.write(content)
    return True


def _get_update_map():
    return {
        PackageManager.PIP.value: update_pip_packages,
        PackageManager.NPM.value: update_npm_packages,
        PackageManager.COMPOSER.value: update_composer_packages,
        PackageManager.GO.value: update_go_packages,
        PackageManager.BUNDLER.value: update_bundler_packages,
        PackageManager.CARGO.value: update_cargo_packages,
        PackageManager.MAVEN.value: update_maven_packages,
        PackageManager.GRADLE.value: update_gradle_packages,
        PackageManager.NUGET.value: update_nuget_packages,
        PackageManager.DOCKER.value: update_docker_packages,
    }


def update_project(project: Project, create_backup_fn) -> bool:
    """Update a project's packages."""
    if not project.package_manager or not any(p.is_outdated for p in project.packages):
        return True

    create_backup_fn(project)

    updater_fn = _get_update_map().get(project.package_manager)
    result = updater_fn(project) if updater_fn else True

    # Also update docker images for non-docker projects
    if project.package_manager != PackageManager.DOCKER.value:
        docker_outdated = [p for p in project.packages if p.package_manager == PackageManager.DOCKER.value and p.is_outdated]
        if docker_outdated:
            update_docker_packages(project)

    return result


def update_single_package(project: Project, package_name: str, create_backup_fn) -> bool:
    """Update a single package in a project."""
    pkg = next((p for p in project.packages if p.name == package_name and p.is_outdated), None)
    if not pkg:
        return False

    create_backup_fn(project)

    # Temporarily scope to just the target package
    original_packages = project.packages
    project.packages = [pkg]

    if pkg.package_manager == PackageManager.DOCKER.value:
        updater_fn = update_docker_packages
    else:
        updater_fn = _get_update_map().get(project.package_manager)
    result = updater_fn(project) if updater_fn else False

    # Restore full list and mark the updated package
    if result:
        for p in original_packages:
            if p.name == package_name:
                p.current_version = p.latest_version
    project.packages = original_packages

    return result
