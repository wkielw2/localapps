#!/usr/bin/env python3
"""localapps: TUI que muestra en tiempo real las apps escuchando en puertos TCP locales."""
from __future__ import annotations

import curses
import os
import pty
import re
import shutil
import signal
import subprocess
import sys
import termios
import time
from dataclasses import dataclass, replace
from typing import Callable, Dict, List, Optional, Set, Tuple

REFRESH_SECONDS = 2.0
INPUT_TIMEOUT_MS = 200
# Procesos que escuchan en puertos pero no son proyectos propios: servicios de macOS (AirPlay, Handoff...)
# y herramientas instaladas como .app (Warp, VS Code, DBeaver...).
SYSTEM_PATH_PREFIXES = ("/System/", "/usr/libexec/", "/usr/sbin/")
TOOL_PATH_PREFIXES = ("/Applications/", os.path.expanduser("~/Applications/"))
# CLIs de agentes de IA (Claude Code, Codex) que abren puertos locales internos y no son proyectos propios.
AI_TOOL_PATH_PREFIXES = (
    os.path.expanduser("~/.local/share/claude/"),
    os.path.expanduser("~/.codex/"),
    "/opt/homebrew/Caskroom/codex/",
)
HIDDEN_PATH_PREFIXES = SYSTEM_PATH_PREFIXES + TOOL_PATH_PREFIXES + AI_TOOL_PATH_PREFIXES
# Lanzados desde el PATH, `ps -o comm=` da solo el nombre sin ruta, por eso los prefijos no bastan.
AI_TOOL_EXECUTABLES = ("claude", "codex", "codex-code-mode-host")
# Supervisores de desarrollo que abren puertos internos (hot reload) junto a la app que lanzan.
# Se detectan por la línea de comandos porque el ejecutable es genérico (`dotnet`).
DEV_TOOLING_MARKERS = ("dotnet-watch",)
HOME = os.path.expanduser("~")

LOG_DIR = os.path.expanduser("~/Library/Logs/localapps")
LOG_TAIL_BYTES = 256 * 1024
WRAPPER_LOG_NAME = re.compile(r"^(\d+)\.log$")
ANSI_ESCAPE = re.compile(r"\x1b(\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])")
# Caracteres de control C0 y C1 (categoría Unicode Cc); incluye C1 como \x9b, que ANSI_ESCAPE no quita.
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")

LSOF_LISTEN = ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn"]
LIST_FOOTER = " ↑↓ mover · clic/⏎ logs · o abrir en navegador · x matar · s mostrar ocultas · r refrescar · q salir "
LOG_FOOTER = " ↑↓/rueda desplazar · PgUp/PgDn página · G seguir el final · q/Esc volver "
LIST_FIRST_ROW = 3

MOUSE_CLICK = curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED
MOUSE_WHEEL_UP = curses.BUTTON4_PRESSED
# BUTTON5 (rueda abajo) solo existe con ncurses 6; sin él se desplaza con el teclado.
MOUSE_WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", 0)
MOUSE_WHEEL_LINES = 3


@dataclass(frozen=True)
class ProcessInfo:
    elapsed: str
    executable: str
    command_line: str = ""


@dataclass(frozen=True)
class App:
    pid: int
    name: str
    ports: Tuple[int, ...]
    executable: str
    cwd: str
    elapsed: str
    command_line: str = ""

    @property
    def is_hidden_by_default(self) -> bool:
        is_dev_tooling = any(marker in self.command_line for marker in DEV_TOOLING_MARKERS)
        is_ai_tool = os.path.basename(self.executable) in AI_TOOL_EXECUTABLES
        return is_dev_tooling or is_ai_tool or self.executable.startswith(HIDDEN_PATH_PREFIXES)


# --- Parsing (puro) ---------------------------------------------------------

def parse_listening_ports(lsof_output: str) -> Tuple[Dict[int, Set[int]], Dict[int, str]]:
    """Devuelve (puertos por PID, nombre corto por PID) a partir de `lsof -F pcn`."""
    ports_by_pid: Dict[int, Set[int]] = {}
    names_by_pid: Dict[int, str] = {}
    pid: Optional[int] = None
    for line in lsof_output.splitlines():
        field, value = line[:1], line[1:]
        if field == "p":
            pid = int(value)
            ports_by_pid.setdefault(pid, set())
        elif field == "c" and pid is not None:
            names_by_pid[pid] = value
        elif field == "n" and pid is not None:
            port = value.rsplit(":", 1)[-1]
            if port.isdigit():
                ports_by_pid[pid].add(int(port))
    return ports_by_pid, names_by_pid


