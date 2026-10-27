# localapps

TUI para macOS que muestra en tiempo real (cada 2 s) las apps que escuchan en puertos TCP locales: PID, puertos, app, tiempo en marcha y carpeta del proyecto.

Solo usa la librería estándar de Python 3.7+ (`curses`) y los comandos `lsof` y `ps` del sistema.

## Instalación

Requisitos: macOS y Python 3.7 o superior (viene con las Command Line Tools de Xcode: `xcode-select --install`).

```bash
git clone https://github.com/wkielw2/localapps.git ~/Documents/Proyectos/localapps
cd ~/Documents/Proyectos/localapps
./install.sh
```

`install.sh`:

- Enlaza `localapps.py` en `~/.local/bin/localapps` (otra carpeta con `LOCALAPPS_BIN_DIR=/ruta ./install.sh`). Como es un enlace, `git pull` actualiza la versión instalada; no muevas ni borres la carpeta del repo.
- Copia el workflow `warp/localapps.yaml` a `~/.warp/workflows/` para lanzarla desde Warp (`Cmd+P` → "Apps corriendo en local").
- Avisa si `~/.local/bin` no está en el `PATH` y te da la línea para añadirlo a `~/.zshrc`.

Para desinstalar: `./uninstall.sh`.

## Ver los logs de una app

Haz clic en una app (o selecciónala y pulsa `Enter`/`l`) para ver sus logs en tiempo real.

Una app que escribe en la terminal donde se lanzó no deja su salida accesible desde fuera, así que para ver sus logs hay que lanzarla con `localapps run`:

```bash
cd ~/proyectos/mi-api
localapps run -- dotnet watch --project Api
```

`localapps run` ejecuta el comando en una pseudo-terminal (se conservan los colores y `Ctrl+C`) y copia la salida en `~/Library/Logs/localapps/<pid>.log` (permisos `600`). El fichero se borra cuando el comando termina. La TUI encuentra el log aunque la app que escucha sea un proceso hijo del comando, como ocurre con `dotnet watch`.

Sin `localapps run`, también se muestran los logs de apps que redirigen su salida a un fichero (`> app.log`) o que tienen abierto un fichero `.log`.

En Warp, el clic y la rueda necesitan la opción *Enable Mouse Reporting* de los ajustes activada.

## Teclas

### Lista

| Tecla | Acción |
|---|---|
| `↑` `↓` / `k` `j` | Mover la selección |
| Clic / `Enter` / `l` | Abrir los logs de la app |
| `o` | Abrir `http://localhost:<primer puerto>` en el navegador |
| `x` | Matar el proceso (SIGTERM, pide confirmación `y`) |
| `s` | Mostrar u ocultar los procesos ocultos por defecto: sistema (`/System/`, `/usr/libexec/`, `/usr/sbin/`) y herramientas instaladas como `.app` (`/Applications/`, `~/Applications/`: Warp, VS Code, DBeaver...), CLIs de agentes de IA (Claude Code, Codex) y supervisores de desarrollo como `dotnet watch` |
| `r` | Refrescar ya |
| `q` / `Esc` | Salir |

### Logs

| Tecla | Acción |
|---|---|
| `↑` `↓` / `k` `j` / rueda | Desplazar línea a línea (pausa el seguimiento). Con Python < 3.10 la rueda solo sube; para bajar usa el teclado o `G` |
| `PgUp` / `PgDn` / `Espacio` | Desplazar por páginas |
| `G` | Volver al final y seguir el log en tiempo real |
| `q` / `Esc` / `←` | Volver a la lista |

Solo ve los procesos de tu usuario; los de otros usuarios o root no aparecen sin `sudo`.

## Tests

```bash
python3 -m unittest -v test_localapps
```
