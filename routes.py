"""
Flask API routes for Docker Package Updater.
"""

import os
import logging
from datetime import datetime
from pathlib import Path
from flask import render_template, jsonify, request

from config import app_config, save_config, DEFAULT_PROJECTS_PATH, get_hosts, get_host, get_active_host, save_host, delete_host
from models import Project, Host
from scanners import discover_projects, scan_project
from updaters import update_project, update_single_package
from containers import rebuild_container
from backups import create_backup, list_backups, rollback_project

logger = logging.getLogger(__name__)

# Module-level state: per-host project caches
_host_projects = {}  # host_id -> list of Projects
_host_paths = {}     # host_id -> last known projects_path


def _get_current_host() -> Host:
    """Get the host from the request query param, or active host."""
    host_id = request.args.get('host') or app_config.get('active_host_id', 'local')
    host = get_host(app_config, host_id)
    if not host:
        host = get_active_host(app_config)
    return host


def _get_host_projects(host: Host):
    """Get or discover projects for a host."""
    global _host_projects, _host_paths
    projects_path = host.projects_path or DEFAULT_PROJECTS_PATH
    cached_path = _host_paths.get(host.id)

    if host.id not in _host_projects or cached_path != projects_path:
        _host_paths[host.id] = projects_path
        _host_projects[host.id] = discover_projects(projects_path, host)

    return _host_projects[host.id]


def _ensure_discovered(host: Host):
    """Ensure projects have been discovered for a host."""
    return _get_host_projects(host)


def _find_project(project_name, host: Host):
    """Find a project by name on a specific host."""
    projects = _ensure_discovered(host)
    return next((p for p in projects if p.name == project_name), None)


def _reset_host(host_id: str):
    """Reset project cache for a host."""
    global _host_projects, _host_paths
    _host_projects.pop(host_id, None)
    _host_paths.pop(host_id, None)


