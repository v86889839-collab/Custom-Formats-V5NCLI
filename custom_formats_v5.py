# -*- coding: utf-8 -*-
"""
custom_formats_v5.py — V5NCLI: Единый модуль для работы с пользовательскими форматами.
Версия: 5.0.0 (V5NCLI)
Совместимость: Python 3.9+
Зависимости: только стандартная библиотека. CLI выпилен (база V3).

Форматы (30):
  V2: .luna .unkn .pyru .lujit .acf .ctxt .stxt
  V3: .cmdx .rmap .qset .tcfg .vset .gcfg .skey .pfx .ulog
  V4: .conn
  V5: .mcfg .alog .envk .bakq .snap .tmpl .hmap .tq .lck .hook .cache .wlist .rpt

V5-улучшения:
  - Кастомные ошибки (все с префиксом [V5NCLI], совместимы со встроенными)
  - Исправлены баги V3/V4: cpp/c детект, дубли секций .luna, мутация словарей
    в create_stxt, отсутствие папки в process_unkn_folder, .exe на Windows,
    перехват таймаутов (CFTimeoutError)
  - .ptbl и .mmsg удалены, CLI удалён
  - Больше скорости через LuaJIT: luajit_available, luajit_eval, luajit_lines

Кастомные ошибки V5NCLI:
  CFError            — базовая ошибка тулкита
  CFFileNotFoundError — файл не найден (наследует FileNotFoundError)
  CFNotADirectoryError — путь не папка (наследует NotADirectoryError)
  CFValueError       — неверное значение/аргумент (наследует ValueError)
  CFFormatError      — содержимое файла не соответствует формату (наследует ValueError)
  CFTimeoutError     — превышен таймаут процесса (наследует TimeoutError)
"""

from __future__ import annotations

import re, sys, json, time, random, string, hashlib, asyncio, subprocess, shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional, List, Dict, Tuple, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

VERSION = "5.0.0"
TOOLKIT_TAG = "V5NCLI"

# =====================================================================
# V5: Кастомные ошибки
# =====================================================================
def _cf_msg(text: str) -> str:
    return f"[{TOOLKIT_TAG}] {text}"

class CFError(Exception):
    """Базовая ошибка V5NCLI. Все ошибки тулкита наследуются от неё."""
    def __init__(self, message: str = "", *, format_name: str = ""):
        self.format_name = format_name
        super().__init__(_cf_msg(message))

class CFFileNotFoundError(CFError, FileNotFoundError):
    """Файл не найден. Наследует FileNotFoundError — старые except работают."""
    def __init__(self, message: str = "", *, format_name: str = ""):
        super().__init__(message, format_name=format_name)

class CFNotADirectoryError(CFError, NotADirectoryError):
    """Переданный путь — не папка."""
    def __init__(self, message: str = "", *, format_name: str = ""):
        super().__init__(message, format_name=format_name)

class CFValueError(CFError, ValueError):
    """Неверное значение или аргумент."""
    def __init__(self, message: str = "", *, format_name: str = ""):
        super().__init__(message, format_name=format_name)

class CFFormatError(CFError, ValueError):
    """Содержимое файла не соответствует формату V5NCLI (битый/подделанный файл)."""
    def __init__(self, message: str = "", *, format_name: str = ""):
        super().__init__(message, format_name=format_name)

class CFTimeoutError(CFError, TimeoutError):
    """Процесс не уложился в таймаут и был остановлен."""
    def __init__(self, message: str = "превышен таймаут процесса", *, format_name: str = ""):
        super().__init__(message, format_name=format_name)

def _require_file(path: Path, format_name: str = "") -> None:
    if not path.exists():
        raise CFFileNotFoundError(f"файл не найден: {path}", format_name=format_name)

# =====================================================================
# Предкомпилированные регулярные выражения
# =====================================================================
_LANG_SIGNATURES_RAW = [
    ("py",   [r"^#!.*python", r"^\s*def\s+\w+\(", r"^\s*import\s+\w+", r"^\s*from\s+\w+\s+import"]),
    ("rs",   [r"^\s*fn\s+\w+\(", r"^\s*use\s+\w+::", r"^\s*pub\s+(fn|struct|enum)\s+\w+"]),
    ("lua",  [r"^\s*local\s+\w+\s*=", r"^\s*function\s+\w+\(", r"^\s*require\s*[\(\"]"]),
    ("js",   [r"^\s*const\s+\w+\s*=", r"^\s*function\s+\w+\(", r"^\s*import\s+.*from\s+"]),
    # V5 FIX: cpp раньше c — у них общая сигнатура #include, но у cpp есть свои
    ("cpp",  [r"^\s*#include\s*<", r"^\s*std::", r"^\s*class\s+\w+\s*\{"]),
    ("c",    [r"^\s*#include\s*<", r"^\s*int\s+main\s*\("]),
    ("go",   [r"^\s*package\s+\w+", r"^\s*func\s+\w+\(", r"^\s*import\s+\("]),
    ("sh",   [r"^#!/bin/(ba)?sh", r"^\s*echo\s+"]),
    ("java", [r"^\s*public\s+(class|static|void)\s+", r"^\s*import\s+java\."]),
]
_LANG_SIGNATURES_COMPILED: Dict[str, List[re.Pattern]] = {
    lang: [re.compile(p, re.MULTILINE) for p in patterns]
    for lang, patterns in _LANG_SIGNATURES_RAW
}
_LUAJIT_MARKERS = ["jit", "luajit", "ffi.cdef", "ffi.C"]
_SHA256_CACHE: Dict[Tuple[str, float], str] = {}
_SHA256_CACHE_LIMIT = 4096  # V5: ограничение кэша

def _sha256_cached(path: Path) -> str:
    stat = path.stat()
    key = (str(path), stat.st_mtime)
    if key in _SHA256_CACHE:
        return _SHA256_CACHE[key]
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    digest = h.hexdigest()
    if len(_SHA256_CACHE) >= _SHA256_CACHE_LIMIT:
        _SHA256_CACHE.clear()  # V5: простая защита от роста кэша
    _SHA256_CACHE[key] = digest
    return digest

def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _is_float(val: str) -> bool:
    try:
        float(val)
        return True
    except ValueError:
        return False

def _format_value(val: Any) -> str:
    if isinstance(val, list):
        return "[" + ", ".join(str(v) for v in val) + "]"
    return str(val)

def _get_random_name(ext: str = "") -> str:
    name = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"{name}{ext}"

