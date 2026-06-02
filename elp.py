#!/usr/bin/env python3
"""ELY Language Package Manager CLI."""

import sys
import argparse
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from package_manager import PackageManager


def main():
    parser = argparse.ArgumentParser(prog='elp', description='Ely Language Package Manager')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # elp init [name]
    init_parser = subparsers.add_parser('init', help='Initialise a new elymodule.json')
    init_parser.add_argument('name', nargs='?', default='my-module', help='Module name')

    # elp install <pkg>
    install_parser = subparsers.add_parser('install', help='Install a package')
    install_parser.add_argument('package', help='Package name (from stdmodules) or user/repo (from GitHub)')

    # elp install-local <path>
    local_parser = subparsers.add_parser('install-local', help='Install a package from a local directory')
    local_parser.add_argument('path', help='Path to the package directory')

    # elp remove <pkg>
    remove_parser = subparsers.add_parser('remove', help='Remove an installed package')
    remove_parser.add_argument('package', help='Package name')

    # elp update
    update_parser = subparsers.add_parser('update', help='Update all installed packages')

    # elp list
    list_parser = subparsers.add_parser('list', help='List installed packages')

    # elp list-available
    avail_parser = subparsers.add_parser('list-available', help='List available standard modules')

    # elp pack [--output <file>]
    pack_parser = subparsers.add_parser('pack', help='Pack project into .elypkg archive')
    pack_parser.add_argument('--output', help='Output archive name')

    # elp publish
    publish_parser = subparsers.add_parser('publish', help='Publish package to local registry')

    # elp info <pkg>
    info_parser = subparsers.add_parser('info', help='Show package metadata')
    info_parser.add_argument('package', help='Package name')

    # elp build [path]
    build_parser = subparsers.add_parser('build', help='Build project')
    build_parser.add_argument('path', nargs='?', default=None, help='Path to project directory or manager.json')
    build_parser.add_argument('--force', action='store_true', help='Force full rebuild')

    # elp run [path]
    run_parser = subparsers.add_parser('run', help='Build and run project')
    run_parser.add_argument('path', nargs='?', default=None, help='Path to project directory or manager.json')
    run_parser.add_argument('--args', nargs='+', default=[], help='Arguments for the executable')

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    pm = PackageManager()

    if args.command == 'init':
        return 0 if pm.init(args.name) else 1
    elif args.command == 'install':
        return 0 if pm.install(args.package) else 1
    elif args.command == 'install-local':
        return 0 if pm.install_local(args.path) else 1
    elif args.command == 'remove':
        return 0 if pm.remove(args.package) else 1
    elif args.command == 'update':
        return 0 if pm.update() else 1
    elif args.command == 'list':
        packages = pm.list_packages()
        if packages:
            print("Installed packages:")
            for pkg in packages:
                print(f"  {pkg['name']}@{pkg['version']} - {pkg['description']}")
        else:
            print("No packages installed.")
        return 0
    elif args.command == 'list-available':
        modules = pm.list_available()
        if modules:
            print("Available standard modules:")
            for mod in modules:
                print(f"  {mod['name']}@{mod['version']} - {mod['description']}")
        else:
            print("No standard modules available.")
        return 0
    elif args.command == 'pack':
        return 0 if pm.pack(args.output) else 1
    elif args.command == 'publish':
        return 0 if pm.publish() else 1
    elif args.command == 'info':
        info = pm.info(args.package)
        if info:
            import json
            print(json.dumps(info, indent=4))
        else:
            print(f"Package '{args.package}' not found.")
            return 1
        return 0
    elif args.command == 'build':
        # Delegate to ebt's build
        from builder import ProjectBuilder
        from package_manager import PackageManager as PM
        project_path = PM.resolve_manager_path(args.path)
        if not project_path:
            print("No manager.json found. Are you in an Ely project?")
            return 1

        builder_obj = ProjectBuilder(project_path)
        builder_obj.optimization = 'hard'
        builder_obj.force_rebuild = args.force
        success = builder_obj.build()
        return 0 if success else 1
    elif args.command == 'run':
        import subprocess
        from builder import ProjectBuilder
        from package_manager import PackageManager as PM
        project_path = PM.resolve_manager_path(args.path)
        if not project_path:
            print("No manager.json found. Are you in an Ely project?")
            return 1

        builder_obj = ProjectBuilder(project_path)
        builder_obj.optimization = 'hard'
        success = builder_obj.build()
        if not success:
            return 1
        exe = builder_obj.output_name
        if not exe or not Path(exe).is_file():
            print(f"Executable not found: {exe}")
            return 1
        return subprocess.call([exe] + args.args)

    return 0


if __name__ == '__main__':
    sys.exit(main())