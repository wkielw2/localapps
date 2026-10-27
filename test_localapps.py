import os
import tempfile
import unittest
from unittest import mock

import localapps

from localapps import (
    HOME,
    App,
    ProcessInfo,
    attach_command_lines,
    ancestors,
    build_apps,
    clean_log_line,
    log_window,
    main,
    format_ports,
    parse_command_lines,
    parse_cwds,
    parse_listening_ports,
    parse_log_candidates,
    parse_parents,
    read_log_lines,
    run_with_log,
    parse_ps,
    selected_index,
    shorten_home,
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