# V5: безопасный запуск процессов с таймаутом
def _run_safe(cmd: List[str] | str, timeout: int, shell: bool = False,
              format_name: str = "") -> Dict[str, Any]:
    try:
        proc = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=timeout)
        return {"returncode": proc.returncode, "output": proc.stdout + proc.stderr}
    except subprocess.TimeoutExpired:
        raise CFTimeoutError(f"процесс '{cmd if isinstance(cmd, str) else cmd[0]}' не уложился в {timeout} сек",
                             format_name=format_name)

# =====================================================================
# 1. .luna — конфиги
# =====================================================================
@dataclass
class LunaConfig:
    data: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "LunaConfig":
        p = Path(path)
        _require_file(p, ".luna")
        cfg = cls()
        current_section: Optional[str] = None
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current_section = line[1:-1].strip()
                    # V5 FIX: мерж вместо затирания
                    cfg.data.setdefault(current_section, {})
                    continue
                if ": " in line:
                    key, _, value = line.partition(": ")
                    key = key.strip(); value = value.strip()
                    if value.startswith("[") and value.endswith("]"):
                        value = [v.strip() for v in value[1:-1].split(",") if v.strip()]
                    elif value.isdigit():
                        value = int(value)
                    elif _is_float(value):
                        value = float(value)
                    if current_section:
                        cfg.data[current_section][key] = value
                    else:
                        cfg.data[key] = value
        return cfg

    def save(self, path: str | Path) -> None:
        p = Path(path)
        lines: List[str] = []
        for key, value in self.data.items():
            if isinstance(value, dict):
                lines.append(f"[{key}]")
                for k, v in value.items():
                    lines.append(f"  {k}: {_format_value(v)}")
                lines.append("")
            else:
                lines.append(f"{key}: {_format_value(value)}")
        p.write_text("\n".join(lines), encoding="utf-8")

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self.data.get(section, {}).get(key, default)

# =====================================================================
# 2. .unkn — определение языка
# =====================================================================
def detect_language(content: str) -> str:
    for marker in _LUAJIT_MARKERS:
        if marker in content:
            return "lujit"
    for lang, patterns in _LANG_SIGNATURES_COMPILED.items():
        score = sum(1 for pat in patterns if pat.search(content))
        if score >= 2:
            return lang
    for lang, patterns in _LANG_SIGNATURES_COMPILED.items():
        for pat in patterns:
            if pat.search(content):
                return lang
    return "txt"

def process_unkn(path: str | Path, dry_run: bool = False) -> str:
    p = Path(path)
    _require_file(p, ".unkn")
    if p.suffix != ".unkn":
        return str(p)
    with p.open("r", encoding="utf-8", errors="replace") as f:
        content = f.read(4096)
    lang = detect_language(content)
    ext_map = {"py": "py", "rs": "rs", "lua": "lua", "lujit": "lujit",
               "js": "js", "c": "c", "cpp": "cpp", "go": "go",
               "sh": "sh", "java": "java", "txt": "txt"}
    new_ext = ext_map.get(lang, "txt")
    new_path = p.with_suffix(f".{new_ext}")
    if dry_run:
        print(f"[DRY RUN] {p.name} -> {new_path.name} (язык: {lang})")
    else:
        p.rename(new_path)
        print(f"[OK] {p.name} -> {new_path.name} (язык: {lang})")
    return str(new_path)

def process_unkn_folder(folder: str | Path, dry_run: bool = False, max_workers: int = 8) -> List[str]:
    f = Path(folder)
    # V5 FIX: внятная ошибка вместо падения на iterdir()
    if not f.is_dir():
        raise CFNotADirectoryError(f"ожидается папка, получено: {f}", format_name=".unkn")
    unkn_files = [fp for fp in f.iterdir() if fp.suffix == ".unkn"]
    results: List[str] = []

    def _process(fp: Path) -> str:
        try:
            return process_unkn(fp, dry_run)
        except Exception as e:
            return f"[ERROR] {fp.name}: {e}"

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process, fp): fp for fp in unkn_files}
        for future in as_completed(futures):
            results.append(future.result())
    return results

async def process_unkn_folder_async(folder: str | Path, dry_run: bool = False) -> List[str]:
    return await asyncio.to_thread(process_unkn_folder, folder, dry_run)

# =====================================================================
# 3. .pyru — гибрид Python/Rust
# =====================================================================
_PYRU_PYTHON_MARKER = "# === PYTHON ==="
_PYRU_RUST_MARKER = "# === RUST ==="
_PYRU_END_MARKER = "# === END ==="

def create_pyru(python_code: str, rust_code: str, path: str | Path) -> None:
    p = Path(path)
    content = f"""{_PYRU_PYTHON_MARKER}
{python_code}
{_PYRU_END_MARKER}
{_PYRU_RUST_MARKER}
{rust_code}
{_PYRU_END_MARKER}
"""
    p.write_text(content.strip() + "\n", encoding="utf-8")

def parse_pyru(path: str | Path) -> Dict[str, str]:
    p = Path(path)
    _require_file(p, ".pyru")
    result: Dict[str, str] = {"python": "", "rust": ""}
    current_block: Optional[str] = None
    block_lines: List[str] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped == _PYRU_PYTHON_MARKER:
                current_block = "python"; block_lines = []; continue
            elif stripped == _PYRU_RUST_MARKER:
                if current_block:
                    result[current_block] = "\n".join(block_lines).strip()
                current_block = "rust"; block_lines = []; continue
            elif stripped == _PYRU_END_MARKER:
                if current_block:
                    result[current_block] = "\n".join(block_lines).strip()
                current_block = None; block_lines = []; continue
            if current_block:
                block_lines.append(line.rstrip())
    if current_block and block_lines:
        result[current_block] = "\n".join(block_lines).strip()
    return result