def parse_cwds(lsof_output: str) -> Dict[int, str]:
    """Devuelve el directorio de trabajo por PID a partir de `lsof -d cwd -F pn`."""
    cwd_by_pid: Dict[int, str] = {}
    pid: Optional[int] = None
    for line in lsof_output.splitlines():
        field, value = line[:1], line[1:]
        if field == "p":
            pid = int(value)
        elif field == "n" and pid is not None:
            cwd_by_pid[pid] = value
    return cwd_by_pid


def parse_ps(ps_output: str) -> Dict[int, ProcessInfo]:
    """Parsea `ps -o pid=,etime=,comm=`; el ejecutable va al final y puede tener espacios."""
    info_by_pid: Dict[int, ProcessInfo] = {}
    for line in ps_output.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit():
            info_by_pid[int(parts[0])] = ProcessInfo(elapsed=parts[1], executable=parts[2])
    return info_by_pid


def parse_command_lines(ps_output: str) -> Dict[int, str]:
    """Parsea `ps -o pid=,args=`."""
    command_line_by_pid: Dict[int, str] = {}
    for line in ps_output.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            command_line_by_pid[int(parts[0])] = parts[1]
    return command_line_by_pid


def attach_command_lines(
    info_by_pid: Dict[int, ProcessInfo], command_line_by_pid: Dict[int, str]
) -> Dict[int, ProcessInfo]:
    return {
        pid: replace(info, command_line=command_line_by_pid.get(pid, ""))
        for pid, info in info_by_pid.items()
    }


def build_apps(
    ports_by_pid: Dict[int, Set[int]],
    names_by_pid: Dict[int, str],
    cwd_by_pid: Dict[int, str],
    info_by_pid: Dict[int, ProcessInfo],
) -> List[App]:
    apps = []
    for pid, ports in ports_by_pid.items():
        if not ports:
            continue
        info = info_by_pid.get(pid, ProcessInfo(elapsed="?", executable=""))
        name = os.path.basename(info.executable) or names_by_pid.get(pid, "?")
        apps.append(App(
            pid=pid,
            name=name,
            ports=tuple(sorted(ports)),
            executable=info.executable,
            cwd=cwd_by_pid.get(pid, ""),
            elapsed=info.elapsed,
            command_line=info.command_line,
        ))
    return sorted(apps, key=lambda app: app.ports[0])


def shorten_home(path: str) -> str:
    if path == HOME or path.startswith(HOME + os.sep):
        return "~" + path[len(HOME):]
    return path


def format_ports(ports: Tuple[int, ...]) -> str:
    return ",".join(str(port) for port in ports)


def parse_parents(ps_output: str) -> Dict[int, int]:
    """Parsea `ps -A -o pid=,ppid=`."""
    parent_by_pid: Dict[int, int] = {}
    for line in ps_output.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            parent_by_pid[int(parts[0])] = int(parts[1])
    return parent_by_pid


def ancestors(pid: int, parent_by_pid: Dict[int, int]) -> List[int]:
    """Devuelve el PID y sus ancestros, del más cercano al más lejano (sin launchd)."""
    chain: List[int] = []
    current: Optional[int] = pid
    while current is not None and current > 1 and current not in chain:
        chain.append(current)
        current = parent_by_pid.get(current)
    return chain


def parse_log_candidates(lsof_output: str) -> List[str]:
    """Ficheros regulares de `lsof -F ftn` que parecen logs: stdout/stderr redirigidos primero, luego *.log."""
    redirected: List[str] = []
    log_files: List[str] = []
    fd, file_type = "", ""
    for line in lsof_output.splitlines():
        field, value = line[:1], line[1:]
        if field == "f":
            fd, file_type = value, ""
        elif field == "t":
            file_type = value
        elif field == "n" and file_type == "REG":
            if fd in ("1", "2"):
                redirected.append(value)
            elif value.endswith(".log"):
                log_files.append(value)
    return list(dict.fromkeys(redirected + log_files))


def orphan_log_names(file_names: List[str], is_alive: Callable[[int], bool]) -> List[str]:
    """Logs de `localapps run` (`<pid>.log`) cuyo envoltorio ya no existe; ignora cualquier otro fichero."""
    orphans = []
    for name in file_names:
        match = WRAPPER_LOG_NAME.match(name)
        if match and not is_alive(int(match.group(1))):
            orphans.append(name)
    return orphans


