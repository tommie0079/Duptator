"""
Docker container rebuild logic (including self-rebuild).
"""

import os
import re
import subprocess
import logging
from typing import Dict

from models import Project

logger = logging.getLogger(__name__)


def _has_build_directive(project: Project) -> bool:
    """Check if the docker-compose file uses 'build:' (vs only 'image:')."""
    try:
        with open(project.docker_compose_file, 'r') as f:
            content = f.read()
        return bool(re.search(r'^\s+build:', content, re.MULTILINE))
    except Exception:
        return False


def _is_self(project: Project) -> bool:
    """Check if this project is Duptator itself."""
    try:
        hostname = os.environ.get('HOSTNAME', '')
        if not hostname:
            return False
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


def _self_rebuild(project: Project) -> Dict:
    """Rebuild Duptator itself using a detached helper container."""
    compose_dir = str(project.path)
    compose_file = str(project.docker_compose_file)
    uses_build = _has_build_directive(project)

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


def rebuild_container(project: Project) -> Dict:
    """Rebuild a Docker container."""
    if not project.docker_compose_file:
        return {"success": False, "message": "No docker-compose file found", "log": ""}

    # Self-rebuild: delegate to a detached helper container
    if _is_self(project):
        return _self_rebuild(project)

    uses_build = _has_build_directive(project)
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
            # Also remove containers by project-service naming convention
            svc_result = subprocess.run(
                cmd + ["-f", project.docker_compose_file, "config", "--services"],
                capture_output=True, text=True, timeout=15, cwd=project.path
            )
            if svc_result.returncode == 0 and svc_result.stdout.strip():
                proj_name = project.name.lower().replace(' ', '-')
                for svc in svc_result.stdout.strip().splitlines():
                    for pattern in [f"{proj_name}-{svc}-1", f"{proj_name}-{svc}", svc]:
                        subprocess.run(
                            ["docker", "rm", "-f", pattern],
                            capture_output=True, text=True, timeout=10
                        )
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