def run_pyru(path: str | Path, python_executable: str = "python",
             rust_compiler: str = "rustc", temp_dir: str | Path = ".pyru_temp") -> Dict[str, Any]:
    parts = parse_pyru(path)
    result: Dict[str, Any] = {"python_output": "", "rust_output": "", "rust_compiled": False}

    if parts["python"]:
        r = _run_safe([python_executable, "-c", parts["python"]], timeout=30, format_name=".pyru")
        result["python_output"] = r["output"]

    if parts["rust"]:
        tmp = Path(temp_dir)
        tmp.mkdir(exist_ok=True)
        rs_file = tmp / "_pyru_rust.rs"
        rs_file.write_text(parts["rust"], encoding="utf-8")
        # V5 FIX: на Windows rustc создаёт .exe
        exe_file = tmp / ("_pyru_rust.exe" if sys.platform == "win32" else "_pyru_rust")
        r = _run_safe([rust_compiler, "-o", str(exe_file), str(rs_file)], timeout=60, format_name=".pyru")
        if r["returncode"] == 0:
            result["rust_compiled"] = True
            r2 = _run_safe([str(exe_file)], timeout=30, format_name=".pyru")
            result["rust_output"] = r2["output"]
        else:
            result["rust_output"] = f"[Ошибка компиляции]\n{r['output']}"
    return result

async def run_pyru_async(path: str | Path) -> Dict[str, Any]:
    return await asyncio.to_thread(run_pyru, path)

# =====================================================================
# 4. .lujit — LuaJIT
# =====================================================================
def is_lujit_file(path: str | Path) -> bool:
    return Path(path).suffix == ".lujit"

def run_lujit(path: str | Path, luajit_executable: str = "luajit",
              fallback_to_lua: bool = False, lua_executable: str = "lua") -> Dict[str, Any]:
    p = Path(path)
    _require_file(p, ".lujit")
    if not is_lujit_file(p):
        raise CFValueError(f"ожидался .lujit файл, получено: {p.suffix}", format_name=".lujit")
    lujit_path = shutil.which(luajit_executable)
    if lujit_path:
        r = _run_safe([lujit_path, str(p)], timeout=60, format_name=".lujit")
        return {"runner": "luajit", **r}
    elif fallback_to_lua:
        lua_path = shutil.which(lua_executable)
        if lua_path:
            r = _run_safe([lua_path, str(p)], timeout=60, format_name=".lujit")
            r["runner"] = "lua (fallback)"
            r["warning"] = "LuaJIT не найден, использован обычный Lua"
            return r
    return {"runner": "none", "returncode": -1, "output": "LuaJIT не найден в системе"}

def validate_lujit(path: str | Path) -> tuple[bool, str]:
    p = Path(path)
    _require_file(p, ".lujit")
    content = p.read_text(encoding="utf-8", errors="replace")
    jit_features = []
    if "ffi." in content: jit_features.append("ffi")
    if "jit." in content: jit_features.append("jit")
    if "require('ffi')" in content or 'require("ffi")' in content: jit_features.append("require ffi")
    if jit_features:
        return True, f"Найдены LuaJIT-фичи: {', '.join(jit_features)}. Обычный Lua не подойдёт."
    return True, "LuaJIT-специфичных конструкций не найдено. Можно запустить и на обычном Lua."

# =====================================================================
# V5 NEW: скорость через LuaJIT
# =====================================================================
def luajit_available() -> bool:
    """Проверяет, установлен ли LuaJIT в системе."""
    return shutil.which("luajit") is not None

def luajit_eval(code: str, timeout: int = 10) -> Dict[str, Any]:
    """Выполняет Lua-код через LuaJIT и возвращает stdout."""
    if not luajit_available():
        return {"runner": "none", "returncode": -1, "output": "LuaJIT не найден в системе"}
    r = _run_safe(["luajit", "-e", code], timeout=timeout, format_name="luajit")
    r["runner"] = "luajit"
    return r

def luajit_lines(lines: List[str], lua_body: str, timeout: int = 30) -> Dict[str, Any]:
    """Пакетная обработка строк через LuaJIT: каждая строка приходит в `line`,
    результат печатайте через print(). Lua-код в lua_body — тело цикла."""
    if not luajit_available():
        return {"runner": "none", "returncode": -1, "output": "LuaJIT не найден в системе"}
    script = (
        "local function esc(s) return s:gsub('\\\\', '\\\\\\\\'):gsub(\"'\", \"\\\\'\") end\n"
        "for line in io.lines() do\n"
        f"  line = esc(line)\n"
        f"  {lua_body}\n"
        "end\n"
    )
    proc_input = "\n".join(lines)
    try:
        proc = subprocess.run(["luajit", "-e", script], input=proc_input,
                              capture_output=True, text=True, timeout=timeout)
        return {"runner": "luajit", "returncode": proc.returncode,
                "output": proc.stdout + proc.stderr}
    except subprocess.TimeoutExpired:
        raise CFTimeoutError(f"luajit_lines не уложился в {timeout} сек", format_name="luajit")

# =====================================================================
# 5. .acf — манифест критического exe
# =====================================================================
@dataclass
class AcfManifest:
    exe_name: str
    version: str = "1.0.0"
    critical: bool = True
    description: str = ""
    expected_sha256: str = ""
    min_os_version: str = ""
    requires_admin: bool = False
    auto_restart: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {"exe_name": self.exe_name, "version": self.version, "critical": self.critical,
                "description": self.description, "expected_sha256": self.expected_sha256,
                "min_os_version": self.min_os_version, "requires_admin": self.requires_admin,
                "auto_restart": self.auto_restart}

    @classmethod
    def load(cls, path: str | Path) -> "AcfManifest":
        p = Path(path)
        _require_file(p, ".acf")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(**data)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    def verify_exe(self, exe_dir: str | Path) -> tuple[bool, str]:
        exe_path = Path(exe_dir) / self.exe_name
        if not exe_path.exists():
            return False, f"EXE не найден: {exe_path}"
        if self.expected_sha256:
            sha = _sha256_cached(exe_path)
            if sha != self.expected_sha256:
                return False, f"Хэш не совпадает: {sha} != {self.expected_sha256}"
        return True, "OK"

def create_acf(exe_path: str | Path, exe_name: str, acf_path: str | Path,
               version: str = "1.0.0", description: str = "",
               critical: bool = True, requires_admin: bool = False) -> None:
    p = Path(exe_path)
    sha = _sha256_cached(p) if p.exists() else ""
    manifest = AcfManifest(exe_name=exe_name, version=version, critical=critical,
                           description=description, expected_sha256=sha, requires_admin=requires_admin)
    manifest.save(acf_path)

# =====================================================================
# 6. .ctxt — подписанный текст
# =====================================================================
def save_ctxt(path: str | Path, content: str, critical_level: str = "normal") -> None:
    p = Path(path)
    sig = _sha256_text(content)
    header = f"SIGNATURE: {sig}\nCritical Level: {critical_level}\n"
    p.write_text(header + content, encoding="utf-8")

