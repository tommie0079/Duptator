"""
SSH client wrapper for remote host operations.
Uses paramiko for SSH connectivity to remote Docker hosts.
"""

import io
import logging
import tempfile
from pathlib import PurePosixPath, PureWindowsPath
from typing import List, Optional, Tuple

import paramiko

from models import Host

logger = logging.getLogger(__name__)

# Cache SSH connections per host ID
_connections = {}


def get_ssh(host: Host) -> paramiko.SSHClient:
    """Get or create an SSH connection for a host."""
    if host.id in _connections:
        transport = _connections[host.id].get_transport()
        if transport and transport.is_active():
            return _connections[host.id]
        # Stale connection, remove
        try:
            _connections[host.id].close()
        except Exception:
            pass
        del _connections[host.id]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=host.hostname,
            port=host.port,
            username=host.username,
            password=host.password if host.password else None,
            timeout=15,
            banner_timeout=15,
            auth_timeout=15,
        )
        _connections[host.id] = client
        logger.info(f"SSH connected to {host.name} ({host.hostname}:{host.port})")
        return client
    except Exception as e:
        logger.error(f"SSH connection failed for {host.name}: {e}")
        raise


def close_ssh(host_id: str):
    """Close SSH connection for a host."""
    if host_id in _connections:
        try:
            _connections[host_id].close()
        except Exception:
            pass
        del _connections[host_id]


def test_connection(host: Host) -> Tuple[bool, str]:
    """Test SSH connection to a host. Returns (success, message)."""
    try:
        ssh = get_ssh(host)
        stdin, stdout, stderr = ssh.exec_command("echo ok", timeout=10)
        result = stdout.read().decode().strip()
        if result == "ok":
            return True, "Connection successful"
        return False, f"Unexpected response: {result}"
    except Exception as e:
        return False, str(e)


def ssh_exec(host: Host, command: str, cwd: str = None, timeout: int = 120) -> Tuple[str, str, int]:
    """Execute a command on a remote host via SSH."""
    ssh = get_ssh(host)

    if cwd:
        if host.host_type == "windows":
            command = f'cd /d "{cwd}" && {command}'
        else:
            command = f'cd "{cwd}" && {command}'

    logger.info(f"SSH exec on {host.name}: {command}")
    stdin, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    out = stdout.read().decode(errors='replace')
    err = stderr.read().decode(errors='replace')
    rc = stdout.channel.recv_exit_status()
    return out, err, rc


def ssh_read_file(host: Host, remote_path: str) -> Optional[str]:
    """Read a file from a remote host."""
    try:
        ssh = get_ssh(host)
        sftp = ssh.open_sftp()
        try:
            with sftp.open(remote_path, 'r') as f:
                content = f.read().decode(errors='replace')
            return content
        finally:
            sftp.close()
    except FileNotFoundError:
        logger.warning(f"File not found on {host.name}: {remote_path}")
        return None
    except Exception as e:
        logger.error(f"Failed to read {remote_path} on {host.name}: {e}")
        return None


def ssh_write_file(host: Host, remote_path: str, content: str):
    """Write content to a file on a remote host."""
    try:
        ssh = get_ssh(host)
        sftp = ssh.open_sftp()
        try:
            with sftp.open(remote_path, 'w') as f:
                f.write(content)
        finally:
            sftp.close()
    except Exception as e:
        logger.error(f"Failed to write {remote_path} on {host.name}: {e}")
        raise


def ssh_list_dirs(host: Host, path: str) -> List[str]:
    """List subdirectories at a path on a remote host."""
    try:
        ssh = get_ssh(host)
        sftp = ssh.open_sftp()
        try:
            items = []
            for attr in sftp.listdir_attr(path):
                if attr.st_mode and (attr.st_mode & 0o40000):  # is directory
                    if not attr.filename.startswith('.'):
                        items.append(attr.filename)
            return sorted(items)
        finally:
            sftp.close()
    except Exception as e:
        logger.error(f"Failed to list dirs at {path} on {host.name}: {e}")
        return []


def ssh_path_exists(host: Host, path: str) -> bool:
    """Check if a path exists on a remote host."""
    try:
        ssh = get_ssh(host)
        sftp = ssh.open_sftp()
        try:
            sftp.stat(path)
            return True
        except FileNotFoundError:
            return False
        finally:
            sftp.close()
    except Exception as e:
        return False


def ssh_is_dir(host: Host, path: str) -> bool:
    """Check if a path is a directory on a remote host."""
    try:
        ssh = get_ssh(host)
        sftp = ssh.open_sftp()
        try:
            attr = sftp.stat(path)
            return bool(attr.st_mode and (attr.st_mode & 0o40000))
        except FileNotFoundError:
            return False
        finally:
            sftp.close()
    except Exception as e:
        return False


def ssh_find_compose_files(host: Host, base_path: str) -> List[str]:
    """Find docker-compose files recursively on a remote host."""
    patterns = ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]
    skip_dirs = ["node_modules", "vendor", ".git", ".package-backups", "logging-proxy"]

    if host.host_type == "windows":
        # Windows: use dir command
        cmd = f'dir /s /b "{base_path}\\docker-compose.yml" "{base_path}\\docker-compose.yaml" "{base_path}\\compose.yml" "{base_path}\\compose.yaml" 2>nul'
    else:
        # Unix: use find command
        name_args = " -o ".join(f'-name "{p}"' for p in patterns)
        prune_args = " -o ".join(f'-name "{d}" -prune' for d in skip_dirs)
        cmd = f'find "{base_path}" \\( {prune_args} \\) -o \\( {name_args} \\) -print 2>/dev/null'

    out, err, rc = ssh_exec(host, cmd)
    files = [line.strip() for line in out.splitlines() if line.strip()]

    # Filter out skipped dirs
    result = []
    for f in files:
        if not any(skip in f for skip in skip_dirs):
            result.append(f)

    logger.info(f"Found {len(result)} compose files on {host.name}")
    return result


def ssh_file_exists_in_dir(host: Host, dir_path: str, filename: str) -> bool:
    """Check if a specific file exists in a directory on remote host."""
    path = f"{dir_path}/{filename}" if host.host_type != "windows" else f"{dir_path}\\{filename}"
    return ssh_path_exists(host, path)


def ssh_glob_in_dir(host: Host, dir_path: str, pattern: str) -> List[str]:
    """Find files matching a glob pattern in a directory on remote host."""
    if host.host_type == "windows":
        cmd = f'dir /b "{dir_path}\\{pattern}" 2>nul'
    else:
        cmd = f'ls -1 "{dir_path}"/{pattern} 2>/dev/null'
    out, err, rc = ssh_exec(host, cmd)
    return [line.strip() for line in out.splitlines() if line.strip()]
