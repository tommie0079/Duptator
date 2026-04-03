"""
Configuration management for Docker Package Updater.
"""

import os
import json
import logging

logger = logging.getLogger(__name__)

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


# Global config instance
app_config = load_config()
