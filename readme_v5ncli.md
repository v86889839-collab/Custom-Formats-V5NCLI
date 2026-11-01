# Custom File Formats Toolkit V5NCLI

A collection of custom file formats and Python parsers/generators for automation, Discord bots, system utilities, and Roblox server management.

**V5NCLI** = V5, **N**o **CLI**. Version 5 is built on the V3 codebase: library-only, no command-line interface. Three core principles unchanged: **Performance, Lightweight, Speed** — now with custom exception types and LuaJIT acceleration built in.

## What's New in V5NCLI

- **Bug fixes** — 6 fixes over V3/V4:
  - C++ files are no longer misdetected as C (signature order fixed: `cpp` before `c`)
  - `.luna` merges duplicate sections instead of overwriting (`setdefault` merge)
  - `create_stxt` no longer mutates the caller's dicts (`get` instead of `pop`)
  - `process_unkn_folder` raises a clear `CFNotADirectoryError` instead of crashing on `iterdir()`
  - `run_pyru` compiles and launches the Rust binary correctly on Windows (`.exe` suffix)
  - All subprocess timeouts are wrapped: `TimeoutExpired` becomes a `CFTimeoutError`, not a crash
- **+13 new formats** — see table below
- **More speed via LuaJIT** — `luajit_available()`, `luajit_eval()`, `luajit_lines()` for batch line processing
- **Custom exceptions** — every toolkit error is a `CFError` subclass with `[V5NCLI]` prefix and a `format_name` field; all inherit from built-in exceptions, so existing `except FileNotFoundError` handlers keep working
- **CLI removed** — pure importable library, matching the V3 philosophy
- **`.ptbl` and `.mmsg` removed** — deprecated and deleted
- **`.conn` kept** — merged-file container carried over from V4
- **30 formats total** — 16 legacy, `.conn`, and 13 new

## What Do I Need to Download?

- Python 3.9+ (required — nothing works without it)
- `rustc` — only for `.pyru` (compiling Rust blocks)
- `luajit` — for `.lujit` and the LuaJIT speed functions
- `lua` — optional fallback if luajit is not installed

All Python dependencies are in the standard library — no pip install needed.

## Custom Exceptions

Every error raised by the toolkit is predictable, prefixed, and introspectable. Users see `[V5NCLI]` in the message and immediately know the toolkit — not their code — is complaining.

| Exception       | Inherits            | Raised when                                        |
|-----------------|---------------------|----------------------------------------------------|
| `CFError`           | `Exception`         | Base class — catch this to catch everything toolkit-related |
| `CFFileNotFoundError` | `FileNotFoundError` | A format file does not exist                    |
| `CFNotADirectoryError` | `NotADirectoryError` | Expected a folder, got a file or invalid path  |
| `CFValueError`      | `ValueError`        | Invalid argument (e.g. unknown list name in `.wlist`) |
| `CFFormatError`     | `ValueError`        | Corrupted or malformed format content              |
| `CFTimeoutError`    | `TimeoutError`      | A subprocess exceeded its timeout                  |

```python
from custom_formats_v5 import LunaConfig, CFError, CFFileNotFoundError

try:
    cfg = LunaConfig.load("missing.luna")
except CFFileNotFoundError as e:
    print(e)                    # [V5NCLI] file not found: missing.luna (format: .luna)
    print(e.format_name)        # ".luna"

# Backwards compatible — still a FileNotFoundError:
try:
    LunaConfig.load("missing.luna")
except FileNotFoundError:
    print("old handlers still work")
```

## Formats Overview (30 total)