def parse_pids(lsof_output: str) -> Set[int]:
    """Parsea `lsof -t`: un PID por línea."""
    return {int(line) for line in lsof_output.split() if line.isdigit()}


def clean_log_line(line: str) -> str:
    """Quita secuencias ANSI y caracteres de control; los tabuladores pasan a 4 espacios."""
    text = ANSI_ESCAPE.sub("", line).replace("\t", "    ")
    return CONTROL_CHARS.sub("", text)


def log_window(lines: List[str], height: int, scroll: int) -> Tuple[List[str], int]:
    """Devuelve las líneas visibles y el desplazamiento acotado; `scroll` cuenta líneas desde el final."""
    if height <= 0:
        return [], 0
    clamped = max(0, min(scroll, len(lines) - height))
    end = len(lines) - clamped
    return lines[max(0, end - height):end], clamped


# --- Recogida de datos (I/O) ------------------------------------------------

def run_command(args: List[str]) -> str:
    # lsof sale con código 1 cuando no encuentra nada: no es un error para nosotros.
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
    return result.stdout


def collect_apps() -> List[App]:
    ports_by_pid, names_by_pid = parse_listening_ports(run_command(LSOF_LISTEN))
    if not ports_by_pid:
        return []
    pid_list = ",".join(str(pid) for pid in ports_by_pid)
    cwd_by_pid = parse_cwds(run_command(["lsof", "-a", "-p", pid_list, "-d", "cwd", "-Fpn"]))
    info_by_pid = attach_command_lines(
        parse_ps(run_command(["ps", "-o", "pid=,etime=,comm=", "-p", pid_list])),
        parse_command_lines(run_command(["ps", "-o", "pid=,args=", "-p", pid_list])),
    )
    return build_apps(ports_by_pid, names_by_pid, cwd_by_pid, info_by_pid)


def process_start_time(pid: int) -> str:
    """Hora de arranque del proceso; cadena vacía si ya no existe."""
    return run_command(["ps", "-o", "lstart=", "-p", str(pid)]).strip()


def is_same_process(expected: App, current: Optional[App]) -> bool:
    """Mismo ejecutable y mismos puertos; la hora de arranque se comprueba aparte (`confirm_kill`)."""
    return (
        current is not None
        and current.executable == expected.executable
        and current.ports == expected.ports
    )


def terminate(app: App) -> str:
    try:
        os.kill(app.pid, signal.SIGTERM)
    except ProcessLookupError:
        return "El proceso {} ya no existe".format(app.pid)
    except PermissionError:
        return "Sin permiso para matar {} (PID {})".format(app.name, app.pid)
    return "SIGTERM enviado a {} (PID {})".format(app.name, app.pid)


def open_in_browser(app: App) -> str:
    url = "http://localhost:{}".format(app.ports[0])
    subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "Abriendo {}".format(url)


def wrapper_log_path(pid: int) -> str:
    return os.path.join(LOG_DIR, "{}.log".format(pid))


def find_log_file(app: App) -> Optional[str]:
    """Busca el log de una app: el de `localapps run` de algún ancestro, o un fichero que tenga abierto."""
    parent_by_pid = parse_parents(run_command(["ps", "-A", "-o", "pid=,ppid="]))
    for pid in ancestors(app.pid, parent_by_pid):
        path = wrapper_log_path(pid)
        # Solo vale si el envoltorio lo tiene abierto: si no, es un log viejo de un PID reutilizado.
        if os.path.isfile(path) and pid in parse_pids(run_command(["lsof", "-t", path])):
            return path
    candidates = parse_log_candidates(run_command(["lsof", "-a", "-nP", "-p", str(app.pid), "-Fftn"]))
    return candidates[0] if candidates else None


def read_log_lines(path: str) -> List[str]:
    try:
        with open(path, "rb") as log:
            size = log.seek(0, os.SEEK_END)
            log.seek(max(0, size - LOG_TAIL_BYTES))
            text = log.read().decode("utf-8", errors="replace")
    except OSError as error:
        return ["No se puede leer {}: {}".format(path, error.strerror)]
    lines = text.splitlines()
    if size > LOG_TAIL_BYTES:
        lines = lines[1:]  # La primera línea del bloque leído suele estar cortada.
    return [clean_log_line(line) for line in lines]


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Existe, aunque sea de otro usuario.
    return True


