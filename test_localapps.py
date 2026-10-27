import curses
import os
import signal
import subprocess
import sys
import tempfile
import termios
import unittest
from unittest import mock

import localapps

from localapps import (
    HOME,
    App,
    LocalAppsTui,
    ProcessInfo,
    attach_command_lines,
    ancestors,
    build_apps,
    clean_log_line,
    is_process_alive,
    is_same_process,
    process_start_time,
    remove_orphan_logs,
    find_log_file,
    log_window,
    main,
    orphan_log_names,
    format_ports,
    parse_command_lines,
    parse_cwds,
    parse_listening_ports,
    parse_log_candidates,
    parse_parents,
    parse_pids,
    read_log_lines,
    run_with_log,
    parse_ps,
    selected_index,
    shorten_home,
    wrapper_log_path,
    visible_apps,
)


def make_app(pid=1, ports=(3000,), executable="/usr/local/bin/node", command_line=""):
    return App(pid=pid, name="node", ports=ports, executable=executable, cwd="/tmp", elapsed="01:00",
               command_line=command_line)


class ParseListeningPortsTest(unittest.TestCase):
    def test_agrupa_puertos_ipv4_e_ipv6_por_pid(self):
        output = "p608\ncrapportd\nf11\nn*:52528\nf12\nn*:52528\np74049\ncMiApi\nf349\nn127.0.0.1:5183\nf350\nn[::1]:5184\n"

        ports, names = parse_listening_ports(output)

        self.assertEqual(ports, {608: {52528}, 74049: {5183, 5184}})
        self.assertEqual(names, {608: "rapportd", 74049: "MiApi"})

    def test_devuelve_vacio_si_lsof_no_encuentra_nada(self):
        self.assertEqual(parse_listening_ports(""), ({}, {}))


class ParseCwdsTest(unittest.TestCase):
    def test_asocia_cada_directorio_a_su_pid(self):
        output = "p100\nfcwd\nn/Users/me/app\np200\nfcwd\nn/\n"

        self.assertEqual(parse_cwds(output), {100: "/Users/me/app", 200: "/"})


class ParsePsTest(unittest.TestCase):
    def test_conserva_espacios_en_la_ruta_del_ejecutable(self):
        output = "  608      01-02:03:04 /usr/libexec/rapportd\n68229   10:05 /Applications/Code.app/Contents/MacOS/Code Helper (Plugin)\n"

        info = parse_ps(output)

        self.assertEqual(info[608], ProcessInfo("01-02:03:04", "/usr/libexec/rapportd"))
        self.assertEqual(info[68229].executable, "/Applications/Code.app/Contents/MacOS/Code Helper (Plugin)")

    def test_ignora_lineas_mal_formadas(self):
        self.assertEqual(parse_ps("basura\n\n"), {})


class ParseCommandLinesTest(unittest.TestCase):
    def test_conserva_los_argumentos_completos(self):
        output = "74040 /usr/local/share/dotnet/dotnet /sdk/dotnet-watch.dll --project Api\nbasura\n"

        self.assertEqual(parse_command_lines(output),
                         {74040: "/usr/local/share/dotnet/dotnet /sdk/dotnet-watch.dll --project Api"})


class AttachCommandLinesTest(unittest.TestCase):
    def test_anade_la_linea_de_comandos_a_cada_proceso(self):
        info = {1: ProcessInfo("01:00", "/opt/node"), 2: ProcessInfo("02:00", "/opt/java")}

        result = attach_command_lines(info, {1: "node server.js"})

        self.assertEqual(result[1], ProcessInfo("01:00", "/opt/node", "node server.js"))
        self.assertEqual(result[2].command_line, "")


