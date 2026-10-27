# localapps

TUI para macOS que muestra en tiempo real (cada 2 s) las apps que escuchan en puertos TCP locales: PID, puertos, app, tiempo en marcha y carpeta del proyecto.

Solo usa la librería estándar de Python 3.7+ (`curses`) y los comandos `lsof`, `ps` y `open` del sistema.

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
- Aborta si `~/.local/bin/localapps` ya existe y no es un enlace a este repo. Bórralo o usa otra carpeta con `LOCALAPPS_BIN_DIR`.
- Si `~/.warp/workflows/localapps.yaml` ya existe y difiere del del repo, o es un enlace simbólico, lo conserva y avisa. Para actualizarlo, bórralo y vuelve a ejecutar `./install.sh`. Volver a ejecutarlo es seguro.

Para desinstalar: `./uninstall.sh`. Solo borra el enlace si apunta a este repo, y el workflow solo si es idéntico al del repo; si lo has modificado, lo conserva y avisa. Si instalaste con `LOCALAPPS_BIN_DIR`, desinstala con la misma variable: `LOCALAPPS_BIN_DIR=/ruta ./uninstall.sh`.

## Ver los logs de una app

Haz clic en una app (o selecciónala y pulsa `Enter`/`l`) para ver sus logs en tiempo real.

Una app que escribe en la terminal donde se lanzó no deja su salida accesible desde fuera, así que para ver sus logs hay que lanzarla con `localapps run`:

```bash
cd ~/proyectos/mi-api
localapps run -- dotnet watch --project Api
```

`localapps run` ejecuta el comando en una pseudo-terminal (se conservan los colores y `Ctrl+C`) y copia la salida en `~/Library/Logs/localapps/<pid>.log` (permisos `600`). El fichero se borra cuando el comando termina, también si cierras la pestaña (SIGHUP) o si recibe SIGTERM; en ambos casos el comando lanzado recibe SIGHUP y termina. Si el envoltorio muere con SIGKILL, el log huérfano se borra en el siguiente `localapps run`. La TUI encuentra el log aunque la app que escucha sea un proceso hijo del comando, como ocurre con `dotnet watch`.

Sin `localapps run`, también se muestran los logs de apps que redirigen su salida a un fichero (`> app.log`) o que tienen abierto un fichero `.log`.

La vista de logs elimina secuencias de escape y caracteres de control, y no se cae si el log contiene bytes nulos.

En Warp, el clic y la rueda necesitan la opción *Enable Mouse Reporting* de los ajustes activada.

## Teclas

### Lista

| Tecla | Acción |
|---|---|
| `↑` `↓` / `k` `j` | Mover la selección |
| Clic / `Enter` / `l` | Abrir los logs de la app |
| `o` | Abrir `http://localhost:<primer puerto>` en el navegador |
| `x` | Matar el proceso (SIGTERM, pide confirmación `y`). Antes comprueba que el PID sigue siendo la misma app (mismo ejecutable, puertos y hora de arranque); si ha cambiado, no la mata y avisa. Un clic durante la confirmación la cancela |
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
