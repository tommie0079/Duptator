"""
Configuration management for Docker Package Updater.
"""

import os
import json
import logging

from models import Host

logger = logging.getLogger(__name__)

CONFIG_FILE = os.environ.get('CONFIG_FILE', '/app/config.json')
DEFAULT_PROJECTS_PATH = os.environ.get('PROJECTS_PATH', '/projects')
BACKUP_DIR = os.environ.get('BACKUP_DIR', '/app/data/backups')


def _default_local_host() -> dict:
    """Create default local host entry."""
    return {
        "id": "local",
        "name": "Local (NAS)",
        "host_type": "synology",
        "hostname": "",
        "port": 22,
        "username": "",
        "password": "",
        "projects_path": DEFAULT_PROJECTS_PATH,
        "is_local": True,
    }


def load_config() -> dict:
    """Load configuration from file."""
    default_config = {
        "projects_path": DEFAULT_PROJECTS_PATH,
        "scan_timeout": 30,
        "auto_backup": True,
        "hosts": [_default_local_host()],
        "active_host_id": "local",
    }

    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                saved_config = json.load(f)
                default_config.update(saved_config)
    except Exception as e:
        logger.warning(f"Failed to load config: {e}")

    # Ensure at least one local host exists
    if not default_config.get('hosts'):
        default_config['hosts'] = [_default_local_host()]

    # Migrate: if old config has projects_path but no hosts, create local host from it
    has_local = any(h.get('is_local') for h in default_config['hosts'])
    if not has_local:
        local = _default_local_host()
        local['projects_path'] = default_config.get('projects_path', DEFAULT_PROJECTS_PATH)
        default_config['hosts'].insert(0, local)

    return default_config


def save_config(config: dict) -> bool:
    """Save configuration to file."""
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save config: {e}")
        return False


def get_hosts(config: dict) -> list:
    """Get list of Host objects from config."""
    return [Host.from_dict(h) for h in config.get('hosts', [])]


def get_host(config: dict, host_id: str) -> Host:
    """Get a specific host by ID."""
    for h in config.get('hosts', []):
        if h.get('id') == host_id:
            return Host.from_dict(h)
    return None


def get_active_host(config: dict) -> Host:
    """Get the currently active host."""
    active_id = config.get('active_host_id', 'local')
    host = get_host(config, active_id)
    if not host:
        hosts = get_hosts(config)
        return hosts[0] if hosts else None
    return host


def save_host(config: dict, host: Host) -> bool:
    """Add or update a host in config."""
    hosts = config.get('hosts', [])
    for i, h in enumerate(hosts):
        if h.get('id') == host.id:
            hosts[i] = host.to_dict(redact_password=False)
            config['hosts'] = hosts
            return save_config(config)
    # New host
    hosts.append(host.to_dict(redact_password=False))
    config['hosts'] = hosts
    return save_config(config)


def delete_host(config: dict, host_id: str) -> bool:
    """Delete a host from config. Cannot delete the local host."""
    hosts = config.get('hosts', [])
    host = next((h for h in hosts if h.get('id') == host_id), None)
    if not host or host.get('is_local'):
        return False
    config['hosts'] = [h for h in hosts if h.get('id') != host_id]
    if config.get('active_host_id') == host_id:
        config['active_host_id'] = 'local'
    return save_config(config)


# Global config instance
app_config = load_config()