class BuildAppsTest(unittest.TestCase):
    def test_usa_el_nombre_del_ejecutable_y_ordena_por_puerto(self):
        apps = build_apps(
            ports_by_pid={1: {8080}, 2: {3000, 3001}},
            names_by_pid={1: "java", 2: "node"},
            cwd_by_pid={2: "/Users/me/web"},
            info_by_pid={1: ProcessInfo("05:00", "/usr/bin/java"), 2: ProcessInfo("01:00", "/opt/node")},
        )

        self.assertEqual([app.pid for app in apps], [2, 1])
        self.assertEqual(apps[0], App(2, "node", (3000, 3001), "/opt/node", "/Users/me/web", "01:00"))
        self.assertEqual(apps[1].cwd, "")

    def test_recurre_al_nombre_de_lsof_si_el_proceso_ya_no_existe(self):
        apps = build_apps({7: {9000}}, {7: "stable"}, {}, {})

        self.assertEqual((apps[0].name, apps[0].elapsed), ("stable", "?"))

    def test_descarta_pids_sin_puertos(self):
        self.assertEqual(build_apps({7: set()}, {}, {}, {}), [])


class VisibleAppsTest(unittest.TestCase):
    def test_oculta_sistema_y_herramientas_salvo_que_se_pidan(self):
        dev = make_app(pid=1)
        system = make_app(pid=2, executable="/System/Library/CoreServices/ControlCenter.app/ControlCenter")
        tool = make_app(pid=3, executable="/Applications/DBeaver.app/Contents/MacOS/dbeaver")
        user_tool = make_app(pid=4, executable=HOME + "/Applications/Cursor.app/Contents/MacOS/Cursor")

        self.assertEqual(visible_apps([dev, system, tool, user_tool], show_hidden=False), [dev])
        self.assertEqual(visible_apps([dev, system, tool, user_tool], show_hidden=True), [dev, system, tool, user_tool])

    def test_oculta_supervisores_de_desarrollo_como_dotnet_watch(self):
        watcher = make_app(executable="/usr/local/share/dotnet/dotnet",
                           command_line="/usr/local/share/dotnet/dotnet /sdk/dotnet-watch.dll --project Api")
        dotnet_app = make_app(pid=2, executable="/usr/local/share/dotnet/dotnet",
                              command_line="/usr/local/share/dotnet/dotnet Api.dll")

        self.assertEqual(visible_apps([watcher, dotnet_app], show_hidden=False), [dotnet_app])

    def test_oculta_clis_de_ia_lanzadas_desde_el_path(self):
        codex = make_app(pid=1, executable="codex")
        claude = make_app(pid=2, executable="claude")
        host = make_app(pid=3, executable="codex-code-mode-host")
        normal = make_app(pid=4)

        self.assertEqual(visible_apps([codex, claude, host, normal], show_hidden=False), [normal])
        self.assertEqual(visible_apps([codex, claude, host, normal], show_hidden=True),
                         [codex, claude, host, normal])

    def test_oculta_clis_de_ia_por_su_ruta_de_instalacion(self):
        claude = make_app(pid=1, executable=HOME + "/.local/share/claude/versions/9.9.9")
        codex_cask = make_app(pid=2, executable="/opt/homebrew/Caskroom/codex/0.0.1/bin/codex")
        codex_host = make_app(pid=3, executable=HOME + "/.codex/packages/x/bin/codex-code-mode-host")

        self.assertEqual(visible_apps([claude, codex_cask, codex_host], show_hidden=False), [])

    def test_no_oculta_apps_con_nombre_o_ruta_parecidos_a_los_clis_de_ia(self):
        similar_name = make_app(pid=1, executable="codex-dashboard")
        similar_path = make_app(pid=2, executable=HOME + "/Proyectos/claude-demo/server")

        self.assertEqual(visible_apps([similar_name, similar_path], show_hidden=False), [similar_name, similar_path])

    def test_no_oculta_apps_sin_ejecutable_conocido(self):
        # build_apps usa "" cuando ps no devuelve información del proceso.
        unknown = make_app(pid=1, executable="")

        self.assertEqual(visible_apps([unknown], show_hidden=False), [unknown])

    def test_no_oculta_directorios_que_solo_comparten_el_inicio_con_las_rutas_de_ia(self):
        similar_dir = make_app(pid=1, executable=HOME + "/.codex-sandbox/bin/server")

        self.assertEqual(visible_apps([similar_dir], show_hidden=False), [similar_dir])

    def test_muestra_proyectos_aunque_esten_dentro_de_home(self):
        project = make_app(executable=HOME + "/Proyectos/api/bin/Debug/net8.0/Api")

        self.assertEqual(visible_apps([project], show_hidden=False), [project])


