# ELP

<a href="https://ely-language.github.io/">Site</a>

**ELP — Ely Language Package Manager. The official package manager for the Ely programming language, included with ElySpruce since version 26.5.**

## Overview
ELP manages modules and dependencies for Ely projects. It handles installation, removal, updating, and publishing of packages from both local directories and remote GitHub repositories.

## Commands
| Command | Description |
|---------|-------------|
| `elp init [name]` | Create a new elymodule.json + src/ scaffold |
| `elp install <pkg>[@ver]` | Install a package from stdmodules or GitHub |
| `elp install-local <path>` | Install a package from a local directory |
| `elp remove <pkg>` | Remove an installed package |
| `elp update` | Update all dependencies |
| `elp list` | List installed packages |
| `elp list-available` | List available standard modules (stdmodules/) |
| `elp pack [--output <file>]` | Pack project into .elypkg archive |
| `elp publish` | Publish to local registry |
| `elp info <pkg>` | Show package metadata |
| `elp build [path]` | Build project (path = directory or manager.json) |
| `elp run [path]` | Build & run project |

## How It Works
ELP integrates with the Ely build system (ebt). When you run `elp install`, the package manager:

 - Resolves the package from `stdmodules/` (built-in standard library) or a GitHub repository
 - Downloads and builds the package as a static library (.lib / .a)
 - Generates `link.ely` — a wrapper that exposes the module's public API to your Ely code
 - Copies compiled artifacts into `ex_prj/modules/` for direct use in your project
 - Updates `manager.json` with the module's path automatically

## Manager.json Integration
ELP reads and writes the `modules` field in your project's `manager.json`:
```json
{
    "name": "my_project",
    "enter": "main.ely",
    "modules": {
        "file": "modules/file/elymodule.json",
        "json": "modules/json/elymodule.json"
    }
}
```
Each module path points to an `elymodule.json` that describes the module's API, link file, and included assets.

## Example
```cpp
// Install the file module
elp install file

// Use it in your Ely code
using "modules/file/link.ely";

File f = File("test.txt");
f.setContent("Hello from ELP!");
println(f.getContent());
```

## Notes
 - ELP is a core component of ElySpruce and is required for compilation since version 26.5
 - Standard modules are located in `stdmodules/` and are always available offline
 - Remote packages are fetched via GitHub and cached locally
 - Module compilation produces static libraries for maximum performance
 - The package manager automatically resolves transitive dependencies

## Quick Start
 - Ensure ElySpruce 26.5+ is installed
 - Navigate to your Ely project directory (with a `manager.json`)
 - Run `elp install file` to add the file module
 - Add `using "modules/file/link.ely";` to your `.ely` source
 - Build and run with `elp run`

*Made in Russia. Inspired for humanity.*
**Have fun!**