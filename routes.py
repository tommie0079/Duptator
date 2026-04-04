"""
Flask API routes for Docker Package Updater.
"""

import os
import logging
from datetime import datetime
from pathlib import Path
from flask import render_template, jsonify, request

from config import app_config, save_config, DEFAULT_PROJECTS_PATH
from models import Project
from scanners import discover_projects, scan_project
from updaters import update_project, update_single_package
from containers import rebuild_container
from backups import create_backup, list_backups, rollback_project

logger = logging.getLogger(__name__)

# Module-level state
_projects = []
_workspace_path = None


def _get_projects():
    """Get or refresh the project list."""
    global _projects, _workspace_path
    current_path = app_config.get('projects_path', DEFAULT_PROJECTS_PATH)
    if _workspace_path != current_path:
        _projects = []
        _workspace_path = current_path
    return _projects


def _ensure_discovered():
    """Ensure projects have been discovered."""
    global _projects, _workspace_path
    current_path = app_config.get('projects_path', DEFAULT_PROJECTS_PATH)
    if not _projects or _workspace_path != current_path:
        _workspace_path = current_path
        _projects = discover_projects(current_path)
    return _projects


def _find_project(project_name):
    """Find a project by name."""
    projects = _ensure_discovered()
    return next((p for p in projects if p.name == project_name), None)


def reset_updater():
    """Reset project cache (called when config changes)."""
    global _projects, _workspace_path
    _projects = []
    _workspace_path = None


def register_routes(app):
    """Register all Flask routes on the app."""

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
        data = request.json
        if not data:
            return jsonify({"error": "No data provided"}), 400

        new_path = data.get('projects_path')
        if new_path:
            if not os.path.isdir(new_path):
                return jsonify({"error": f"Path does not exist: {new_path}"}), 400
            app_config['projects_path'] = new_path
            reset_updater()

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

    @app.route('/api/projects')
    def get_projects():
        """Get all discovered projects."""
        global _projects, _workspace_path
        current_path = app_config.get('projects_path', DEFAULT_PROJECTS_PATH)
        _workspace_path = current_path
        _projects = discover_projects(current_path)
        return jsonify([p.to_dict() for p in _projects])

    @app.route('/api/scan/<project_name>')
    def api_scan_project(project_name):
        """Scan a specific project for outdated packages."""
        try:
            logger.info(f"API: Scanning project {project_name}")
            project = _find_project(project_name)
            if not project:
                logger.error(f"Project not found: {project_name}")
                return jsonify({"error": "Project not found"}), 404

            if not project.package_manager:
                logger.warning(f"No package manager for: {project_name}")
                return jsonify({"error": "No package manager detected"}), 400

            scan_project(project)
            logger.info(f"Scan complete for {project_name}: {len(project.packages)} packages")
            return jsonify(project.to_dict())
        except Exception as e:
            logger.exception(f"Error scanning {project_name}")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/scan-all')
    def api_scan_all():
        """Scan all projects."""
        try:
            logger.info("API: Scanning all projects")
            projects = _ensure_discovered()

            results = []
            for project in projects:
                if project.package_manager:
                    scan_project(project)
                results.append(project.to_dict())

            logger.info(f"Scan all complete: {len(results)} projects")
            return jsonify(results)
        except Exception as e:
            logger.exception("Error scanning all projects")
            return jsonify({"error": str(e)}), 500

    @app.route('/api/update/<project_name>', methods=['POST'])
    def api_update_project(project_name):
        """Update packages for a project."""
        project = _find_project(project_name)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        if not project.packages:
            scan_project(project)

        success = update_project(project, create_backup)
        if success:
            scan_project(project)
        return jsonify({
            "success": success,
            "project": project.to_dict()
        })

    @app.route('/api/update-package/<project_name>', methods=['POST'])
    def api_update_single_package(project_name):
        """Update a single package in a project."""
        project = _find_project(project_name)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        data = request.json
        if not data or not data.get('package'):
            return jsonify({"error": "No package specified"}), 400

        package_name = data['package']

        if not project.packages:
            scan_project(project)

        success = update_single_package(project, package_name, create_backup)
        if success:
            scan_project(project)
        return jsonify({
            "success": success,
            "project": project.to_dict()
        })

    @app.route('/api/rebuild/<project_name>', methods=['POST'])
    def api_rebuild_project(project_name):
        """Rebuild Docker container for a project."""
        project = _find_project(project_name)
        if not project:
            return jsonify({"error": "Project not found"}), 404

        result = rebuild_container(project)
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