class SelectedIndexTest(unittest.TestCase):
    def test_mantiene_la_seleccion_por_pid_y_vuelve_al_inicio_si_desaparece(self):
        apps = [make_app(pid=1), make_app(pid=2)]

        self.assertEqual(selected_index(apps, 2), 1)
        self.assertEqual(selected_index(apps, 99), 0)


class FormattingTest(unittest.TestCase):
    def test_acorta_rutas_bajo_home(self):
        self.assertEqual(shorten_home(HOME + "/Proyectos/web"), "~/Proyectos/web")
        self.assertEqual(shorten_home(HOME + "otro"), HOME + "otro")
        self.assertEqual(shorten_home("/tmp"), "/tmp")

    def test_une_puertos_con_comas(self):
        self.assertEqual(format_ports((3000, 3001)), "3000,3001")


class AncestorsTest(unittest.TestCase):
    def test_sube_por_los_padres_hasta_launchd(self):
        parents = parse_parents("  74049 74048\n74048 74040\n74040 74039\n74039 1\n    1 0\n")

        self.assertEqual(ancestors(74049, parents), [74049, 74048, 74040, 74039])

    def test_no_entra_en_bucle_si_ps_devuelve_un_ciclo(self):
        self.assertEqual(ancestors(5, {5: 6, 6: 5}), [5, 6])


class ParseLogCandidatesTest(unittest.TestCase):
    def test_prioriza_la_salida_redirigida_y_luego_los_ficheros_log(self):
        output = ("p10\nfcwd\ntDIR\nn/app\nf1\ntREG\nn/app/out.txt\nf2\ntREG\nn/app/out.txt\n"
                  "f7\ntREG\nn/app/logs/server.log\nf8\ntREG\nn/app/lib.dll\n")

        self.assertEqual(parse_log_candidates(output), ["/app/out.txt", "/app/logs/server.log"])

    def test_ignora_tuberias_y_terminales(self):
        output = "p10\nf1\ntPIPE\nn->0x52\nf2\ntCHR\nn/dev/ttys003\n"

        self.assertEqual(parse_log_candidates(output), [])