def register_routes(app):
    """Register all Flask routes on the app."""

    @app.route('/')
    def index():
        return render_template('index.html')

    # ---- Host management ----

    @app.route('/api/hosts', methods=['GET'])
    def get_hosts_api():
        """Get all configured hosts."""
        hosts = get_hosts(app_config)
        return jsonify({
            "hosts": [h.to_dict() for h in hosts],
            "active_host_id": app_config.get('active_host_id', 'local')
        })

    @app.route('/api/hosts', methods=['POST'])
    def add_host_api():
        """Add or update a host."""
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        if not data.get('name'):
            return jsonify({"error": "Host name is required"}), 400
        if not data.get('host_type'):
            return jsonify({"error": "Host type is required"}), 400

        # Editing existing host
        host_id = data.get('id')
        if host_id:
            existing = get_host(app_config, host_id)
            if existing:
                # Preserve password if not provided
                if not data.get('password') and existing.password:
                    data['password'] = existing.password
                data['is_local'] = existing.is_local

        host = Host.from_dict(data)
        if save_host(app_config, host):
            _reset_host(host.id)
            return jsonify({"success": True, "host": host.to_dict()})
        return jsonify({"error": "Failed to save host"}), 500

    @app.route('/api/hosts/<host_id>', methods=['DELETE'])
    def delete_host_api(host_id):
        """Delete a remote host."""
        if delete_host(app_config, host_id):
            _reset_host(host_id)
            return jsonify({"success": True})
        return jsonify({"error": "Cannot delete this host"}), 400

    @app.route('/api/hosts/<host_id>/test', methods=['POST'])
    def test_host_api(host_id):
        """Test SSH connection to a host."""
        host = get_host(app_config, host_id)
        if not host:
            return jsonify({"error": "Host not found"}), 404
        if host.is_local:
            return jsonify({"success": True, "message": "Local host is always available"})

        from ssh_client import test_connection
        success, message = test_connection(host)
        return jsonify({"success": success, "message": message})

    @app.route('/api/hosts/active', methods=['POST'])
    def set_active_host():
        """Set the active host."""
        data = request.json
        host_id = data.get('host_id') if data else None
        if not host_id:
            return jsonify({"error": "No host_id provided"}), 400
        host = get_host(app_config, host_id)
        if not host:
            return jsonify({"error": "Host not found"}), 404
        app_config['active_host_id'] = host_id
        save_config(app_config)
        return jsonify({"success": True, "active_host_id": host_id})

    # ---- Config ----

    @app.route('/api/config', methods=['GET'])
    def get_config():
        """Get current configuration."""
        host = _get_current_host()
        return jsonify({
            "projects_path": host.projects_path if host else app_config.get('projects_path', DEFAULT_PROJECTS_PATH),
            "scan_timeout": app_config.get('scan_timeout', 30),
            "auto_backup": app_config.get('auto_backup', True),
            "host_id": host.id if host else 'local',
        })

    @app.route('/api/config', methods=['POST'])
    def set_config():
        """Update configuration."""
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        host = _get_current_host()

        new_path = data.get('projects_path')
        if new_path:
            if host and host.is_local:
                if not os.path.isdir(new_path):
                    return jsonify({"error": f"Path does not exist: {new_path}"}), 400
            # Update path on host
            if host:
                host.projects_path = new_path
                save_host(app_config, host)
                _reset_host(host.id)
            # Also update legacy projects_path
            app_config['projects_path'] = new_path

        if 'scan_timeout' in data:
            app_config['scan_timeout'] = int(data['scan_timeout'])
        if 'auto_backup' in data:
            app_config['auto_backup'] = bool(data['auto_backup'])

        if save_config(app_config):
            logger.info(f"Config updated: {app_config}")
            return jsonify({"success": True, "config": app_config})
        else:
            return jsonify({"error": "Failed to save config"}), 500

    @app.route('/api/browse', methods=['GET'])
    def browse_directory():
        """Browse directories for path selection."""
        path = request.args.get('path', '/')
        host = _get_current_host()

        if host and not host.is_local:
            return _browse_remote(host, path)

        try:
            if not os.path.isdir(path):
                return jsonify({"error": "Invalid path"}), 400

            items = []
            for item in sorted(os.listdir(path)):
                full_path = os.path.join(path, item)
                if os.path.isdir(full_path):
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

    def _browse_remote(host, path):
        """Browse directories on a remote host."""
        from ssh_client import ssh_list_dirs, ssh_is_dir
        if not ssh_is_dir(host, path):
            return jsonify({"error": f"Invalid path: {path}"}), 400

        folders = ssh_list_dirs(host, path)
        return jsonify({
            "current_path": path,
            "folders": folders,
        })

    # ---- Projects ----

    @app.route('/api/projects')
    def get_projects():
        """Get all discovered projects for the active host."""
        host = _get_current_host()
        projects_path = host.projects_path if host else app_config.get('projects_path', DEFAULT_PROJECTS_PATH)
        projects = discover_projects(projects_path, host)
        _host_projects[host.id] = projects
        _host_paths[host.id] = projects_path
        return jsonify([p.to_dict() for p in projects])

    @app.route('/api/scan/<project_name>')
    def api_scan_project(project_name):
        """Scan a specific project for outdated packages."""
        try:
            host = _get_current_host()
            logger.info(f"API: Scanning project {project_name} on {host.name}")
            project = _find_project(project_name, host)
            if not project:
                logger.error(f"Project not found: {project_name}")
                return jsonify({"error": "Project not found"}), 404

            if not project.package_manager:
                logger.warning(f"No package manager for: {project_name}")
                return jsonify({"error": "No package manager detected"}), 400

            scan_project(project, host)
            logger.info(f"Scan complete for {project_name}: {len(project.packages)} packages")
            return jsonify(project.to_dict())
        except Exception as e:
            logger.exception(f"Error scanning {project_name}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/scan-all')
    def api_scan_all():
        """Scan all projects."""
        try:
            host = _get_current_host()
            logger.info(f"API: Scanning all projects on {host.name}")
            projects = _ensure_discovered(host)

            results = []
            for project in projects:
                if project.package_manager:
                    scan_project(project, host)
                results.append(project.to_dict())

            logger.info(f"Scan all complete: {len(results)} projects")
            return jsonify(results)
        except Exception as e:
            logger.exception("Error scanning all projects")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/update/<project_name>', methods=['POST'])
    def api_update_project(project_name):
        """Update packages for a project."""
        host = _get_current_host()
        project = _find_project(project_name, host)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        if not project.packages:
            scan_project(project, host)

        success = update_project(project, create_backup)
        if success:
            scan_project(project, host)
        return jsonify({
            "success": success,
            "project": project.to_dict()
        })

    @app.route('/api/update-package/<project_name>', methods=['POST'])
    def api_update_single_package(project_name):
        """Update a single package in a project."""
        host = _get_current_host()
        project = _find_project(project_name, host)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        data = request.json
        if not data or not data.get('package'):
            return jsonify({"error": "No package specified"}), 400

        package_name = data['package']

        if not project.packages:
            scan_project(project, host)

        success = update_single_package(project, package_name, create_backup)
        if success:
            scan_project(project, host)
        return jsonify({
            "success": success,
            "project": project.to_dict()
        })

    @app.route('/api/rebuild/<project_name>', methods=['POST'])
    def api_rebuild_project(project_name):
        """Rebuild Docker container for a project."""
        host = _get_current_host()
        project = _find_project(project_name, host)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        result = rebuild_container(project, host)
        return jsonify(result)

    @app.route('/api/backups')
    def api_get_backups():
        """Get all available backups."""
        project_name = request.args.get('project')
        backups = list_backups(project_name)
        return jsonify(backups)

    @app.route('/api/rollback/<project_name>', methods=['POST'])
    def api_rollback_project(project_name):
        """Rollback a project to a previous state."""
        timestamp = request.json.get('timestamp') if request.json else None
        success = rollback_project(project_name, timestamp)
        return jsonify({"success": success})

    @app.route('/api/health')
    def health():
        """Health check endpoint."""
        return jsonify({"status": "ok", "timestamp": datetime.now().isoformat()})