def load_ctxt(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    _require_file(p, ".ctxt")
    with p.open("r", encoding="utf-8") as f:
        first_line = f.readline().strip()
        second_line = f.readline().strip()
        rest = f.read()
    if not first_line.startswith("SIGNATURE: "):
        return {"valid": False, "content": rest, "critical_level": "unknown",
                "error": "Нет подписи"}
    expected_sig = first_line[len("SIGNATURE: "):]
    if second_line.startswith("Critical Level: "):
        critical_level = second_line[len("Critical Level: "):].strip()
        content = rest
    else:
        critical_level = "normal"
        content = second_line + "\n" + rest
    actual_sig = _sha256_text(content)
    return {"valid": actual_sig == expected_sig, "content": content,
            "critical_level": critical_level, "expected_sig": expected_sig, "actual_sig": actual_sig}

# =====================================================================
# 7. .stxt — текстовые команды
# =====================================================================
@dataclass
class StxtCommand:
    command: str
    params: Dict[str, str] = field(default_factory=dict)

def parse_stxt(path: str | Path) -> List[StxtCommand]:
    p = Path(path)
    _require_file(p, ".stxt")
    commands: List[StxtCommand] = []
    current: Optional[StxtCommand] = None
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("COMMAND: "):
                if current:
                    commands.append(current)
                current = StxtCommand(command=line[len("COMMAND: "):].strip())
            elif current and ": " in line:
                key, _, value = line.partition(": ")
                current.params[key.strip()] = value.strip()
    if current:
        commands.append(current)
    return commands

def execute_stxt(path: str | Path, handler: Callable[[str, Dict[str, str]], Any]) -> List[Any]:
    return [handler(cmd.command, cmd.params) for cmd in parse_stxt(path)]

def create_stxt(commands: List[Dict[str, Any]], path: str | Path) -> None:
    p = Path(path)
    lines: List[str] = ["# SCRIPT_TXT v1", f"# Generated by {TOOLKIT_TAG} v{VERSION}", ""]
    for cmd in commands:
        # V5 FIX: get вместо pop — не мутируем словари вызывающего
        command = cmd.get("command")
        if command is None:
            raise CFValueError("в словаре команды нет ключа 'command'", format_name=".stxt")
        lines.append(f"COMMAND: {command}")
        for k, v in cmd.items():
            if k == "command":
                continue
            lines.append(f"{k}: {v}")
        lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")

async def parse_stxt_async(path: str | Path) -> List[StxtCommand]:
    return await asyncio.to_thread(parse_stxt, path)

# =====================================================================
# 8. .cmdx — команды с приоритетами
# =====================================================================
@dataclass
class CmdxCommand:
    priority: int
    command: str

def parse_cmdx(path: str | Path) -> List[CmdxCommand]:
    p = Path(path)
    _require_file(p, ".cmdx")
    commands: List[CmdxCommand] = []
    with p.open("r", encoding="utf-8") as f:
        prev_prio = None
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and "priority=" in line:
                try:
                    prev_prio = int(line.split("priority=")[1].split("]")[0])
                except (ValueError, IndexError):
                    prev_prio = 99
            elif prev_prio is not None:
                commands.append(CmdxCommand(priority=prev_prio, command=line))
                prev_prio = None
    commands.sort(key=lambda c: c.priority)
    return commands

def run_cmdx(path: str | Path, dry_run: bool = False) -> List[Dict[str, Any]]:
    commands = parse_cmdx(path)
    results = []
    for cmd in commands:
        if dry_run:
            results.append({"command": cmd.command, "priority": cmd.priority, "output": "[DRY RUN]"})
        else:
            r = _run_safe(cmd.command, timeout=60, shell=True, format_name=".cmdx")
            results.append({"command": cmd.command, "priority": cmd.priority, **r})
    return results

def create_cmdx(commands: List[Tuple[int, str]], path: str | Path) -> None:
    p = Path(path)
    lines: List[str] = ["# CMDX v1", f"# Generated by {TOOLKIT_TAG} v{VERSION}", ""]
    for prio, cmd in sorted(commands, key=lambda x: x[0]):
        lines.append(f"[priority={prio}]")
        lines.append(cmd)
        lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")

# =====================================================================
# 9. .rmap — метаданные карты
# =====================================================================
@dataclass
class RmapConfig:
    name: str = ""
    place_id: str = ""
    min_players: int = 1
    max_players: int = 50
    mini_games: List[str] = field(default_factory=list)
    required_roles: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "RmapConfig":
        p = Path(path)
        _require_file(p, ".rmap")
        cfg = cls()
        current_section: Optional[str] = None
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("[") and line.endswith("]"):
                    current_section = line[1:-1].strip()
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip(); value = value.strip()
                    if current_section == "map":
                        if key == "name": cfg.name = value
                        elif key == "id": cfg.place_id = value
                        elif key == "min_players": cfg.min_players = int(value)
                        elif key == "max_players": cfg.max_players = int(value)
                    elif current_section == "features":
                        if key == "mini_games":
                            cfg.mini_games = [v.strip() for v in value.split(",") if v.strip()]
                        elif key == "required_roles":
                            cfg.required_roles = [v.strip() for v in value.split(",") if v.strip()]
        return cfg

    def save(self, path: str | Path) -> None:
        p = Path(path)
        lines: List[str] = ["[map]", f"name = {self.name}", f"id = {self.place_id}",
                            f"min_players = {self.min_players}", f"max_players = {self.max_players}",
                            "", "[features]",
                            f"mini_games = {', '.join(self.mini_games)}",
                            f"required_roles = {', '.join(self.required_roles)}"]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

# =====================================================================
# 10. .qset — карантин файлов
# =====================================================================
@dataclass
class QsetEntry:
    path: str
    sha256: str
    reason: str
    timestamp: int

def load_qset(path: str | Path) -> List[QsetEntry]:
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [QsetEntry(**f) for f in data.get("files", [])]

def save_qset(path: str | Path, entries: List[QsetEntry]) -> None:
    p = Path(path)
    data = {"files": [{"path": e.path, "sha256": e.sha256, "reason": e.reason, "timestamp": e.timestamp}
                      for e in entries]}
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def add_to_qset(path: str | Path, file_path: str | Path, reason: str) -> bool:
    entries = load_qset(path)
    fp = Path(file_path)
    if not fp.exists():
        return False
    sha = _sha256_cached(fp)
    for e in entries:
        if e.sha256 == sha:
            return False
    entries.append(QsetEntry(path=str(file_path), sha256=sha, reason=reason, timestamp=int(time.time())))
    save_qset(path, entries)
    return True

def remove_from_qset(path: str | Path, sha256: str) -> bool:
    entries = load_qset(path)
    before = len(entries)
    entries = [e for e in entries if e.sha256 != sha256]
    if len(entries) < before:
        save_qset(path, entries)
        return True
    return False

# =====================================================================
# 11. .tcfg — задачи по расписанию
# =====================================================================
@dataclass
class TcfgTask:
    name: str
    schedule: str  # "HH:MM"
    command: str
    enabled: bool = True

def parse_tcfg(path: str | Path) -> List[TcfgTask]:
    p = Path(path)
    _require_file(p, ".tcfg")
    tasks: List[TcfgTask] = []
    current: Optional[TcfgTask] = None
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                if current:
                    tasks.append(current)
                current = TcfgTask(name=line[1:-1].strip(), schedule="", command="")
            elif current and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip(); value = value.strip()
                if key == "schedule": current.schedule = value
                elif key == "command": current.command = value
                elif key == "enabled": current.enabled = value.lower() in ("true", "1", "yes")
        if current:
            tasks.append(current)
    return tasks

def check_and_run_tcfg(path: str | Path) -> List[Dict[str, Any]]:
    tasks = parse_tcfg(path)
    now = time.localtime()
    hh_mm = f"{now.tm_hour:02d}:{now.tm_min:02d}"
    results = []
    for t in tasks:
        if not t.enabled:
            continue
        if t.schedule == hh_mm:
            r = _run_safe(t.command, timeout=120, shell=True, format_name=".tcfg")
            results.append({"task": t.name, "command": t.command, **r})
    return results

def create_tcfg(tasks: List[Dict[str, str]], path: str | Path) -> None:
    p = Path(path)
    lines: List[str] = ["# TCFG v1", f"# Generated by {TOOLKIT_TAG} v{VERSION}", ""]
    for t in tasks:
        lines.append(f"[{t['name']}]")
        lines.append(f"schedule = {t.get('schedule', '')}")
        lines.append(f"command = {t.get('command', '')}")
        if "enabled" in t:
            lines.append(f"enabled = {t['enabled']}")
        lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")

# =====================================================================
# 12. .vset — визуальные настройки
# =====================================================================
@dataclass
class VsetConfig:
    theme: str = "dark"
    colors: Dict[str, str] = field(default_factory=dict)
    ui: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> "VsetConfig":
        p = Path(path)
        _require_file(p, ".vset")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(theme=data.get("theme", "dark"), colors=data.get("colors", {}), ui=data.get("ui", {}))

    def save(self, path: str | Path) -> None:
        p = Path(path)
        data = {"theme": self.theme, "colors": self.colors, "ui": self.ui}
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_color(self, key: str, default: str = "#FFFFFF") -> str:
        return self.colors.get(key, default)

    def get_ui(self, key: str, default: Any = None) -> Any:
        return self.ui.get(key, default)

# =====================================================================
# 13. .gcfg — конфиг мини-игр
# =====================================================================
@dataclass
class GcfgGame:
    enabled: bool = False
    reward_points: int = 0
    cooldown_sec: int = 0
    ui_color_accent: str = "#FFFFFF"
    ui_font_size: int = 12
    sound_volume: float = 1.0

def load_gcfg(path: str | Path) -> Dict[str, GcfgGame]:
    p = Path(path)
    _require_file(p, ".gcfg")
    from configparser import ConfigParser
    cfg = ConfigParser()
    cfg.read(p, encoding="utf-8")
    games: Dict[str, GcfgGame] = {}
    for section in cfg.sections():
        games[section] = GcfgGame(
            enabled=cfg.getboolean(section, "enabled", fallback=False),
            reward_points=cfg.getint(section, "reward_points", fallback=0),
            cooldown_sec=cfg.getint(section, "cooldown_sec", fallback=0),
            ui_color_accent=cfg.get(section, "ui_color_accent", fallback="#FFFFFF"),
            ui_font_size=cfg.getint(section, "ui_font_size", fallback=12),
            sound_volume=cfg.getfloat(section, "sound_volume", fallback=1.0))
    return games

def get_game_config(path: str | Path, game_name: str) -> Optional[GcfgGame]:
    return load_gcfg(path).get(game_name)

def create_gcfg(games: Dict[str, Dict[str, Any]], path: str | Path) -> None:
    p = Path(path)
    lines: List[str] = ["# GCFG v1", f"# Generated by {TOOLKIT_TAG} v{VERSION}", ""]
    for name, settings in games.items():
        lines.append(f"[{name}]")
        for k, v in settings.items():
            lines.append(f"{k} = {v}")
        lines.append("")
    p.write_text("\n".join(lines), encoding="utf-8")

# =====================================================================
# 14. .skey — секретные ключи
# =====================================================================
@dataclass
class SkeyManifest:
    keys: Dict[str, str] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "SkeyManifest":
        p = Path(path)
        _require_file(p, ".skey")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(keys=data.get("keys", data.get("secrets", {})), flags=data.get("flags", []))

    def save(self, path: str | Path) -> None:
        p = Path(path)
        data = {"keys": self.keys, "flags": self.flags}
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def validate(self, required_keys: List[str]) -> tuple[bool, List[str]]:
        missing = [k for k in required_keys if k not in self.keys or not self.keys[k]]
        return (len(missing) == 0, missing)

    def get(self, key: str, default: str = "") -> str:
        return self.keys.get(key, default)

# =====================================================================
# 15. .pfx — манифест плагина
# =====================================================================
@dataclass
class PfxManifest:
    name: str = ""
    language: str = "python"
    entry_point: str = "main.py"
    dependencies: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    version: str = "1.0.0"

    @classmethod
    def load(cls, path: str | Path) -> "PfxManifest":
        p = Path(path)
        _require_file(p, ".pfx")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(name=data.get("name", ""), language=data.get("language", "python"),
                   entry_point=data.get("entry_point", "main.py"), dependencies=data.get("dependencies", []),
                   permissions=data.get("permissions", []), version=data.get("version", "1.0.0"))

    def save(self, path: str | Path) -> None:
        p = Path(path)
        data = {"name": self.name, "language": self.language, "entry_point": self.entry_point,
                "dependencies": self.dependencies, "permissions": self.permissions, "version": self.version}
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def has_permission(self, perm: str) -> bool:
        return perm in self.permissions

# =====================================================================
# 16. .ulog — лёгкий лог
# =====================================================================
def log_ulog(path: str | Path, event: str) -> None:
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(event + "\n")

def tail_ulog(path: str | Path, lines: int = 20) -> List[str]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8") as f:
        all_lines = f.readlines()
    return all_lines[-lines:]

def read_ulog(path: str | Path) -> List[str]:
    p = Path(path)
    if not p.exists():
        return []
    return p.read_text(encoding="utf-8").strip().splitlines()

def clear_ulog(path: str | Path) -> None:
    Path(path).write_text("", encoding="utf-8")

# =====================================================================
# 17. .conn — склейка файлов (из V4)
# =====================================================================
def connect_files(files: List[str], output_name: str = "merged.conn") -> str:
    if len(files) < 2:
        raise CFValueError("нужно минимум 2 файла для объединения", format_name=".conn")
    output_path = Path(output_name)
    lines = []
    for f in files:
        path = Path(f)
        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                # V5: бинарные файлы не роняют склейку
                content = f"<< бинарный файл пропущен: {path.name} >>"
            lines.append(f"# === SOURCE: {path.name} ===")
            lines.append(content)
            lines.append("# === END SOURCE ===\n")
        else:
            lines.append(f"# === SOURCE: {f} (не найден) ===\n")
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return str(output_path)

def clear_conn_format(filepath: str) -> str:
    p = Path(filepath)
    if p.suffix == ".conn":
        new_path = p.with_suffix("")
        p.rename(new_path)
        return str(new_path)
    return str(p)

# =====================================================================
# 18. V5 NEW: .mcfg — главный конфиг тулкита
# =====================================================================
def mcfg_default() -> Dict[str, Any]:
    return {"version": VERSION, "default_location": str(Path.cwd()), "safe_mode": True,
            "max_time_sec": 10, "max_memory_mb": 256,
            "formats": [".luna", ".unkn", ".pyru", ".lujit", ".acf", ".ctxt", ".stxt",
                        ".cmdx", ".rmap", ".qset", ".tcfg", ".vset", ".gcfg", ".skey",
                        ".pfx", ".ulog", ".conn", ".mcfg", ".alog", ".envk", ".bakq",
                        ".snap", ".tmpl", ".hmap", ".tq", ".lck", ".hook", ".cache",
                        ".wlist", ".rpt"]}

def save_mcfg(path: str | Path, cfg: Dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

def load_mcfg(path: str | Path) -> Dict[str, Any]:
    p = Path(path)
    _require_file(p, ".mcfg")
    return json.loads(p.read_text(encoding="utf-8"))

def mcfg_get(path: str | Path, key: str, default: Any = None) -> Any:
    return load_mcfg(path).get(key, default)

def mcfg_set(path: str | Path, key: str, value: Any) -> None:
    cfg = load_mcfg(path) if Path(path).exists() else mcfg_default()
    cfg[key] = value
    save_mcfg(path, cfg)

# =====================================================================
# 19. V5 NEW: .alog — лог с таймстампами и уровнями
# =====================================================================
def log_alog(path: str | Path, event: str, level: str = "INFO") -> None:
    ts = int(time.time())
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(f"{ts}|{level}|{event}\n")

def _parse_alog_line(line: str) -> Optional[Tuple[int, str, str]]:
    parts = line.split("|", 2)
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), parts[1], parts[2]
    except ValueError:
        return None

def read_alog(path: str | Path, level: Optional[str] = None, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            parsed = _parse_alog_line(line.strip())
            if parsed is None:
                continue
            ts, lvl, event = parsed
            if level and lvl != level:
                continue
            out.append({"ts": ts, "level": lvl, "event": event})
    if limit:
        out = out[-limit:]
    return out

def alog_since(path: str | Path, since_ts: int) -> List[Dict[str, Any]]:
    return [e for e in read_alog(path) if e["ts"] >= since_ts]

def clear_alog(path: str | Path) -> None:
    Path(path).write_text("", encoding="utf-8")

# =====================================================================
# 20. V5 NEW: .envk — env-переменные с обфускацией
# =====================================================================
def _xor_bytes(data: bytes, key: str) -> bytes:
    kb = key.encode("utf-8")
    return bytes(b ^ kb[i % len(kb)] for i, b in enumerate(data))

def save_envk(path: str | Path, env: Dict[str, str], key: str) -> None:
    p = Path(path)
    lines = [f"ENVK v1|{len(env)}"]
    for k, v in sorted(env.items()):
        payload = _xor_bytes(f"{k}={v}".encode("utf-8"), key)
        lines.append(f"{k}\t{__import__('base64').b64encode(payload).decode('ascii')}")
    p.write_text("\n".join(lines), encoding="utf-8")

def load_envk(path: str | Path, key: str) -> Dict[str, str]:
    p = Path(path)
    _require_file(p, ".envk")
    import base64
    env: Dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("ENVK"):
            continue
        name, sep, payload = line.partition("\t")
        if not sep:
            raise CFFormatError(f"битая строка в .envk: {line[:40]!r}", format_name=".envk")
        decoded = _xor_bytes(base64.b64decode(payload), key)
        k, _, v = decoded.decode("utf-8").partition("=")
        env[k] = v
    return env

# =====================================================================
# 21. V5 NEW: .bakq — очередь бэкапов
# =====================================================================
def backup_file(file_path: str | Path, queue_path: str | Path, backup_dir: str | Path = ".bakq_store") -> Dict[str, Any]:
    fp = Path(file_path)
    _require_file(fp, ".bakq")
    bd = Path(backup_dir)
    bd.mkdir(exist_ok=True)
    sha = _sha256_cached(fp)
    dst = bd / f"{fp.stem}_{int(time.time())}{fp.suffix}"
    shutil.copy(fp, dst)
    record = {"src": str(fp), "backup": str(dst), "sha256": sha, "ts": int(time.time())}
    qpath = Path(queue_path)
    queue: List[Dict[str, Any]] = []
    if qpath.exists():
        queue = json.loads(qpath.read_text(encoding="utf-8")).get("backups", [])
    queue.append(record)
    qpath.write_text(json.dumps({"backups": queue}, indent=2, ensure_ascii=False), encoding="utf-8")
    return record

def list_backups(queue_path: str | Path, src: Optional[str] = None) -> List[Dict[str, Any]]:
    qpath = Path(queue_path)
    if not qpath.exists():
        return []
    queue = json.loads(qpath.read_text(encoding="utf-8")).get("backups", [])
    if src:
        queue = [b for b in queue if b["src"] == str(src)]
    return queue

def restore_latest(src: str | Path, queue_path: str | Path) -> str:
    backups = list_backups(queue_path, src=str(src))
    if not backups:
        raise CFValueError(f"бэкапов для {src} не найдено", format_name=".bakq")
    latest = max(backups, key=lambda b: b["ts"])
    shutil.copy(latest["backup"], src)
    return latest["backup"]

# =====================================================================
# 22. V5 NEW: .snap — снапшот каталога
# =====================================================================
def create_snap(folder: str | Path, snap_path: str | Path) -> Dict[str, Any]:
    f = Path(folder)
    if not f.is_dir():
        raise CFNotADirectoryError(f"ожидается папка, получено: {f}", format_name=".snap")
    files = {}
    for fp in sorted(f.rglob("*")):
        if fp.is_file():
            files[str(fp.relative_to(f))] = _sha256_cached(fp)
    data = {"ts": int(time.time()), "files": files}
    Path(snap_path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return data

def diff_snap(snap_old: str | Path, snap_new: str | Path) -> Dict[str, List[str]]:
    old = json.loads(Path(snap_old).read_text(encoding="utf-8")).get("files", {})
    new = json.loads(Path(snap_new).read_text(encoding="utf-8")).get("files", {})
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    return {"added": added, "removed": removed, "changed": changed}

# =====================================================================
# 23. V5 NEW: .tmpl — шаблоны
# =====================================================================
_TMPL_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")

def save_tmpl(path: str | Path, template: str) -> None:
    Path(path).write_text(template, encoding="utf-8")

def load_tmpl(path: str | Path) -> str:
    p = Path(path)
    _require_file(p, ".tmpl")
    return p.read_text(encoding="utf-8")

def render_tmpl(template: str, context: Dict[str, Any]) -> str:
    return _TMPL_RE.sub(lambda m: str(context.get(m.group(1), m.group(0))), template)

def find_placeholders(template: str) -> List[str]:
    return _TMPL_RE.findall(template)

# =====================================================================
# 24. V5 NEW: .hmap — постоянный key-value
# =====================================================================
def hmap_set(path: str | Path, key: str, value: Any) -> None:
    p = Path(path)
    data: Dict[str, Any] = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line and ": " in line and not line.startswith("#"):
                k, _, v = line.partition(": ")
                data[k] = v
    data[key] = _format_value(value) if isinstance(value, (list, dict)) else str(value)
    p.write_text("\n".join(f"{k}: {v}" for k, v in data.items()), encoding="utf-8")

def hmap_get(path: str | Path, key: str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    for line in p.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and ": " in line:
            k, _, v = line.partition(": ")
            if k == key:
                if v.isdigit(): return int(v)
                if _is_float(v): return float(v)
                if v.startswith("[") and v.endswith("]"):
                    return [x.strip() for x in v[1:-1].split(",") if x.strip()]
                return v
    return default

def hmap_del(path: str | Path, key: str) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    lines = p.read_text(encoding="utf-8").splitlines()
    kept = [l for l in lines if not (l and not l.startswith("#") and l.partition(": ")[0] == key)]
    if len(kept) == len(lines):
        return False
    p.write_text("\n".join(kept), encoding="utf-8")
    return True

def hmap_keys(path: str | Path) -> List[str]:
    p = Path(path)
    if not p.exists():
        return []
    return [l.partition(": ")[0] for l in p.read_text(encoding="utf-8").splitlines()
            if l and not l.startswith("#") and ": " in l]

# =====================================================================
# 25. V5 NEW: .tq — очередь задач с приоритетами
# =====================================================================
def _tq_load(path: str | Path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("tasks", [])

def _tq_save(path: str | Path, tasks: List[Dict[str, Any]]) -> None:
    Path(path).write_text(json.dumps({"tasks": tasks}, indent=2, ensure_ascii=False), encoding="utf-8")

def tq_push(path: str | Path, task: Any, priority: int = 5) -> int:
    tasks = _tq_load(path)
    tasks.append({"task": task, "priority": priority, "ts": int(time.time())})
    tasks.sort(key=lambda t: t["priority"])
    _tq_save(path, tasks)
    return len(tasks)

def tq_pop(path: str | Path) -> Optional[Any]:
    tasks = _tq_load(path)
    if not tasks:
        return None
    item = tasks.pop(0)
    _tq_save(path, tasks)
    return item["task"]

def tq_peek(path: str | Path) -> Optional[Any]:
    tasks = _tq_load(path)
    return tasks[0]["task"] if tasks else None

def tq_len(path: str | Path) -> int:
    return len(_tq_load(path))

# =====================================================================
# 26. V5 NEW: .lck — файловые блокировки с TTL
# =====================================================================
def lock_acquire(path: str | Path, holder: str, ttl_sec: int = 30) -> bool:
    p = Path(path)
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() < data.get("expires", 0):
            return False  # живой lock — занято
        # просроченный lock считается свободным
    p.write_text(json.dumps({"holder": holder, "acquired": int(time.time()),
                             "expires": time.time() + ttl_sec}), encoding="utf-8")
    return True

def lock_release(path: str | Path, holder: str) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("holder") != holder:
        return False
    p.unlink()
    return True

def lock_holder(path: str | Path) -> Optional[str]:
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if time.time() >= data.get("expires", 0):
        return None  # просроченный lock — фактически свободен
    return data.get("holder")

# =====================================================================
# 27. V5 NEW: .hook — события и хуки
# =====================================================================
def add_hook(path: str | Path, event: str, command: str, use_tmpl: bool = False) -> int:
    p = Path(path)
    data: Dict[str, Any] = {}
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
    hooks = data.setdefault("hooks", {})
    hooks.setdefault(event, []).append({"command": command, "use_tmpl": use_tmpl})
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(hooks[event])

def remove_hook(path: str | Path, event: str, index: int = 0) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    data = json.loads(p.read_text(encoding="utf-8"))
    hooks = data.get("hooks", {})
    if event not in hooks or index >= len(hooks[event]):
        return False
    hooks[event].pop(index)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return True

def fire_hooks(path: str | Path, event: str, context: Dict[str, Any] = {},
               dry_run: bool = True) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    results = []
    for hook in data.get("hooks", {}).get(event, []):
        cmd = render_tmpl(hook["command"], context) if hook.get("use_tmpl") else hook["command"]
        if dry_run:
            results.append({"command": cmd, "output": "[DRY RUN]"})
        else:
            r = _run_safe(cmd, timeout=60, shell=True, format_name=".hook")
            results.append({"command": cmd, **r})
    return results

# =====================================================================
# 28. V5 NEW: .cache — TTL-кэш с ленивой чисткой
# =====================================================================
def cache_set(path: str | Path, key: str, value: Any, ttl_sec: int = 300) -> None:
    p = Path(path)
    data: Dict[str, Any] = {}
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
    data[key] = {"value": value, "expires": time.time() + ttl_sec}
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

def cache_get(path: str | Path, key: str, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    data = json.loads(p.read_text(encoding="utf-8"))
    now = time.time()
    # ленивая чистка просроченного
    expired = [k for k, v in data.items() if now >= v.get("expires", 0)]
    if expired:
        for k in expired:
            del data[k]
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    entry = data.get(key)
    if entry is None:
        return default
    if now >= entry.get("expires", 0):
        return default
    return entry["value"]

def cache_clear(path: str | Path) -> None:
    Path(path).write_text("{}", encoding="utf-8")

# =====================================================================
# 29. V5 NEW: .wlist — белый/чёрный список
# =====================================================================
def wlist_add(path: str | Path, entry: str, list_name: str = "allow") -> None:
    if list_name not in ("allow", "deny"):
        raise CFValueError("list_name должен быть 'allow' или 'deny'", format_name=".wlist")
    p = Path(path)
    sections: Dict[str, List[str]] = {"allow": [], "deny": []}
    current = None
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                continue
            if current in sections:
                sections[current].append(line)
    if entry not in sections[list_name]:
        sections[list_name].append(entry)
    lines = [f"[{name}]"] + sorted(sections[name]) + [""] for name in ("allow", "deny") if True
    # аккуратная сборка
    out = []
    for name in ("allow", "deny"):
        out.append(f"[{name}]")
        out.extend(sorted(set(sections[name])))
        out.append("")
    p.write_text("\n".join(out), encoding="utf-8")

def wlist_check(path: str | Path, value: str) -> Optional[str]:
    """Возвращает 'deny' | 'allow' | None (нет упоминаний). deny имеет приоритет."""
    p = Path(path)
    if not p.exists():
        return None
    sections: Dict[str, List[str]] = {"allow": [], "deny": []}
    current = None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current in sections:
            sections[current].append(line.lower())
    v = value.lower()
    if v in sections["deny"]:
        return "deny"
    if v in sections["allow"]:
        return "allow"
    return None

# =====================================================================
# 30. V5 NEW: .rpt — отчёты
# =====================================================================
@dataclass
class RptReport:
    sections: Dict[str, List[str]] = field(default_factory=dict)

    def add(self, section: str, line: str) -> "RptReport":
        self.sections.setdefault(section, []).append(line)
        return self

    def render(self) -> str:
        lines = []
        for section, items in self.sections.items():
            lines.append(f"== {section} ==")
            lines.extend(items)
            lines.append("")
        return "\n".join(lines)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.render(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "RptReport":
        p = Path(path)
        _require_file(p, ".rpt")
        rpt = cls()
        current: Optional[str] = None
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.rstrip()
            if not line:
                continue
            if line.startswith("== ") and line.endswith(" =="):
                current = line[3:-3]
                rpt.sections.setdefault(current, [])
            elif current:
                rpt.sections[current].append(line)
        return rpt

# =====================================================================
# Бенчмарк
# =====================================================================
def benchmark(func: Callable, *args, runs: int = 10, **kwargs) -> Dict[str, Any]:
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        func(*args, **kwargs)
        times.append(time.perf_counter() - start)
    return {"runs": runs, "min": min(times), "max": max(times),
            "avg": sum(times) / len(times), "total": sum(times)}

# =====================================================================
# Экспорт
# =====================================================================
__all__ = [
    # Ошибки V5NCLI
    "CFError", "CFFileNotFoundError", "CFNotADirectoryError", "CFValueError",
    "CFFormatError", "CFTimeoutError",
    # V2/V3 форматы
    "LunaConfig", "detect_language", "process_unkn", "process_unkn_folder",
    "process_unkn_folder_async", "create_pyru", "parse_pyru", "run_pyru",
    "run_pyru_async", "is_lujit_file", "run_lujit", "validate_lujit",
    "AcfManifest", "create_acf", "save_ctxt", "load_ctxt",
    "StxtCommand", "parse_stxt", "execute_stxt", "create_stxt", "parse_stxt_async",
    "CmdxCommand", "parse_cmdx", "run_cmdx", "create_cmdx",
    "RmapConfig",
    "QsetEntry", "load_qset", "save_qset", "add_to_qset", "remove_from_qset",
    "TcfgTask", "parse_tcfg", "check_and_run_tcfg", "create_tcfg",
    "VsetConfig",
    "GcfgGame", "load_gcfg", "get_game_config", "create_gcfg",
    "SkeyManifest",
    "PfxManifest",
    "log_ulog", "tail_ulog", "read_ulog", "clear_ulog",
    ".conn".strip("."),
    # V5: скорость через LuaJIT
    "luajit_available", "luajit_eval", "luajit_lines",
    # V5 форматы
    "mcfg_default", "save_mcfg", "load_mcfg", "mcfg_get", "mcfg_set",
    "log_alog", "read_alog", "alog_since", "clear_alog",
    "save_envk", "load_envk",
    "backup_file", "list_backups", "restore_latest",
    "create_snap", "diff_snap",
    "save_tmpl", "load_tmpl", "render_tmpl", "find_placeholders",
    "hmap_set", "hmap_get", "hmap_del", "hmap_keys",
    "tq_push", "tq_pop", "tq_peek", "tq_len",
    "lock_acquire", "lock_release", "lock_holder",
    "add_hook", "remove_hook", "fire_hooks",
    "cache_set", "cache_get", "cache_clear",
    "wlist_add", "wlist_check",
    "RptReport",
    # утилиты
    "benchmark", "_sha256_cached",
]

# нормализуем "conn" в __all__
__all__ = [x if x != "conn" else "connect_files" for x in __all__]

if __name__ == "__main__":
    print(f"{TOOLKIT_TAG} v{VERSION} — модуль без CLI, импортируйте: import custom_formats_v5")