def remove_orphan_logs() -> None:
    """Borra los logs que dejó un `localapps run` que murió sin limpiar (p. ej. con SIGKILL)."""
    for name in orphan_log_names(os.listdir(LOG_DIR), is_process_alive):
        try:
            os.unlink(os.path.join(LOG_DIR, name))
        except FileNotFoundError:
            pass


def exit_on_signal(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)


def run_with_log(command: List[str]) -> int:
    """Ejecuta el comando en una pseudo-terminal (conserva colores y Ctrl+C) y copia su salida al log."""
    if shutil.which(command[0]) is None:
        print("localapps: no se encuentra el comando '{}'".format(command[0]), file=sys.stderr)
        return 127
    os.makedirs(LOG_DIR, mode=0o700, exist_ok=True)
    remove_orphan_logs()
    path = wrapper_log_path(os.getpid())
    try:
        os.unlink(path)  # Un log con nuestro PID es de un proceso anterior que reutilizó el PID.
    except FileNotFoundError:
        pass
    try:
        # Los logs pueden contener datos sensibles (tokens, datos de usuarios): solo los lee el propietario.
        # O_EXCL|O_NOFOLLOW: no seguir un enlace plantado ni reutilizar un fichero ajeno.
        log_fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        print("localapps: no se puede crear el log {}: {}".format(path, error.strerror), file=sys.stderr)
        return 1
    print("localapps: guardando logs en {}".format(shorten_home(path)), file=sys.stderr)

    def copy_to_log(master_fd: int) -> bytes:
        data = os.read(master_fd, 4096)
        if not data:
            # En macOS el fin del comando llega como lectura vacía y pty.spawn (Python < 3.10)
            # se quedaría esperando para siempre; un OSError es lo que pty.spawn trata como fin.
            raise OSError("fin de la salida del comando")
        os.write(log_fd, data)
        return data

    # pty.spawn pone la terminal en modo raw y solo la restaura si termina por OSError.
    stdin_fd = pty.STDIN_FILENO
    tty_mode = termios.tcgetattr(stdin_fd) if os.isatty(stdin_fd) else None
    # Python deja SIGHUP/SIGTERM en SIG_DFL y el proceso moriría sin ejecutar el `finally`.
    # Al salir se cierra el maestro del pty y el comando recibe SIGHUP.
    previous_handlers = {signum: signal.signal(signum, exit_on_signal) for signum in (signal.SIGHUP, signal.SIGTERM)}
    try:
        status = pty.spawn(command, copy_to_log)
    finally:
        try:
            os.close(log_fd)
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            finally:
                try:
                    if tty_mode is not None:
                        termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, tty_mode)
                except (termios.error, OSError):
                    pass  # Con la pestaña cerrada la TTY ya no existe (EIO).
                finally:
                    for signum, handler in previous_handlers.items():
                        signal.signal(signum, handler)
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 128 + os.WTERMSIG(status)


# --- TUI --------------------------------------------------------------------

def visible_apps(apps: List[App], show_hidden: bool) -> List[App]:
    return apps if show_hidden else [app for app in apps if not app.is_hidden_by_default]


def selected_index(apps: List[App], selected_pid: Optional[int]) -> int:
    for index, app in enumerate(apps):
        if app.pid == selected_pid:
            return index
    return 0


@dataclass
class LogView:
    app: App
    path: Optional[str]
    scroll: int = 0  # Líneas desde el final; 0 = seguir el log en tiempo real.


