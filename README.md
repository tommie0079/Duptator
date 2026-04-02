<div align="center">

# 📦 Duptator

**Docker Package Updater**

*Keep your Docker project dependencies safe, updated, and under control.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](Dockerfile)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com)
[![GitHub issues](https://img.shields.io/github/issues/tommie0079/Duptator)](https://github.com/tommie0079/Duptator/issues)
[![GitHub stars](https://img.shields.io/github/stars/tommie0079/Duptator?style=social)](https://github.com/tommie0079/Duptator/stargazers)

---

[**Getting Started**](#-getting-started) · [**Features**](#-features) · [**How It Works**](#-how-it-works) · [**Configuration**](#%EF%B8%8F-configuration) · [**Contributing**](#-contributing)

</div>

---

## 💡 Motivation

This project was born out of the [npm supply chain attacks](https://en.wikipedia.org/wiki/Npm#Security) that compromised thousands of projects through outdated and malicious dependencies. These incidents showed how easily a single unpatched package can cascade into a serious security breach.

Duptator was built to solve a simple problem: **keeping dependencies up-to-date shouldn't be hard.** Whether you run 5 or 50 Docker projects on your NAS, Duptator gives you a single dashboard to scan, update, and rollback packages — safely and instantly.

> **Stay updated. Stay secure.** 🛡️

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔍 **Scan** | Discover outdated packages across all Docker projects |
| ⬆️ **Update All** | One-click bulk update with automatic backups |
| 🎯 **Per-Package Update** | Expand any project row and update individual packages |
| ⚠️ **Stale Detection** | Yellow flag warning for packages not updated in 6+ months |
| ↩️ **Rollback** | Instantly restore previous dependency versions |
| 🔨 **Rebuild** | Rebuild or pull + restart containers (auto-detects `build:` vs `image:`) |
| 📋 **Build Log Viewer** | Full terminal output from rebuild/pull shown in a syntax-highlighted modal |
| 📊 **Progress Bars** | Real-time progress for scanning, updating, and rebuilding |
| 🔔 **Rebuild Warnings** | Persistent banner reminding you which projects need a rebuild |
| ⚙️ **Settings** | Configure projects path with built-in folder browser |

### Supported Package Managers

| Manager | File | Registry |
|---------|------|----------|
| ![Python](https://img.shields.io/badge/pip-3776AB?logo=python&logoColor=white) | `requirements.txt` | PyPI |
| ![npm](https://img.shields.io/badge/npm-CB3837?logo=npm&logoColor=white) | `package.json` | npm Registry |
| ![Composer](https://img.shields.io/badge/Composer-885630?logo=composer&logoColor=white) | `composer.json` | Packagist |
| ![Go](https://img.shields.io/badge/Go_Modules-00ADD8?logo=go&logoColor=white) | `go.mod` | proxy.golang.org |
| ![Ruby](https://img.shields.io/badge/Bundler-CC342D?logo=ruby&logoColor=white) | `Gemfile` | RubyGems |
| ![Rust](https://img.shields.io/badge/Cargo-DEA584?logo=rust&logoColor=black) | `Cargo.toml` | crates.io |
| ![Maven](https://img.shields.io/badge/Maven-C71A36?logo=apachemaven&logoColor=white) | `pom.xml` | Maven Central |
| ![Gradle](https://img.shields.io/badge/Gradle-02303A?logo=gradle&logoColor=white) | `build.gradle` | Maven Central |
| ![NuGet](https://img.shields.io/badge/NuGet-004880?logo=nuget&logoColor=white) | `*.csproj` | nuget.org |

---

## 🚀 Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/install/)
- Access to the host where your Docker projects live

### 1. Clone

```bash
git clone https://github.com/tommie0079/Duptator.git
cd Duptator
```

### 2. Configure

Edit `docker-compose.yml` to match your environment:

```yaml
volumes:
  - /volume1:/volume1:rw          # Mount your NAS volume
  - updater-data:/app/data        # Persistent config & backups
environment:
  - PROJECTS_PATH=/volume1/docker # Default scan path
```

<details>
<summary><b>📁 Common NAS paths</b></summary>

| Platform | Path |
|----------|------|
| **Synology** | `/volume1/docker` |
| **QNAP** | `/share/Container` |
| **TrueNAS** | `/mnt/pool/docker` |
| **Unraid** | `/mnt/user/appdata` |
| **Linux** | `/home/user/docker` |

</details>

### 3. Deploy

```bash
docker compose up -d --build
```

### 4. Open

Navigate to **`http://your-server-ip:4554`**

---

## 🔧 How It Works

```
┌─────────────────────────────────────────────┐
│  Browser — http://your-ip:4554              │
└───────────────────┬─────────────────────────┘
                    │
┌───────────────────▼─────────────────────────┐
│  Duptator Container                         │
│                                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │  Scan    │  │  Update  │  │ Rollback │  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  │
│       │              │              │        │
│  ┌────▼──────────────▼──────────────▼────┐  │
│  │  Package Registry APIs               │  │
│  │  PyPI · npm · Packagist · Go Proxy   │  │
│  │  RubyGems · crates.io · Maven Central│  │
│  │  NuGet · + publish date detection    │  │
│  └───────────────────────────────────────┘  │
│                                             │
│  Mounted: /volume1/docker (your projects)   │
└─────────────────────────────────────────────┘
```

| Action | What happens |
|--------|-------------|
| **Scan** | Finds all `docker-compose.yml` files, reads dependency files, checks latest versions via registry APIs |
| **Update All** | Creates timestamped backups, writes new version numbers to all outdated packages across projects |
| **Update Single** | Expand a project row → click Update on an individual package → only that package is updated |
| **Stale Check** | Flags packages whose latest version was published 6+ months ago with a ⚠️ warning |
| **Rebuild** | Auto-detects `build:` vs `image:` projects. Runs `build --no-cache` + `up -d` or `pull` + `up -d`. Full log shown in a syntax-highlighted viewer |
| **Rollback** | Restores dependency file from backup, ready to rebuild |

---

## ⚙️ Configuration

All settings are configurable from the **web UI**:

| Setting | Description | Default |
|---------|-------------|---------|
| Projects Path | Root folder to scan for Docker projects | `/volume1/docker` |

Settings are persisted in a Docker volume and survive container restarts.

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PROJECTS_PATH` | Default projects directory | `/volume1/docker` |
| `CONFIG_FILE` | Config file location | `/app/data/config.json` |
| `BACKUP_DIR` | Backup storage location | `/app/data/backups` |

---

## 🏗️ Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.11 · Flask |
| Frontend | Vanilla HTML · CSS · JavaScript |
| Container | Docker · Docker Compose |
| Version Checks | PyPI · npm · Packagist · Go Proxy · RubyGems · crates.io · Maven Central · NuGet |
| Stale Detection | Publish date fetched from all 9 registries, flagged at 6+ months |

---

## 🤝 Contributing

Contributions are welcome! Here's how:

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

For major changes, please [open an issue](https://github.com/tommie0079/Duptator/issues) first to discuss what you'd like to change.

---

## 📄 License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.

---

## ⚠️ Disclaimer

This software is provided **as-is**, without any warranty. Use it at your own risk.

Updating packages in production containers can introduce breaking changes, unexpected behavior, or downtime. Always:

- **Review** what will be updated before applying changes
- **Test** updates in a staging environment when possible
- **Use the built-in backup & rollback** feature — it exists for a reason
- **Never blindly update** all packages at once on critical systems

The authors are not responsible for any damage, data loss, or service disruption caused by the use of this tool.

---

<div align="center">

**Built with ❤️ for the self-hosting community**

*Inspired by the need to keep dependencies safe in an era of supply chain attacks*

[![GitHub](https://img.shields.io/badge/GitHub-tommie0079-181717?logo=github)](https://github.com/tommie0079)

</div>