| Format | Purpose | Typical Use Case |
|---|---|---|
| `.luna`  | Lightweight config with sections | Bot configs (prefixes, roles, settings) |
| `.unkn`  | Auto-detected code placeholder    | Plugin system, dynamic code delivery |
| `.pyru`  | Hybrid Python/Rust script         | Multi-language command modules |
| `.lujit` | LuaJIT-ready scripts              | In-game logic, fast scripting for Roblox/bots |
| `.acf`   | Application Critical Manifest (JSON) | Integrity checks, version control, admin flags |
| `.ctxt`  | Critical Text with SHA-256 signature | Signed rules, moderation policies, verified configs |
| `.stxt`  | Structured text commands          | Bot command batching, moderation actions |
| `.cmdx`  | Batch commands with priorities    | Task execution, system automation |
| `.rmap`  | Roblox map metadata               | Server map config, mini-game selection, role requirements |
| `.qset`  | Quarantine set manifest           | File quarantine with SHA-256 dedup |
| `.tcfg`  | Task config (scheduler)           | Scheduled scans, cleanups, backups |
| `.vset`  | Visual settings (JSON)            | UI themes, colors, fonts |
| `.gcfg`  | Game config for Roblox mini-games | Toggle games, set rewards, UI colors, sound volume |
| `.skey`  | Secret key manifest               | API tokens, Discord bot tokens, access flags |
| `.pfx`   | Plugin flex manifest              | Dynamic plugin loading for ReBoot/LUnlocker |
| `.ulog`  | Ultra-light log                   | Maximum-speed event logging without timestamps |
| `.conn`  | Merged file container             | Combine multiple files into one |
| `.mcfg`  | Toolkit master config             | Global toolkit settings: safe mode, timeouts, defaults |
| `.alog`  | Log with timestamps and levels    | `ts\|level\|event` lines, time-range queries |
| `.envk`  | Encrypted env store               | XOR+base64 obfuscated environment variables |
| `.bakq`  | Backup queue                      | Hash-deduplicated backups, restore latest version |
| `.snap`  | Directory snapshot                | SHA-256 snapshot of a folder, diff between snapshots |
| `.tmpl`  | Text template                     | `{{placeholder}}` rendering, missing-key detection |
| `.hmap`  | Persistent key-value store        | Simple on-disk hashmap for small datasets |
| `.tq`    | Priority task queue               | Push/pop tasks with priorities, persisted on disk |
| `.lck`   | File lock with TTL                | Cross-process locks, stale locks auto-expire |
| `.hook`  | Event hooks                       | Register commands on events, fire with context |
| `.cache` | TTL cache                         | Persistent cache with lazy expiration |
| `.wlist` | White/black lists                 | `allow` / `deny` / `None` check for users, paths, IPs |
| `.rpt`   | Report document                   | Sections, render, save/load for scan results |

## Quick Start

```bash
git clone https://github.com/v86889839-collab/Custom-Formats.git
cd Custom-Formats
```

No CLI in V5NCLI — import the module:

```python
import asyncio
from custom_formats_v5 import (
    LunaConfig, process_unkn_folder, save_ctxt, load_ctxt,
    save_mcfg, load_mcfg, log_alog, wlist_check,
    luajit_available, luajit_eval,
)

# Config
cfg = LunaConfig.load("config.luna")
print(cfg.get("bot", "name"))

# Language detection (parallel)
process_unkn_folder("./uploads/", max_workers=8)

# Signed critical text
save_ctxt("rules.ctxt", "Moderation rules...", critical_level="high")
print(load_ctxt("rules.ctxt")["valid"])

# Toolkit master config
save_mcfg("toolkit.mcfg", {"safe_mode": True, "max_time_sec": 10})
print(load_mcfg("toolkit.mcfg"))

# Timestamped log
log_alog("events.alog", "scan_start", level="INFO")

# LuaJIT acceleration (optional runtime)
print(luajit_available())          # True if luajit is on PATH
print(luajit_eval("return 2 + 2")) # "4"
```

## Performance Features

### Pre-compiled Regex

```python
from custom_formats_v5 import detect_language
lang = detect_language(content)  # uses pre-compiled patterns, cpp checked before c
```

### SHA-256 Cache

