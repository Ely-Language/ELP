#!/usr/bin/env python3
"""
elp - Ely Language Package Manager

Usage:
    elp init [--lang VERSION] [--force]
    elp project {set [PATH] | unset | show}
    elp install <name|user/repo|URL>[@version] [--version VERSION]
    elp remove <name> [--autoremove]
    elp update [<name>]
    elp clean [--purge | --keep-deps]
    elp list
    elp search <query>
    elp info <name>
"""

import sys
import os
import json
import argparse
import urllib.request
import urllib.error
import zipfile
import io
import shutil
import re
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------
GLOBAL_CONFIG_DIR = Path.home() / '.ely'
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / 'elp-config.json'
LOCAL_CONFIG_DIR_NAME = '.ely'
LOCAL_CONFIG_FILE_NAME = 'config.json'
MANAGER_FILE_NAME = 'manager.json'
MODULES_DIR_NAME = 'modules'
LOCK_FILE_NAME = 'ely.lock'
VERSION_FILE_NAME = 'VERSION'          # рядом с elp.py или в ~/.ely/
DEFAULT_LANGUAGE_VERSION = '26.4'
ELYMODULE_TOPICS = ['elymodule', 'elylib']
GITHUB_API = 'https://api.github.com'
GITHUB_RAW = 'https://raw.githubusercontent.com'

# ----------------------------------------------------------------------
# Helper: semver handling
# ----------------------------------------------------------------------
def semver_tuple(v: str) -> Tuple[int, ...]:
    """Convert '1.2.3' to (1,2,3)."""
    try:
        return tuple(map(int, v.split('.')))
    except Exception:
        return (0, 0, 0)

def version_satisfies(requirement: str, actual: str) -> bool:
    """Check if actual version satisfies requirement (e.g., '>=1.2', '~1.2.3', '*', '1.2.3')."""
    if requirement == '*':
        return True
    req = requirement.strip()
    # Operator '>='
    if req.startswith('>='):
        req_ver = req[2:].strip()
        return semver_tuple(actual) >= semver_tuple(req_ver)
    # Operator '~>' (pessimistic) - allow same major.minor, >= specified
    if req.startswith('~>'):
        req_ver = req[1:].strip()
        act = semver_tuple(actual)
        base = semver_tuple(req_ver)
        if len(base) >= 2 and act[0:2] == base[0:2]:
            return act >= base
        return False
    # Operator '^' (compatible) - allow same major, >= specified
    if req.startswith('^'):
        req_ver = req[1:].strip()
        act = semver_tuple(actual)
        base = semver_tuple(req_ver)
        if act[0] == base[0]:
            return act >= base
        return False
    # Exact match
    return actual == req

def latest_compatible(versions: List[str], requirement: str, language_version: str,
                      fetcher_fn) -> Optional[str]:
    """Return the highest version from list that satisfies requirement and language_version."""
    valid = []
    for v in versions:
        if not version_satisfies(requirement, v):
            continue
        # Check language compatibility (downloading elymodule.json could be heavy,
        # but we cache or do lazy check)
        if fetcher_fn and not fetcher_fn(v):
            continue
        valid.append(v)
    if not valid:
        return None
    valid.sort(key=semver_tuple, reverse=True)
    return valid[0]