class LocalAppsTui:
    def __init__(self, screen) -> None:
        self.screen = screen
        self.apps: List[App] = []
        self.selected_pid: Optional[int] = None
        self.show_hidden = False
        self.pending_kill: Optional[App] = None
        self.pending_kill_started = ""
        self.log_view: Optional[LogView] = None
        self.list_offset = 0
        self.message = ""
        self.last_refresh = 0.0

    def run(self) -> None:
        curses.curs_set(0)
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        curses.mousemask(MOUSE_CLICK | MOUSE_WHEEL_UP | MOUSE_WHEEL_DOWN)
        curses.mouseinterval(0)
        self.screen.timeout(INPUT_TIMEOUT_MS)
        while True:
            if self.log_view is None and time.monotonic() - self.last_refresh >= REFRESH_SECONDS:
                self.refresh_apps()
            self.render()
            key = self.screen.getch()
            if key != -1 and not self.handle_key(key):
                return

    def refresh_apps(self) -> None:
        self.apps = collect_apps()
        self.last_refresh = time.monotonic()

    def current_rows(self) -> List[App]:
        return visible_apps(self.apps, self.show_hidden)

    def selected_app(self) -> Optional[App]:
        rows = self.current_rows()
        return rows[selected_index(rows, self.selected_pid)] if rows else None

    def move(self, delta: int) -> None:
        rows = self.current_rows()
        if not rows:
            return
        index = max(0, min(len(rows) - 1, selected_index(rows, self.selected_pid) + delta))
        self.selected_pid = rows[index].pid

    def open_logs(self, app: App) -> None:
        self.log_view = LogView(app=app, path=find_log_file(app))
        self.pending_kill = None
        self.message = ""

    def log_page_height(self) -> int:
        height, _ = self.screen.getmaxyx()
        return max(0, height - 3)

    # --- Entrada ---

    def handle_key(self, key: int) -> bool:
        """Procesa una tecla o evento de ratón; devuelve False para salir."""
        if key == curses.KEY_MOUSE:
            if self.pending_kill is not None:
                self.pending_kill = None
                self.message = "Cancelado"
            self.handle_mouse()
            return True
        if self.log_view is not None:
            self.handle_log_key(key)
            return True
        return self.handle_list_key(key)

    def handle_mouse(self) -> None:
        try:
            _, _, y, _, button_state = curses.getmouse()
        except curses.error:
            return  # Evento de ratón que ncurses no sabe decodificar.
        if self.log_view is not None:
            if button_state & MOUSE_WHEEL_UP:
                self.log_view.scroll += MOUSE_WHEEL_LINES
            elif MOUSE_WHEEL_DOWN and button_state & MOUSE_WHEEL_DOWN:
                self.log_view.scroll = max(0, self.log_view.scroll - MOUSE_WHEEL_LINES)
            return
        if not button_state & MOUSE_CLICK:
            return
        rows = self.current_rows()
        index = self.list_offset + y - LIST_FIRST_ROW
        if y >= LIST_FIRST_ROW and 0 <= index < len(rows):
            self.selected_pid = rows[index].pid
            self.open_logs(rows[index])

    def handle_log_key(self, key: int) -> None:
        view = self.log_view
        page = max(1, self.log_page_height() - 1)
        if key in (ord("q"), 27, curses.KEY_LEFT):
            self.log_view = None
            self.last_refresh = 0.0
        elif key in (curses.KEY_UP, ord("k")):
            view.scroll += 1
        elif key in (curses.KEY_DOWN, ord("j")):
            view.scroll = max(0, view.scroll - 1)
        elif key == curses.KEY_PPAGE:
            view.scroll += page
        elif key in (curses.KEY_NPAGE, ord(" ")):
            view.scroll = max(0, view.scroll - page)
        elif key == ord("G"):
            view.scroll = 0

    def confirm_kill(self, expected: App, started: str) -> None:
        """Mata solo si el PID sigue siendo el mismo proceso que se confirmó."""
        self.refresh_apps()
        current = next((a for a in self.apps if a.pid == expected.pid), None)
        now_started = process_start_time(expected.pid)
        if is_same_process(expected, current) and now_started and now_started == started:
            self.message = terminate(expected)
        else:
            self.message = "{} (PID {}) ha cambiado o ya no existe; no se ha matado".format(
                expected.name, expected.pid)

    def handle_list_key(self, key: int) -> bool:
        if self.pending_kill is not None:
            if key in (ord("y"), ord("Y")):
                self.confirm_kill(self.pending_kill, self.pending_kill_started)
                self.last_refresh = 0.0
            else:
                self.message = "Cancelado"
            self.pending_kill = None
            self.pending_kill_started = ""
            return True

        app = self.selected_app()
        if key in (ord("q"), 27):
            return False
        if key in (curses.KEY_UP, ord("k")):
            self.move(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.move(1)
        elif key in (curses.KEY_ENTER, 10, 13, ord("l")) and app:
            self.open_logs(app)
        elif key == ord("s"):
            self.show_hidden = not self.show_hidden
        elif key == ord("r"):
            self.last_refresh = 0.0
        elif key == ord("o") and app:
            self.message = open_in_browser(app)
        elif key == ord("x") and app:
            self.pending_kill = app
            self.pending_kill_started = process_start_time(app.pid)
            self.message = "¿Matar {} (PID {})? [y/N]".format(app.name, app.pid)
        return True

    # --- Pintado ---

    def render(self) -> None:
        self.screen.erase()
        if self.log_view is not None:
            self.render_logs(self.log_view)
        else:
            self.render_list()
        self.screen.refresh()

    def render_list(self) -> None:
        height, width = self.screen.getmaxyx()
        rows = self.current_rows()
        hidden = len(self.apps) - len(rows)

        title = " localapps · {} apps · {}{} ".format(
            len(rows),
            time.strftime("%H:%M:%S"),
            " · {} ocultas (sistema/herramientas/IA)".format(hidden) if hidden else "",
        )
        self.write(0, title.ljust(width), width, curses.A_REVERSE | curses.A_BOLD)
        self.write(2, "{:>7}  {:<18} {:<22} {:>12}  {}".format("PID", "PUERTOS", "APP", "TIEMPO", "PROYECTO"),
                   width, curses.A_BOLD)

        selected = selected_index(rows, self.selected_pid)
        list_height = max(0, height - 6)
        self.list_offset = max(0, selected - list_height + 1)
        for line, app in enumerate(rows[self.list_offset:self.list_offset + list_height]):
            text = "{:>7}  {:<18} {:<22} {:>12}  {}".format(
                app.pid, format_ports(app.ports)[:18], app.name[:22], app.elapsed, shorten_home(app.cwd))
            attr = curses.A_DIM if app.is_hidden_by_default else curses.color_pair(1)
            if self.list_offset + line == selected:
                attr = curses.A_REVERSE
            self.write(LIST_FIRST_ROW + line, text.ljust(width), width, attr)

        if not rows:
            self.write(LIST_FIRST_ROW, "  No hay apps escuchando en puertos TCP locales.", width, curses.A_DIM)
        if self.message:
            self.write(height - 2, " " + self.message, width, curses.color_pair(2))
        self.write(height - 1, LIST_FOOTER.ljust(width), width, curses.A_REVERSE)

    def render_logs(self, view: LogView) -> None:
        height, width = self.screen.getmaxyx()
        source = shorten_home(view.path) if view.path else "sin logs accesibles"
        status = "siguiendo" if view.scroll == 0 else "pausado (G para seguir)"
        title = " Logs · {} (PID {}) · {} · {} ".format(view.app.name, view.app.pid, source, status)
        self.write(0, title.ljust(width), width, curses.A_REVERSE | curses.A_BOLD)

        if view.path is None:
            self.render_missing_logs(view.app, width)
        else:
            lines, view.scroll = log_window(read_log_lines(view.path), self.log_page_height(), view.scroll)
            for offset, line in enumerate(lines):
                self.write(1 + offset, line, width)
        self.write(height - 1, LOG_FOOTER.ljust(width), width, curses.A_REVERSE)

    def render_missing_logs(self, app: App, width: int) -> None:
        hints = [
            "{} escribe su salida directamente en la terminal donde se lanzó,".format(app.name),
            "y localapps no puede leer lo que ya se mostró en otra pestaña.",
            "",
            "Para ver sus logs aquí, párala y relánzala desde su carpeta con:",
            "",
            "    localapps run -- <el comando que uses, p. ej. dotnet watch --project Api>",
            "",
            "También se detectan apps que redirigen su salida a un fichero (> app.log)",
            "o que tienen abierto un fichero .log.",
        ]
        for offset, hint in enumerate(hints):
            self.write(2 + offset, "  " + hint, width, curses.color_pair(2) if offset == 5 else 0)

    def write(self, y: int, text: str, width: int, attr: int = 0) -> None:
        height, _ = self.screen.getmaxyx()
        if 0 <= y < height:
            try:
                self.screen.addnstr(y, 0, text, width - 1, attr)
            except (curses.error, ValueError):
                pass  # Terminal redimensionada durante el pintado o texto con caracteres que curses rechaza; se repinta en el siguiente ciclo.


USAGE = """uso:
  localapps                      abre la TUI con las apps escuchando en puertos locales
  localapps run -- <comando>     lanza <comando> guardando sus logs para verlos en la TUI"""


def main(argv: List[str]) -> int:
    if sys.platform != "darwin":
        print("localapps solo está soportado en macOS.", file=sys.stderr)
        return 1
    if argv[:1] == ["run"]:
        command = argv[2:] if argv[1:2] == ["--"] else argv[1:]
        if not command:
            print(USAGE, file=sys.stderr)
            return 2
        return run_with_log(command)
    if argv:
        print(USAGE, file=sys.stderr)
        return 0 if argv[0] in ("-h", "--help") else 2
    try:
        curses.wrapper(lambda screen: LocalAppsTui(screen).run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