```python
from custom_formats_v5 import _sha256_cached
sha = _sha256_cached(Path("app.exe"))  # cached by (path, mtime)
```

### Parallel `.unkn` Processing

```python
from custom_formats_v5 import process_unkn_folder
results = process_unkn_folder("./uploads/", max_workers=8)
```

### LuaJIT Acceleration

```python
from custom_formats_v5 import luajit_available, luajit_eval, luajit_lines

if luajit_available():
    out = luajit_eval("return string.upper('hello')")     # "HELLO"
    lines = luajit_lines(["a.txt", "b.txt", "c.txt"],
                         lua_body="for _, l in ipairs(...) do ... end")
```

Code is passed to LuaJIT via stdin — no temp files, no external dependencies. If LuaJIT is missing, functions return an honest `runner="none"` result; fallback logic stays with the caller.

### Async Wrappers

```python
from custom_formats_v5 import process_unkn_folder_async, parse_stxt_async

async def main():
    results = await process_unkn_folder_async("./uploads/")
    commands = await parse_stxt_async("actions.stxt")

asyncio.run(main())
```

### Benchmark

```python
from custom_formats_v5 import benchmark, process_unkn_folder
result = benchmark(process_unkn_folder, "./uploads/", runs=10)
print(f"Avg: {result['avg']:.4f}s, Min: {result['min']:.4f}s")
```

## Format Details

### Legacy formats (unchanged API from V3)

Usage is identical to V3 — `LunaConfig`, `process_unkn`, `create_pyru`/`run_pyru`, `run_lujit`, `AcfManifest`, `save_ctxt`/`load_ctxt`, `parse_stxt`/`execute_stxt`, `parse_cmdx`/`run_cmdx`, `RmapConfig`, `load_qset`/`add_to_qset`, `parse_tcfg`/`check_and_run_tcfg`, `VsetConfig`, `load_gcfg`/`get_game_config`, `SkeyManifest`, `PfxManifest`, `log_ulog`/`tail_ulog`. See the V3-era examples — they still apply verbatim.

### `.conn` — Merged File Container (from V4)

```python
from custom_formats_v5 import connect_files, clear_conn_format

connect_files(["script1.py", "script2.py"], "merged.conn")
clear_conn_format("merged.conn")   # strips the extension back off
```

Each source is stored with a `# === SOURCE: name ===` header and `# === END SOURCE ===` footer.

### New in V5NCLI

#### `.mcfg` — Toolkit Master Config

```python
from custom_formats_v5 import save_mcfg, load_mcfg, mcfg_get

save_mcfg("toolkit.mcfg", {"safe_mode": True, "max_time_sec": 10})
cfg = load_mcfg("toolkit.mcfg")
```

#### `.alog` — Timestamped Log

```python
from custom_formats_v5 import log_alog, alog_since

log_alog("events.alog", "scan_start", level="INFO")
log_alog("events.alog", "quarantine_ok", level="OK")
recent = alog_since("events.alog", since_ts=1700000000)  # events after timestamp
```

#### `.envk` — Encrypted Env Store

```python
from custom_formats_v5 import save_envk, load_envk

save_envk(".envk", {"DISCORD_TOKEN": "..."}, key="my-secret")
data = load_envk(".envk", key="my-secret")
```

#### `.bakq` — Backup Queue

```python
from custom_formats_v5 import backup_file, restore_latest

backup_file("config.luna", backup_dir="./backups", queue_path="backups.bakq")
restore_latest("config.luna", queue_path="backups.bakq")  # rolls back to last version
```

Hash-deduplicated: a file is only queued when its content actually changed.

#### `.snap` — Directory Snapshot & Diff

```python
from custom_formats_v5 import create_snap, diff_snap

create_snap("./project", "before.snap")
# ... files changed ...
create_snap("./project", "after.snap")
d = diff_snap("before.snap", "after.snap")
print(d["added"], d["removed"], d["changed"])
```

#### `.tmpl` — Templates