# ----------------------------------------------------------------------
# GitHub interaction
# ----------------------------------------------------------------------
class GitHubFetcher:
    @staticmethod
    def search_repos(query: str) -> List[Dict[str, Any]]:
        """Search GitHub repositories with given query, return list of repos."""
        url = f"{GITHUB_API}/search/repositories?q={query}&sort=stars&order=desc&per_page=10"
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github.v3+json'})
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data.get('items', [])
        except Exception as e:
            print(f"GitHub search error: {e}")
            return []

    @staticmethod
    def get_tags(full_name: str) -> List[str]:
        """Get list of tag names (e.g., 'v1.0.0') from a repository."""
        url = f"{GITHUB_API}/repos/{full_name}/tags?per_page=100"
        req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github.v3+json'})
        try:
            with urllib.request.urlopen(req) as resp:
                tags = json.loads(resp.read().decode('utf-8'))
                return [t['name'] for t in tags]
        except Exception:
            return []

    @staticmethod
    def download_archive(full_name: str, version: str) -> bytes:
        """Download zip archive for a specific tag (version should be like '1.0.0', tag 'v1.0.0' expected)."""
        tag = f"v{version}"
        url = f"https://github.com/{full_name}/archive/refs/tags/{tag}.zip"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return resp.read()

    @staticmethod
    def fetch_elymodule(full_name: str, version: str) -> Optional[Dict]:
        """Download elymodule.json from raw content of a specific version."""
        # Tag name expected 'v{version}'
        raw_url = f"{GITHUB_RAW}/{full_name}/v{version}/elymodule.json"
        try:
            req = urllib.request.Request(raw_url)
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception:
            # Attempt without 'v' prefix if not found
            raw_url = f"{GITHUB_RAW}/{full_name}/{version}/elymodule.json"
            try:
                req = urllib.request.Request(raw_url)
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read().decode('utf-8'))
            except:
                return None

# ----------------------------------------------------------------------
# Lockfile
# ----------------------------------------------------------------------
class Lockfile:
    def __init__(self, project_path: Path):
        self.path = project_path / LOCK_FILE_NAME

    def load(self) -> Dict:
        if not self.path.exists():
            return {"packages": {}}
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except:
            return {"packages": {}}

    def save(self, packages: Dict[str, Dict]):
        data = {"packages": packages}
        self.path.write_text(json.dumps(data, indent=4), encoding='utf-8')

    def get_installed(self) -> Dict[str, str]:
        """Return dict of name -> version installed."""
        lock = self.load()
        result = {}
        for name, info in lock.get('packages', {}).items():
            result[name] = info.get('version', 'unknown')
        return result

