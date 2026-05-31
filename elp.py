#!/usr/bin/env python3
"""
elp — Ely Language Package Manager

Usage:
    elp init [name]             Create a new elymodule.json + src/ scaffold
    elp install <pkg>[@ver]     Install a package from stdmodules or GitHub
    elp install-local <path>    Install a package from a local directory
    elp remove <pkg>            Remove an installed package
    elp update                  Update all dependencies
    elp list                    List installed packages
    elp list-available          List available standard modules (stdmodules/)
    elp pack [--output <file>]  Pack project into .elypkg archive
    elp publish                 Publish to local registry
    elp info <pkg>              Show package metadata
    elp build [path]            Build project (path = directory or manager.json)
    elp run [path]              Build & run project
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Ensure we can import from the parent directory (EasyBoots root)
sys.path.insert(0, str(Path(__file__).parent.parent))
from package_manager import PackageManager


def _resolve_and_build(mgr: PackageManager, path: str | None, run_after: bool = False) -> int:
    """
    Resolve manager.json from path, then build (and optionally run).

    Path can be:
      - None          → look for manager.json in cwd
      - directory     → look for manager.json inside it
      - manager.json  → use directly
    """
    manager_path = mgr.resolve_manager_path(path)
    if not manager_path:
        if path:
            print(f"No manager.json found in: {path}")
        else:
            print("No manager.json found in current directory.")
        return 1

    project_dir = manager_path.parent
    ebt_path = Path(__file__).parent.parent / 'ebt.py'

    if run_after:
        cmd = [sys.executable, str(ebt_path), 'run', str(manager_path)]
    else:
        cmd = [sys.executable, str(ebt_path), 'build', str(manager_path)]

    print(f"Project: {project_dir}")
    print(f"Running: {' '.join(cmd)}")
    print()

    result = subprocess.run(cmd, cwd=str(project_dir))
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        prog='elp',
        description='Ely Language Package Manager'
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # elp init [name]
    init_parser = subparsers.add_parser('init', help='Initialise a new module')
    init_parser.add_argument('name', nargs='?', default='my-module',
                             help='Module name (default: my-module)')

    # elp install <pkg>[@ver]
    install_parser = subparsers.add_parser('install', help='Install a package from stdmodules or GitHub')
    install_parser.add_argument('package', help='Package name (e.g. "file", "json") or "user/repo" for GitHub')

    # elp install-local <path>
    local_parser = subparsers.add_parser('install-local', help='Install a package from a local directory')
    local_parser.add_argument('path', help='Path to package directory (contains elymodule.json)')

    # elp remove <pkg>
    remove_parser = subparsers.add_parser('remove', help='Remove an installed package')
    remove_parser.add_argument('package', help='Package name to remove')

    # elp update
    update_parser = subparsers.add_parser('update', help='Update all dependencies')

    # elp list
    list_parser = subparsers.add_parser('list', help='List installed packages')

    # elp list-available
    list_avail_parser = subparsers.add_parser('list-available', help='List available standard modules')

    # elp pack [--output <file>]
    pack_parser = subparsers.add_parser('pack', help='Pack project into .elypkg archive')
    pack_parser.add_argument('--output', '-o', help='Output archive name')

    # elp publish
    publish_parser = subparsers.add_parser('publish', help='Publish to local registry')

    # elp info <pkg>
    info_parser = subparsers.add_parser('info', help='Show package metadata')
    info_parser.add_argument('package', help='Package name')

    # elp build [path]
    build_parser = subparsers.add_parser('build', help='Build project (path = directory or manager.json)')
    build_parser.add_argument('path', nargs='?', default=None,
                              help='Path to project directory or manager.json (default: cwd)')

    # elp run [path]
    run_parser = subparsers.add_parser('run', help='Build & run project')
    run_parser.add_argument('path', nargs='?', default=None,
                            help='Path to project directory or manager.json (default: cwd)')

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    mgr = PackageManager()

    if args.command == 'init':
        return 0 if mgr.init(args.name) else 1

    elif args.command == 'install':
        return 0 if mgr.install(args.package) else 1

    elif args.command == 'install-local':
        return 0 if mgr.install_local(args.path) else 1

    elif args.command == 'remove':
        return 0 if mgr.remove(args.package) else 1

    elif args.command == 'update':
        return 0 if mgr.update() else 1

    elif args.command == 'list':
        packages = mgr.list_packages()
        if packages:
            print(f"\nInstalled packages ({len(packages)}):")
            print(f"{'Name':<20} {'Version':<12} Description")
            print("-" * 60)
            for pkg in packages:
                print(f"{pkg['name']:<20} {pkg['version']:<12} {pkg['description']}")
        else:
            print("No packages installed.")
        return 0

    elif args.command == 'list-available':
        modules = mgr.list_available()
        if modules:
            print(f"\nAvailable standard modules ({len(modules)}):")
            print(f"{'Name':<20} {'Version':<12} Description")
            print("-" * 60)
            for m in modules:
                print(f"{m['name']:<20} {m['version']:<12} {m['description']}")
        else:
            print("No standard modules found.")
        return 0

    elif args.command == 'pack':
        return 0 if mgr.pack(args.output) else 1

    elif args.command == 'publish':
        return 0 if mgr.publish() else 1

    elif args.command == 'info':
        meta = mgr.info(args.package)
        if meta:
            print(json.dumps(meta, indent=4))
            return 0
        else:
            print(f"Package '{args.package}' not found.")
            return 1

    elif args.command == 'build':
        return _resolve_and_build(mgr, args.path, run_after=False)

    elif args.command == 'run':
        return _resolve_and_build(mgr, args.path, run_after=True)

    return 0


if __name__ == '__main__':
    sys.exit(main())