```python
from custom_formats_v5 import render_tmpl, find_missing_keys

text = render_tmpl("welcome.tmpl", {"user": "Rimmw123"})
missing = find_missing_keys("welcome.tmpl", {"user": "Rimmw123"})  # unfilled keys
```

#### `.hmap` — Persistent Key-Value

```python
from custom_formats_v5 import hmap_set, hmap_get, hmap_del, hmap_keys

hmap_set("state.hmap", "last_scan", "2026-11-01")
hmap_get("state.hmap", "last_scan")
```

#### `.tq` — Priority Task Queue

```python
from custom_formats_v5 import tq_push, tq_pop, tq_peek, tq_len

tq_push("jobs.tq", "scan_temp", priority=1)
task = tq_pop("jobs.tq")   # highest priority first
```

#### `.lck` — File Lock with TTL

```python
from custom_formats_v5 import acquire_lock, release_lock

if acquire_lock("scan.lck", owner="lunlocker", ttl=60):
    # ... critical section ...
    release_lock("scan.lck", owner="lunlocker")
# Stale locks (expired TTL) are treated as free.
```

#### `.hook` — Event Hooks

```python
from custom_formats_v5 import add_hook, fire_hooks

add_hook("hooks.hook", "on_scan_complete", "notify --channel {{channel}}")
fire_hooks("hooks.hook", "on_scan_complete", {"channel": "mod-log"})
```

Commands support `{{placeholder}}` substitution via the `.tmpl` engine.

#### `.cache` — TTL Cache

```python
from custom_formats_v5 import cache_set, cache_get

cache_set("app.cache", "rate_limit", 42, ttl=300)
cache_get("app.cache", "rate_limit")   # 42, or None after TTL
```

Expired entries are cleaned lazily on read.

#### `.wlist` — White/Black Lists

```python
from custom_formats_v5 import wlist_add, wlist_check

wlist_add("access.wlist", "whitelist", "123456789012345678")
wlist_add("access.wlist", "blacklist", "987654321098765432")

verdict = wlist_check("access.wlist", "123456789012345678")  # "allow"
verdict = wlist_check("access.wlist", "999999999999999999")  # None
```

#### `.rpt` — Report

```python
from custom_formats_v5 import rpt_add_section, render_rpt, save_rpt

rpt = rpt_add_section({}, "Scan Results", ["3 files checked", "1 quarantined"])
save_rpt(rpt, "weekly.rpt")
print(render_rpt(rpt))
```

## Integration Guide

### For ReBoot (Discord Bot)

| Format | How to integrate |
|---|---|
| `.luna` | Load config.luna on bot startup to set prefix, roles, limits |
| `.unkn` | `!auto-fix` scans uploads/ in parallel, renames files by language |
| `.stxt` | `!run-script` reads .stxt and executes commands in sequence |
| `.ctxt` | Load signed moderation rules on startup, reject if signature invalid |
| `.wlist` | Whitelist/blacklist checks for commands and users |
| `.gcfg` | Toggle mini-games, set rewards, UI colors, sound volume |
| `.skey` | Load bot tokens and API keys at startup with validation |
| `.pfx` | Dynamic plugin loading with permission checks |
| `.alog` | Timestamped audit log for bot actions |
| `.tq` | Queue user-submitted jobs with priorities |
| `.lck` | Prevent double-scheduling of periodic bot tasks |
| `.hook` | Reaction to events: joins, raids, commands |
| `.lujit` | Run in-game logic scripts via LuaJIT subprocess |
| `.pyru` | Advanced plugin modules with Python (bot API) + Rust (perf-critical) |

### For LUnlocker (System Utility)