# ----------------------------------------------------------------------
# Package Manager Core
# ----------------------------------------------------------------------
class PackageManager:
    def __init__(self):
        self.global_config = self._load_global_config()
        self.project_path = self._resolve_project_path()
        self.language_version = self._get_language_version()
        self.lockfile = Lockfile(self.project_path) if self.project_path else None
        self.fetcher = GitHubFetcher()

    # ---- Global config ----
    def _load_global_config(self):
        if GLOBAL_CONFIG_FILE.exists():
            try:
                return json.loads(GLOBAL_CONFIG_FILE.read_text(encoding='utf-8'))
            except:
                pass
        return {}

    def _save_global_config(self):
        GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        GLOBAL_CONFIG_FILE.write_text(json.dumps(self.global_config, indent=4), encoding='utf-8')

    def _resolve_project_path(self):
        env_project = os.environ.get('ELY_PROJECT')
        if env_project:
            path = Path(env_project).resolve()
            if path.exists():
                return path
        if 'current_project' in self.global_config:
            path = Path(self.global_config['current_project']).resolve()
            if path.exists():
                return path
        cwd = Path.cwd()
        if (cwd / MANAGER_FILE_NAME).exists():
            return cwd
        return None

    def _get_language_version(self):
        script_dir = Path(__file__).parent.resolve()
        version_file = script_dir / VERSION_FILE_NAME
        if version_file.exists():
            return version_file.read_text(encoding='utf-8').strip()
        global_version_file = GLOBAL_CONFIG_DIR / VERSION_FILE_NAME
        if global_version_file.exists():
            return global_version_file.read_text(encoding='utf-8').strip()
        env_version = os.environ.get('ELY_VERSION')
        if env_version:
            return env_version
        return DEFAULT_LANGUAGE_VERSION

    def _ensure_project(self):
        if self.project_path is None:
            print("No project focused and current directory is not an Ely project. "
                  "Use 'elp init' or 'elp project set'.")
            return None
        if not (self.project_path / MANAGER_FILE_NAME).exists():
            print(f"No {MANAGER_FILE_NAME} in {self.project_path}. Please initialize with 'elp init'.")
            return None
        return self.project_path

    # ---- Project commands ----
    def project_set(self, path_str=None):
        if path_str is None:
            path_str = str(Path.cwd())
        abs_path = Path(path_str).resolve()
        if not abs_path.exists():
            print(f"Error: path does not exist: {abs_path}")
            return 1
        if not (abs_path / MANAGER_FILE_NAME).exists():
            print(f"Warning: no {MANAGER_FILE_NAME} found in {abs_path}. Run 'elp init' to create one.")
        self.global_config['current_project'] = str(abs_path)
        self._save_global_config()
        print(f"Project set to {abs_path}")
        return 0

    def project_unset(self):
        if 'current_project' in self.global_config:
            del self.global_config['current_project']
            self._save_global_config()
            print("Project focus cleared.")
        else:
            print("No project was set.")
        return 0

    def project_show(self):
        if self.project_path:
            print(f"Current project: {self.project_path}")
        else:
            print("No project focused.")
        return 0

    def init_project(self, lang_version=None, force=False):
        project_path = self.project_path or Path.cwd()
        if lang_version is None:
            lang_version = self.language_version
        ely_dir = project_path / LOCAL_CONFIG_DIR_NAME
        ely_dir.mkdir(parents=True, exist_ok=True)
        local_config = ely_dir / LOCAL_CONFIG_FILE_NAME
        if local_config.exists() and not force:
            print(f"{local_config} already exists. Use --force to overwrite.")
        else:
            local_config.write_text(json.dumps({
                "compiler": {
                    "version": lang_version
                }
            }, indent=4), encoding='utf-8')
            print(f"Initialized {local_config}")
        manager_file = project_path / MANAGER_FILE_NAME
        if not manager_file.exists() or force:
            default_manager = {
                "name": project_path.name,
                "language": lang_version,
                "dependencies": {},
                "modules": {},
                "enter": "main.ely",
                "output": {
                    "enter": {
                        "name": f"{project_path.name}.exe",
                        "type": "exe"
                    }
                }
            }
            manager_file.write_text(json.dumps(default_manager, indent=4), encoding='utf-8')
            print(f"Created {manager_file}")
        main_ely = project_path / 'main.ely'
        if not main_ely.exists() or force:
            main_ely.write_text('public int func main() {\n    println("Hello from ely!");\n    return 0;\n}\n', encoding='utf-8')
            print(f"Created {main_ely}")
        return 0

    # ---- Install ----
    def install_package(self, package_spec, version=None):
        project_path = self._ensure_project()
        if project_path is None:
            return 1
        name, spec_version = self._parse_spec(package_spec)
        if version is None and spec_version:
            version = spec_version
        # Resolve package source
        repo_info = self._find_package_source(name)
        if not repo_info:
            print(f"Package '{name}' not found.")
            return 1
        full_name = repo_info['full_name']
        # Get available versions
        tags = self.fetcher.get_tags(full_name)
        versions = [t[1:] if t.startswith('v') else t for t in tags if re.match(r'^v?\d+\.\d+\.\d+$', t)]
        if not versions:
            print(f"No semantic version tags found in {full_name}.")
            return 1
        # Determine target version
        req = version if version else '*'
        target = latest_compatible(versions, req, self.language_version,
                                   lambda v: self._check_lang_compat(full_name, v))
        if target is None:
            print(f"No version of '{name}' satisfies {req} and language {self.language_version}.")
            return 1
        # Install recursively
        return self._install_from_github(project_path, name, target, full_name)

    def _install_from_github(self, project_path: Path, name: str, version: str, full_name: str,
                             parent_deps: Dict[str, str] = None) -> int:
        """Download and install a package, then install its dependencies."""
        if parent_deps is None:
            parent_deps = {}
        # Check if already installed (exact version)
        installed = self.lockfile.get_installed() if self.lockfile else {}
        if installed.get(name) == version:
            print(f"Package {name} {version} already installed.")
            return 0
        # Download archive
        print(f"Installing {name} {version}...")
        try:
            archive_bytes = self.fetcher.download_archive(full_name, version)
        except Exception as e:
            print(f"Failed to download {full_name} v{version}: {e}")
            return 1
        # Extract
        modules_dir = project_path / MODULES_DIR_NAME
        modules_dir.mkdir(exist_ok=True)
        dest_dir = modules_dir / f"{name}-{version}"
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir()
        self._extract_zip(archive_bytes, dest_dir)
        # Read elymodule.json to get modules and include list
        elymodule = self._read_json(dest_dir / 'elymodule.json')
        if elymodule:
            # Optionally filter files by 'include' list
            include_files = elymodule.get('include')
            if include_files:
                self._filter_include(dest_dir, include_files)
            modules_map = elymodule.get('modules', {})
        else:
            modules_map = {name: f"src/{name}.ely"}  # default assumption
        # Update manager.json modules
        self._add_modules_to_manager(project_path, name, version, modules_map)
        # Update dependencies in manager.json
        self._update_dependency(project_path, name, f">={version}")
        # Update lockfile
        self._update_lock(name, version, full_name)
        # Now resolve dependencies of this package (if any)
        deps = elymodule.get('dependencies', {}) if elymodule else {}
        for dep_name, dep_ver_range in deps.items():
            if dep_name in parent_deps:
                # Avoid circular dependency issues (basic)
                continue
            # Install dependency recursively, but we must avoid scanning the whole internet every time.
            # We assume dependency name can be resolved via GitHub topics.
            dep_repo = self._find_package_source(dep_name)
            if not dep_repo:
                print(f"Warning: dependency '{dep_name}' of '{name}' not found. Skipping.")
                continue
            dep_full_name = dep_repo['full_name']
            dep_tags = self.fetcher.get_tags(dep_full_name)
            dep_versions = [t[1:] if t.startswith('v') else t for t in dep_tags if re.match(r'^v?\d+\.\d+\.\d+$', t)]
            if not dep_versions:
                print(f"Warning: no versions for dependency '{dep_name}'. Skipping.")
                continue
            dep_target = latest_compatible(dep_versions, dep_ver_range, self.language_version,
                                           lambda v: self._check_lang_compat(dep_full_name, v))
            if dep_target is None:
                print(f"Warning: no compatible version of '{dep_name}' satisfies {dep_ver_range}. Skipping.")
                continue
            self._install_from_github(project_path, dep_name, dep_target, dep_full_name, {**parent_deps, name: version})
        print(f"Installed {name} {version}")
        return 0

    def _check_lang_compat(self, full_name: str, version: str) -> bool:
        """Download elymodule.json and check language_version against project's language version."""
        meta = self.fetcher.fetch_elymodule(full_name, version)
        if meta is None:
            # No metadata, assume compatible
            return True
        lang_req = meta.get('language_version', '*')
        return version_satisfies(lang_req, self.language_version)

    def _extract_zip(self, data: bytes, dest_dir: Path):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.infolist():
                # Strip top-level directory
                parts = Path(member.filename).parts
                if len(parts) <= 1:
                    continue
                stripped = Path(*parts[1:])
                if member.is_dir():
                    (dest_dir / stripped).mkdir(parents=True, exist_ok=True)
                else:
                    (dest_dir / stripped).parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as source, open(dest_dir / stripped, 'wb') as target:
                        shutil.copyfileobj(source, target)

    def _filter_include(self, base_dir: Path, include_list: List[str]):
        """Remove files and directories not in include_list."""
        # Simple implementation: delete everything not matching any prefix in include_list.
        keep = set()
        for inc in include_list:
            # Normalize path
            inc_path = base_dir / inc
            if inc_path.exists():
                if inc_path.is_dir():
                    for f in inc_path.rglob('*'):
                        keep.add(f)
                    keep.add(inc_path)
                else:
                    keep.add(inc_path)
        # Now delete files not in keep
        for item in base_dir.rglob('*'):
            if item not in keep:
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

    def _add_modules_to_manager(self, project_path: Path, name: str, version: str, modules_map: Dict[str, str]):
        manager = self._read_manager(project_path)
        if manager is None:
            return
        prefix = f"{MODULES_DIR_NAME}/{name}-{version}/"
        for mod_name, rel_path in modules_map.items():
            manager.setdefault('modules', {})[mod_name] = prefix + rel_path
        self._write_manager(project_path, manager)

    def _update_dependency(self, project_path: Path, name: str, range_str: str):
        manager = self._read_manager(project_path)
        if manager is not None:
            manager.setdefault('dependencies', {})[name] = range_str
            self._write_manager(project_path, manager)

    def _read_manager(self, project_path: Path) -> Optional[Dict]:
        path = project_path / MANAGER_FILE_NAME
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding='utf-8'))

    def _write_manager(self, project_path: Path, data: Dict):
        path = project_path / MANAGER_FILE_NAME
        path.write_text(json.dumps(data, indent=4), encoding='utf-8')

    def _read_json(self, path: Path) -> Optional[Dict]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except:
            return None

    def _update_lock(self, name: str, version: str, full_name: str):
        if not self.lockfile:
            return
        lock = self.lockfile.load()
        lock['packages'][name] = {
            'version': version,
            'source': f"github:{full_name}",
            'checksum': ''  # we could compute later
        }
        self.lockfile.save(lock['packages'])

    def _find_package_source(self, name: str) -> Optional[Dict]:
        """Search for package: first interpret as user/repo if contains '/', else search GitHub topics."""
        if '/' in name and not name.startswith(('http://', 'https://')):
            parts = name.split('/')
            if len(parts) == 2:
                # Direct user/repo
                return {'full_name': name, 'description': ''}
        # Search by topic
        query = f"topic:{ELYMODULE_TOPICS[0]}+topic:{ELYMODULE_TOPICS[1]}+{name}+in:name"
        repos = self.fetcher.search_repos(query)
        if not repos:
            return None
        # Prefer exact match on repo name
        exact = [r for r in repos if r['name'].lower() == name.lower()]
        repo = exact[0] if exact else repos[0]
        return {'full_name': repo['full_name'], 'description': repo.get('description', '')}

    def _parse_spec(self, spec: str) -> Tuple[str, Optional[str]]:
        if '@' in spec:
            name, ver = spec.split('@', 1)
            return name, ver
        return spec, None

    # ---- Remove ----
    def remove_package(self, name: str, autoremove: bool = False):
        project_path = self._ensure_project()
        if project_path is None:
            return 1
        manager = self._read_manager(project_path)
        if not manager or name not in manager.get('dependencies', {}):
            print(f"Package '{name}' is not listed as a dependency.")
            return 1
        # Find installed version
        lock = self.lockfile.load() if self.lockfile else {'packages': {}}
        installed = lock['packages'].get(name)
        if not installed:
            print(f"No installed version found for '{name}'.")
            return 1
        version = installed['version']
        pkg_dir = project_path / MODULES_DIR_NAME / f"{name}-{version}"
        if pkg_dir.exists():
            shutil.rmtree(pkg_dir)
        # Remove modules entries pointing to this package
        modules = manager.get('modules', {})
        prefix = f"{MODULES_DIR_NAME}/{name}-{version}/"
        to_remove = [k for k, v in modules.items() if v.startswith(prefix)]
        for k in to_remove:
            del modules[k]
        # Remove dependency entry
        del manager['dependencies'][name]
        # Remove from lock
        if name in lock['packages']:
            del lock['packages'][name]
            self.lockfile.save(lock['packages'])
        # Write back manager
        self._write_manager(project_path, manager)
        print(f"Removed {name} {version}")
        # Optionally autoremove orphan dependencies
        if autoremove:
            self._autoremove_orphans(project_path)
        return 0

    def _autoremove_orphans(self, project_path: Path):
        """Remove packages that are not direct dependencies and not required by others."""
        manager = self._read_manager(project_path)
        if not manager:
            return
        deps = manager.get('dependencies', {})
        lock = self.lockfile.load() if self.lockfile else {'packages': {}}
        installed = set(lock['packages'].keys())
        # Determine which are needed by direct dependencies (simple version: keep all direct)
        # In a more complex system we'd build a dependency graph. For now we just keep direct dependencies.
        direct = set(deps.keys())
        orphans = installed - direct
        for orphan in orphans:
            print(f"Removing orphaned package {orphan}...")
            self.remove_package(orphan, autoremove=False)

    # ---- Clean ----
    def clean_packages(self, purge: bool = False, keep_deps: bool = False):
        project_path = self._ensure_project()
        if project_path is None:
            return 1
        modules_dir = project_path / MODULES_DIR_NAME
        if modules_dir.exists():
            shutil.rmtree(modules_dir)
            print(f"Removed {MODULES_DIR_NAME}/")
        # Handle lockfile
        lock_path = project_path / LOCK_FILE_NAME
        if lock_path.exists():
            lock_path.unlink()
            print("Removed ely.lock")
        # Update manager.json
        manager = self._read_manager(project_path)
        if manager:
            # Remove all modules that were pointing to modules/
            mods = manager.get('modules', {})
            mods_to_keep = {k: v for k, v in mods.items() if not v.startswith(MODULES_DIR_NAME + '/')}
            manager['modules'] = mods_to_keep
            if purge:
                manager['dependencies'] = {}
                print("Cleared dependencies.")
            elif keep_deps:
                # Leave dependencies as is
                pass
            else:
                # Default: clear dependencies (since packages are removed)
                manager['dependencies'] = {}
            self._write_manager(project_path, manager)
        print("Clean complete.")
        return 0

    # ---- List ----
    def list_packages(self):
        project_path = self._ensure_project()
        if project_path is None:
            return 1
        lock = self.lockfile.load() if self.lockfile else {'packages': {}}
        if not lock['packages']:
            print("No packages installed.")
        else:
            for name, info in lock['packages'].items():
                print(f"{name} {info.get('version', '?')} (source: {info.get('source', 'unknown')})")
        return 0

    # ---- Search ----
    def search_packages(self, query: str):
        q = f"topic:{ELYMODULE_TOPICS[0]}+topic:{ELYMODULE_TOPICS[1]}+{query}+in:name"
        repos = self.fetcher.search_repos(q)
        if not repos:
            print("No packages found.")
        else:
            for repo in repos:
                print(f"{repo['full_name']} - {repo.get('description', '')} (stars: {repo['stargazers_count']})")
        return 0

    # ---- Info ----
    def info_package(self, name: str):
        # Try to find locally first
        project_path = self._ensure_project()
        local_info = None
        if project_path:
            lock = self.lockfile.load() if self.lockfile else {'packages': {}}
            installed = lock['packages'].get(name)
            if installed:
                version = installed['version']
                pkg_dir = project_path / MODULES_DIR_NAME / f"{name}-{version}"
                elymodule = self._read_json(pkg_dir / 'elymodule.json')
                if elymodule:
                    local_info = elymodule
        if local_info:
            print(json.dumps(local_info, indent=4))
        else:
            # Try to fetch from GitHub by searching
            repo = self._find_package_source(name)
            if not repo:
                print(f"Package '{name}' not found.")
                return 1
            full_name = repo['full_name']
            # Get latest version tag and read its elymodule.json
            tags = self.fetcher.get_tags(full_name)
            versions = [t[1:] if t.startswith('v') else t for t in tags if re.match(r'^v?\d+\.\d+\.\d+$', t)]
            if not versions:
                print(f"No version tags found for {full_name}")
                return 1
            latest = sorted(versions, key=semver_tuple)[-1]
            meta = self.fetcher.fetch_elymodule(full_name, latest)
            if meta:
                print(json.dumps(meta, indent=4))
            else:
                print(f"No elymodule.json found in {full_name}")
        return 0

    # ---- Update ----
    def update_package(self, name: Optional[str] = None):
        project_path = self._ensure_project()
        if project_path is None:
            return 1
        lock = self.lockfile.load() if self.lockfile else {'packages': {}}
        installed = lock['packages']
        if not installed:
            print("No packages installed.")
            return 0
        if name:
            if name not in installed:
                print(f"Package '{name}' is not installed.")
                return 1
            return self._update_single(project_path, name, installed[name])
        else:
            for pkg in list(installed.keys()):
                self._update_single(project_path, pkg, installed[pkg])
            return 0

    def _update_single(self, project_path: Path, name: str, current_info: Dict):
        current_version = current_info['version']
        source = current_info.get('source', '')
        if source.startswith('github:'):
            full_name = source[7:]  # strip 'github:'
        else:
            # Need to find source by searching
            repo = self._find_package_source(name)
            if not repo:
                print(f"Cannot determine source for {name}")
                return 1
            full_name = repo['full_name']
        # Get available versions
        tags = self.fetcher.get_tags(full_name)
        versions = [t[1:] if t.startswith('v') else t for t in tags if re.match(r'^v?\d+\.\d+\.\d+$', t)]
        if not versions:
            print(f"No versions available for {name}")
            return 1
        # Determine what version range is allowed (from manager.json dependencies)
        manager = self._read_manager(project_path)
        dep_range = manager.get('dependencies', {}).get(name, '*') if manager else '*'
        target = latest_compatible(versions, dep_range, self.language_version,
                                   lambda v: self._check_lang_compat(full_name, v))
        if target is None:
            print(f"No compatible update for {name}.")
            return 0
        if target == current_version:
            print(f"{name} is already up-to-date ({current_version}).")
            return 0
        print(f"Updating {name} from {current_version} to {target}...")
        # Remove old version
        old_dir = project_path / MODULES_DIR_NAME / f"{name}-{current_version}"
        if old_dir.exists():
            shutil.rmtree(old_dir)
        # Install new version
        return self._install_from_github(project_path, name, target, full_name)

# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(prog='elp', description='Ely Language Package Manager')
    sub = parser.add_subparsers(dest='command', help='Commands')

    # init
    init_p = sub.add_parser('init', help='Initialize a new Ely project')
    init_p.add_argument('--lang', help='Language version')
    init_p.add_argument('--force', action='store_true', help='Overwrite existing files')

    # project
    proj_p = sub.add_parser('project', help='Manage project focus')
    proj_sub = proj_p.add_subparsers(dest='project_command')
    proj_set = proj_sub.add_parser('set', help='Set current project path')
    proj_set.add_argument('path', nargs='?', default=None, help='Path to project (default current directory)')
    proj_sub.add_parser('unset', help='Clear project focus')
    proj_sub.add_parser('show', help='Show current project path')

    # install
    inst_p = sub.add_parser('install', help='Install a package')
    inst_p.add_argument('package', help='Package name, user/repo, or URL')
    inst_p.add_argument('--version', help='Specific version')

    # remove
    rem_p = sub.add_parser('remove', help='Remove a package')
    rem_p.add_argument('package', help='Package name')
    rem_p.add_argument('--autoremove', action='store_true', help='Also remove orphan dependencies')

    # update
    upd_p = sub.add_parser('update', help='Update packages')
    upd_p.add_argument('package', nargs='?', default=None, help='Package name (omit to update all)')

    # clean
    clean_p = sub.add_parser('clean', help='Remove all installed packages')
    clean_p.add_argument('--purge', action='store_true', help='Also clear dependencies')
    clean_p.add_argument('--keep-deps', action='store_true', help='Keep dependencies in manager.json')

    # list
    sub.add_parser('list', help='List installed packages')

    # search
    search_p = sub.add_parser('search', help='Search packages on GitHub')
    search_p.add_argument('query', help='Search query')

    # info
    info_p = sub.add_parser('info', help='Show package details')
    info_p.add_argument('package', help='Package name')

    args = parser.parse_args()
    mgr = PackageManager()

    if args.command == 'init':
        return mgr.init_project(args.lang, args.force)
    elif args.command == 'project':
        if args.project_command == 'set':
            return mgr.project_set(args.path)
        elif args.project_command == 'unset':
            return mgr.project_unset()
        elif args.project_command == 'show':
            return mgr.project_show()
        else:
            parser.parse_args(['project', '--help'])
            return 1
    elif args.command == 'install':
        return mgr.install_package(args.package, args.version)
    elif args.command == 'remove':
        return mgr.remove_package(args.package, autoremove=args.autoremove)
    elif args.command == 'update':
        return mgr.update_package(args.package)
    elif args.command == 'clean':
        return mgr.clean_packages(purge=args.purge, keep_deps=args.keep_deps)
    elif args.command == 'list':
        return mgr.list_packages()
    elif args.command == 'search':
        return mgr.search_packages(args.query)
    elif args.command == 'info':
        return mgr.info_package(args.package)
    else:
        parser.print_help()
        return 1

if __name__ == '__main__':
    sys.exit(main())