class CleanLogLineTest(unittest.TestCase):
    def test_quita_colores_ansi_retornos_de_carro_y_tabuladores(self):
        line = "\x1b[32minfo\x1b[0m: Microsoft.Hosting.Lifetime[14]\r"

        self.assertEqual(clean_log_line(line), "info: Microsoft.Hosting.Lifetime[14]")
        self.assertEqual(clean_log_line("a\tb"), "a    b")

    def test_quita_nul_y_caracteres_de_control_c0_y_c1(self):
        self.assertEqual(clean_log_line("a\x00b"), "ab")
        self.assertEqual(clean_log_line("a\x9bb"), "ab")
        self.assertEqual(clean_log_line("\x07a\x7fb"), "ab")

    def test_quita_osc_terminado_en_bel_o_en_st(self):
        self.assertEqual(clean_log_line("\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\"), "link")
        self.assertEqual(clean_log_line("\x1b]0;título\x07x"), "x")

    def test_conserva_texto_unicode_imprimible(self):
        self.assertEqual(clean_log_line("ñandú € 😀"), "ñandú € 😀")


class WriteTest(unittest.TestCase):
    def test_no_lanza_si_curses_rechaza_el_texto(self):
        screen = mock.Mock()
        screen.getmaxyx.return_value = (24, 80)
        screen.addnstr.side_effect = ValueError("embedded null character")
        tui = LocalAppsTui(screen)

        tui.write(0, "x", 80)


class LogWindowTest(unittest.TestCase):
    LINES = ["l1", "l2", "l3", "l4", "l5"]

    def test_sigue_el_final_por_defecto(self):
        self.assertEqual(log_window(self.LINES, 2, 0), (["l4", "l5"], 0))

    def test_desplaza_hacia_arriba_sin_pasarse_del_principio(self):
        self.assertEqual(log_window(self.LINES, 2, 1), (["l3", "l4"], 1))
        self.assertEqual(log_window(self.LINES, 2, 99), (["l1", "l2"], 3))

    def test_muestra_todo_si_cabe(self):
        self.assertEqual(log_window(self.LINES, 10, 4), (self.LINES, 0))


class ReadLogLinesTest(unittest.TestCase):
    def test_lee_solo_el_final_y_descarta_la_linea_cortada(self):
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as log:
            log.write("primera linea larga\nsegunda\ntercera\n")
        self.addCleanup(os.remove, log.name)

        tail_starting_inside_first_line = len("a\nsegunda\ntercera\n")
        with mock.patch.object(localapps, "LOG_TAIL_BYTES", tail_starting_inside_first_line):
            self.assertEqual(read_log_lines(log.name), ["segunda", "tercera"])

    def test_informa_si_el_fichero_no_existe(self):
        self.assertTrue(read_log_lines("/no/existe.log")[0].startswith("No se puede leer /no/existe.log"))


class RunWithLogTest(unittest.TestCase):
    def setUp(self):
        self.log_dir = tempfile.mkdtemp()
        patcher = mock.patch.object(localapps, "LOG_DIR", self.log_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_devuelve_el_codigo_de_salida_y_borra_el_log_al_terminar(self):
        with mock.patch("sys.stderr"):
            exit_code = run_with_log(["sh", "-c", "echo hola; exit 3"])

        self.assertEqual(exit_code, 3)
        self.assertEqual(os.listdir(self.log_dir), [])

    def test_guarda_la_salida_mientras_el_comando_corre(self):
        with mock.patch("sys.stderr"):
            run_with_log(["sh", "-c", "echo hola; sleep 0.2; cat {0}/*.log > {0}/copia.txt".format(self.log_dir)])

        with open(os.path.join(self.log_dir, "copia.txt")) as copy:
            self.assertIn("hola", copy.read())

    def test_falla_con_127_si_el_comando_no_existe(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(run_with_log(["comando-que-no-existe-xyz"]), 127)

    def test_borra_el_log_y_sale_con_128_mas_la_senal_si_matan_al_envoltorio(self):
        # En un subproceso para no matar al runner; el `sh` es hijo directo del envoltorio (pty.fork).
        test_dir = os.path.dirname(os.path.abspath(__file__))
        for signal_name, expected_code in (("TERM", 143), ("HUP", 129)):
            with self.subTest(signal=signal_name):
                script = ("import localapps, sys; localapps.LOG_DIR = {!r}; "
                          "sys.exit(localapps.run_with_log(['sh', '-c', 'kill -{} $PPID; sleep 1']))"
                          ).format(self.log_dir, signal_name)

                result = subprocess.run([sys.executable, "-B", "-c", script], cwd=test_dir,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, timeout=10)

                self.assertEqual(result.returncode, expected_code)
                self.assertEqual(os.listdir(self.log_dir), [])

    def test_borra_al_arrancar_los_logs_de_envoltorios_muertos(self):
        for name in ("11111.log", "22222.log", "notas.txt"):
            open(os.path.join(self.log_dir, name), "w").close()

        with mock.patch.object(localapps, "is_process_alive", lambda pid: pid == 22222), mock.patch("sys.stderr"):
            run_with_log(["true"])

        self.assertEqual(sorted(os.listdir(self.log_dir)), ["22222.log", "notas.txt"])

    def test_no_sigue_un_enlace_plantado_en_la_ruta_del_log(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as target:
            target.write("original")
        self.addCleanup(os.remove, target.name)
        os.symlink(target.name, wrapper_log_path(os.getpid()))

        with mock.patch("sys.stderr"):
            run_with_log(["sh", "-c", "echo hola"])

        with open(target.name) as target_file:
            self.assertEqual(target_file.read(), "original")
        self.assertFalse(os.path.lexists(wrapper_log_path(os.getpid())))

    def test_restaura_los_manejadores_de_senales_previos(self):
        def sentinel_handler(signum, frame):
            pass

        for signum in (signal.SIGTERM, signal.SIGHUP):
            self.addCleanup(signal.signal, signum, signal.signal(signum, sentinel_handler))
        previous = (sentinel_handler, sentinel_handler)

        with mock.patch("sys.stderr"):
            run_with_log(["true"])

        self.assertEqual((signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGHUP)), previous)

    def test_devuelve_1_con_mensaje_si_no_puede_crear_el_log(self):
        with mock.patch("localapps.os.open", side_effect=OSError(13, "Permission denied")), \
                mock.patch("localapps.pty.spawn") as spawn, mock.patch("sys.stderr") as stderr:
            exit_code = run_with_log(["true"])

        self.assertEqual(exit_code, 1)
        spawn.assert_not_called()
        written = "".join(call[0][0] for call in stderr.write.call_args_list)
        self.assertIn("no se puede crear el log", written)
        self.assertIn("Permission denied", written)

    def run_with_fake_tty(self, restore_error):
        saved_mode = ["modo-guardado"]
        with mock.patch("localapps.os.isatty", return_value=True), \
                mock.patch("localapps.termios.tcgetattr", return_value=saved_mode), \
                mock.patch("localapps.termios.tcsetattr", side_effect=restore_error) as restore, \
                mock.patch("localapps.pty.spawn", return_value=0), mock.patch("sys.stderr"):
            exit_code = run_with_log(["true"])
        return exit_code, restore, saved_mode

    def test_restaura_el_modo_original_de_la_tty(self):
        exit_code, restore, saved_mode = self.run_with_fake_tty(None)

        self.assertEqual(exit_code, 0)
        restore.assert_called_once_with(localapps.pty.STDIN_FILENO, termios.TCSAFLUSH, saved_mode)

    def test_un_fallo_al_restaurar_la_tty_no_rompe_la_limpieza(self):
        for error in (termios.error(5, "EIO"), OSError(5, "EIO")):
            with self.subTest(error=type(error).__name__):
                previous = signal.getsignal(signal.SIGTERM)

                exit_code, restore, _ = self.run_with_fake_tty(error)

                self.assertEqual(exit_code, 0)
                restore.assert_called_once()
                self.assertEqual(os.listdir(self.log_dir), [])
                self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_limpia_el_log_y_los_manejadores_si_pty_spawn_falla(self):
        previous = signal.getsignal(signal.SIGHUP)

        with mock.patch("localapps.pty.spawn", side_effect=RuntimeError("fallo")), mock.patch("sys.stderr"):
            with self.assertRaises(RuntimeError):
                run_with_log(["true"])

        self.assertEqual(os.listdir(self.log_dir), [])
        self.assertEqual(signal.getsignal(signal.SIGHUP), previous)

    def test_no_falla_si_el_log_ya_no_existe_al_limpiar(self):
        def spawn_and_delete_log(command, master_read):
            os.unlink(wrapper_log_path(os.getpid()))
            return 0

        with mock.patch("localapps.pty.spawn", side_effect=spawn_and_delete_log), mock.patch("sys.stderr"):
            self.assertEqual(run_with_log(["true"]), 0)

    def test_el_log_se_crea_solo_para_el_propietario(self):
        modes = []

        def spawn_and_stat(command, master_read):
            modes.append(os.stat(wrapper_log_path(os.getpid())).st_mode & 0o777)
            return 0

        with mock.patch("localapps.pty.spawn", side_effect=spawn_and_stat), mock.patch("sys.stderr"):
            run_with_log(["true"])

        self.assertEqual(modes, [0o600])

    def test_sustituye_un_log_antiguo_con_el_mismo_pid(self):
        with open(wrapper_log_path(os.getpid()), "w") as stale:
            stale.write("datos del proceso anterior")
        contents = []

        def spawn_and_read(command, master_read):
            with open(wrapper_log_path(os.getpid())) as log:
                contents.append(log.read())
            return 0

        with mock.patch("localapps.pty.spawn", side_effect=spawn_and_read), mock.patch("sys.stderr"):
            run_with_log(["true"])

        self.assertEqual(contents, [""])


class OrphanLogNamesTest(unittest.TestCase):
    def test_devuelve_solo_logs_de_envoltorio_con_pid_muerto(self):
        names = ["100.log", "200.log", "abc.log", "12.log.bak", "12.txt"]

        self.assertEqual(orphan_log_names(names, lambda pid: pid == 200), ["100.log"])


class ParsePidsTest(unittest.TestCase):
    def test_lee_un_pid_por_linea_e_ignora_lo_demas(self):
        self.assertEqual(parse_pids("123\n456\nbasura\n\n"), {123, 456})


class FindLogFileTest(unittest.TestCase):
    APP_PID, WRAPPER_PID = 300, 200

    def setUp(self):
        self.log_dir = tempfile.mkdtemp()
        patcher = mock.patch.object(localapps, "LOG_DIR", self.log_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log_path = wrapper_log_path(self.WRAPPER_PID)
        open(self.log_path, "w").close()
        self.addCleanup(os.remove, self.log_path)

    def fake_run_command(self, lsof_t_output):
        def run(args):
            if args[0] == "ps":
                return "{} {}\n{} 1\n".format(self.APP_PID, self.WRAPPER_PID, self.WRAPPER_PID)
            if args[:2] == ["lsof", "-t"]:
                return lsof_t_output
            return ""
        return run

    def test_ignora_el_log_si_el_envoltorio_no_lo_tiene_abierto(self):
        with mock.patch.object(localapps, "run_command", side_effect=self.fake_run_command("999\n")):
            self.assertIsNone(find_log_file(make_app(pid=self.APP_PID)))

    def test_usa_el_log_que_el_envoltorio_tiene_abierto(self):
        with mock.patch.object(localapps, "run_command",
                               side_effect=self.fake_run_command("{}\n".format(self.WRAPPER_PID))):
            self.assertEqual(find_log_file(make_app(pid=self.APP_PID)), self.log_path)


class IsProcessAliveTest(unittest.TestCase):
    def test_vivo_si_kill_0_funciona(self):
        with mock.patch("localapps.os.kill") as kill:
            self.assertTrue(is_process_alive(4321))
        kill.assert_called_once_with(4321, 0)

    def test_muerto_si_el_proceso_no_existe(self):
        with mock.patch("localapps.os.kill", side_effect=ProcessLookupError):
            self.assertFalse(is_process_alive(4321))

    def test_vivo_si_pertenece_a_otro_usuario(self):
        with mock.patch("localapps.os.kill", side_effect=PermissionError):
            self.assertTrue(is_process_alive(4321))


class RemoveOrphanLogsTest(unittest.TestCase):
    def test_tolera_que_otro_proceso_borre_el_log_antes(self):
        log_dir = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, log_dir)
        with mock.patch.object(localapps, "LOG_DIR", log_dir), \
                mock.patch("localapps.os.listdir", return_value=["111.log"]), \
                mock.patch("localapps.is_process_alive", return_value=False), \
                mock.patch("localapps.os.unlink", side_effect=FileNotFoundError) as unlink:
            remove_orphan_logs()

        unlink.assert_called_once_with(os.path.join(log_dir, "111.log"))


class ProcessStartTimeTest(unittest.TestCase):
    def test_devuelve_la_hora_sin_espacios_sobrantes(self):
        with mock.patch.object(localapps, "run_command", return_value="Mon Oct 27 09:00:00 2026\n") as run:
            self.assertEqual(process_start_time(42), "Mon Oct 27 09:00:00 2026")
        run.assert_called_once_with(["ps", "-o", "lstart=", "-p", "42"])

    def test_cadena_vacia_si_el_proceso_no_existe(self):
        with mock.patch.object(localapps, "run_command", return_value=""):
            self.assertEqual(process_start_time(42), "")


class IsSameProcessTest(unittest.TestCase):
    def test_mismo_ejecutable_y_puertos(self):
        self.assertTrue(is_same_process(make_app(), make_app()))

    def test_ejecutable_distinto(self):
        self.assertFalse(is_same_process(make_app(), make_app(executable="/usr/bin/python3")))

    def test_puertos_distintos(self):
        self.assertFalse(is_same_process(make_app(), make_app(ports=(4000,))))

    def test_proceso_desaparecido(self):
        self.assertFalse(is_same_process(make_app(), None))


class ConfirmKillTest(unittest.TestCase):
    def make_tui(self, apps):
        screen = mock.Mock()
        screen.getmaxyx.return_value = (24, 80)
        tui = LocalAppsTui(screen)
        tui.apps = apps
        return tui

    def test_clic_cancela_la_confirmacion_y_no_mata_despues(self):
        a, b = make_app(pid=1), make_app(pid=2, ports=(4000,))
        tui = self.make_tui([a, b])
        tui.pending_kill = a
        click = (0, 0, localapps.LIST_FIRST_ROW + 1, 0, curses.BUTTON1_CLICKED)
        with mock.patch("localapps.curses.getmouse", return_value=click), \
                mock.patch("localapps.find_log_file", return_value=None):
            tui.handle_key(curses.KEY_MOUSE)

        self.assertIsNone(tui.pending_kill)
        self.assertEqual(tui.log_view.app, b)

        tui.log_view = None
        with mock.patch("localapps.terminate") as terminate:
            tui.handle_key(ord("y"))
        terminate.assert_not_called()

    def confirm(self, original, current, started_then, started_now):
        tui = self.make_tui([original])
        tui.selected_pid = original.pid
        with mock.patch("localapps.process_start_time", side_effect=[started_then, started_now]), \
                mock.patch("localapps.collect_apps", return_value=current), \
                mock.patch("localapps.terminate", return_value="ok") as terminate:
            tui.handle_key(ord("x"))
            tui.handle_key(ord("y"))
        return tui, terminate

    def test_mata_si_es_el_mismo_proceso(self):
        a = make_app(pid=1)
        _, terminate = self.confirm(a, [make_app(pid=1)], "Mon 9:00", "Mon 9:00")
        terminate.assert_called_once_with(a)

    def test_no_mata_si_cambio_el_ejecutable(self):
        a = make_app(pid=1)
        tui, terminate = self.confirm(a, [make_app(pid=1, executable="/bin/sh")], "Mon 9:00", "Mon 9:00")
        terminate.assert_not_called()
        self.assertIn("no se ha matado", tui.message)

    def test_no_mata_si_el_pid_se_reutilizo(self):
        a = make_app(pid=1)
        tui, terminate = self.confirm(a, [make_app(pid=1)], "Mon 9:00", "Tue 10:00")
        terminate.assert_not_called()
        self.assertIn("no se ha matado", tui.message)

    def test_no_mata_si_el_proceso_ya_no_existe(self):
        a = make_app(pid=1)
        _, terminate = self.confirm(a, [], "Mon 9:00", "")
        terminate.assert_not_called()

    def test_no_mata_si_no_se_pudo_leer_la_hora_de_arranque(self):
        a = make_app(pid=1)
        tui, terminate = self.confirm(a, [make_app(pid=1)], "", "")
        terminate.assert_not_called()
        self.assertIn("no se ha matado", tui.message)

    def test_otra_tecla_distinta_de_y_cancela_sin_matar(self):
        tui = self.make_tui([make_app(pid=1)])
        tui.selected_pid = 1
        with mock.patch("localapps.process_start_time", return_value="Mon 9:00"), \
                mock.patch("localapps.terminate") as terminate:
            tui.handle_key(ord("x"))
            tui.handle_key(ord("n"))
            tui.handle_key(ord("y"))
        terminate.assert_not_called()
        self.assertIsNone(tui.pending_kill)

    def test_abrir_los_logs_cancela_la_confirmacion_pendiente(self):
        a = make_app(pid=1)
        tui = self.make_tui([a])
        tui.pending_kill = a
        with mock.patch("localapps.find_log_file", return_value=None):
            tui.open_logs(a)

        self.assertIsNone(tui.pending_kill)
        self.assertEqual(tui.message, "")

    def test_el_clic_con_confirmacion_pendiente_muestra_cancelado(self):
        a = make_app(pid=1)
        tui = self.make_tui([a])
        tui.pending_kill = a
        with mock.patch("localapps.curses.getmouse", return_value=(0, 0, 0, 0, curses.BUTTON1_CLICKED)):
            tui.handle_key(curses.KEY_MOUSE)

        self.assertIsNone(tui.pending_kill)
        self.assertEqual(tui.message, "Cancelado")


class MainTest(unittest.TestCase):
    def test_run_sin_comando_muestra_el_uso(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(main(["run", "--"]), 2)

    def test_run_pasa_el_comando_tras_el_separador(self):
        with mock.patch.object(localapps, "run_with_log", return_value=0) as run:
            main(["run", "--", "dotnet", "watch", "--project", "Api"])

        run.assert_called_once_with(["dotnet", "watch", "--project", "Api"])


if __name__ == "__main__":
    unittest.main()