| Format | How to integrate |
|---|---|
| `.acf` | Verify integrity of critical .exe files before launch (cached SHA-256) |
| `.ctxt` | Load signed security policies, refuse to run if tampered |
| `.cmdx` | Execute system commands by priority |
| `.qset` | Quarantine files with SHA-256 deduplication |
| `.tcfg` | Schedule scans, cleanups, backups |
| `.vset` | UI theme and visual settings |
| `.bakq` | Automatic versioned backups of user files |
| `.snap` | Detect added/changed/removed files between scans |
| `.cache` | Cache scan results with TTL |
| `.rpt` | Generate scan reports |
| `.envk` | Store encrypted configuration secrets |
| `.stxt` | Batch operations: clean → verify → restart service |
| `.unkn` | Analyze unknown files before execution |
| `.ulog` | Ultra-fast event logging |
| `.skey` | API keys and access flags |

### For Roblox Server

| Format | How to integrate |
|---|---|
| `.rmap` | Map metadata: name, place ID, player limits, mini-games |
| `.gcfg` | Mini-game settings: rewards, cooldowns, UI colors, sounds |
| `.hmap` | Persistent per-player state without external databases |
| `.alog` | Game event log with timestamps |
| `.tq` | Matchmaking / reward queues |

## VS Code Setup

Add to settings.json:

```json
{
  "files.associations": {
    "*.luna": "ini",
    "*.lujit": "lua",
    "*.pyru": "python",
    "*.stxt": "plaintext",
    "*.ctxt": "plaintext",
    "*.acf": "json",
    "*.unkn": "plaintext",
    "*.cmdx": "plaintext",
    "*.rmap": "ini",
    "*.qset": "json",
    "*.tcfg": "ini",
    "*.vset": "json",
    "*.gcfg": "ini",
    "*.skey": "json",
    "*.pfx": "json",
    "*.ulog": "plaintext",
    "*.conn": "plaintext",
    "*.mcfg": "json",
    "*.alog": "plaintext",
    "*.envk": "plaintext",
    "*.bakq": "json",
    "*.snap": "json",
    "*.tmpl": "plaintext",
    "*.hmap": "json",
    "*.tq": "json",
    "*.lck": "json",
    "*.hook": "json",
    "*.cache": "json",
    "*.wlist": "plaintext",
    "*.rpt": "json"
  }
}
```

## Project Structure

```
custom-formats/
├── custom_formats_v5.py         # Core module (30 formats, no CLI)
├── generators/
│   └── gen_all.py               # Generate all example files
├── examples/                    # One example file per format
├── LICENSE
└── README.md
```

## License

MIT License. See LICENSE for details.

## Author

GitHub: [@v86889839-collab](https://github.com/v86889839-collab)

## Changelog

### V5NCLI
- Bug fixes: C++/C misdetection, `.luna` duplicate-section merge, `create_stxt` dict mutation, `process_unkn_folder` invalid-path crash, Windows `.pyru` binary suffix, timeout handling via `CFTimeoutError`
- 13 new formats: `.mcfg`, `.alog`, `.envk`, `.bakq`, `.snap`, `.tmpl`, `.hmap`, `.tq`, `.lck`, `.hook`, `.cache`, `.wlist`, `.rpt`
- LuaJIT speed functions: `luajit_available`, `luajit_eval`, `luajit_lines`
- Custom exception hierarchy (`CFError` family) with `[V5NCLI]` messages and `format_name` field
- CLI removed (V3-based core)
- `.ptbl` and `.mmsg` removed
- `.conn` format kept from V4
- Total formats: 30

### V4
- CLI interface with 13 commands
- New `.conn` format
- Total formats: 21

### V3
- 11 new formats: `.cmdx`, `.ptbl`, `.rmap`, `.qset`, `.tcfg`, `.mmsg`, `.vset`, `.gcfg`, `.skey`, `.pfx`, `.ulog`
- Pre-compiled regex, SHA-256 cache, ThreadPoolExecutor, streaming reads, async wrappers, `benchmark()`

### V2
- 7 formats: `.luna`, `.unkn`, `.pyru`, `.lujit`, `.acf`, `.ctxt`, `.stxt`
