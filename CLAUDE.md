# CLAUDE.md — Git Config User

El uso y el porqué, en `README.md`.

## Estructura

- `src/git_config_user/core.py`: toda la lógica (buscar repos, leer y escribir la identidad con `git config`, perfiles, ajustes). No importa GTK, así que se puede probar.
- `src/git_config_user/ui.py`: ventana GTK 3 (PyGObject). El análisis va en un hilo y vuelve con `GLib.idle_add`. El "tiempo real" es un `GLib.timeout` que vuelve a analizar; `core.Scanner` solo relee un repo si cambió el mtime de alguno de sus ficheros de config (local, global, incluidos).
- `scripts/build-deb.sh`: `.deb` `Architecture: all` hecho con `dpkg-deb`. Instala en `/usr/lib/git-config-user` y `/usr/bin/git-config-user`.
- La versión solo está en `src/git_config_user/__init__.py`.

## Regla número uno: la config real no se toca

`~/.gitconfig` y los repos del usuario son **reales**. Los tests usan un `HOME` y `GIT_CONFIG_GLOBAL` temporales. Para probar la UI, `XDG_CONFIG_HOME` temporal y solo lectura sobre repos reales: no se asigna ni se quita identidad en repos reales sin que el usuario lo pida.

## Idioma

- Código y comentarios: inglés.
- Textos de la UI, documentación y respuestas: castellano.
- Commits: en inglés, terminan con `Co-Authored-By`. Autor: Raul Adamuz <radamuzc@gmail.com>.

## Antes de dar un cambio por bueno

```sh
python3 -m unittest discover -s tests -v
./scripts/build-deb.sh
```

## Trampas conocidas

- `git config -z --show-scope --show-origin --get-regexp` devuelve `scope\0origin\0clave\nvalor\0`; el último valor es el efectivo.
- Se ejecuta git con `-c safe.directory=*` para que los repos de otro usuario no fallen con "dubious ownership".
- Si la app instalada está abierta, `python3 -m git_config_user` no abre otra ventana: solo activa la instalada (mismo `APP_ID` en D-Bus). Y crear `ui.Window` a mano en un script da segfault. Para probar con la instalada abierta: `ui.APP_ID += ".Dev"` antes de crear `ui.App()`.
- Las URL SSH tipo scp (`git@host:org/repo`) se normalizan a `host/org/repo` para que las reglas sirvan igual que con HTTPS.

## CI

Runners alojados de GitHub (`ubuntu-22.04`), actions ancladas a SHA, igual que los demás repos de infranettone. `release.yml` publica cuando cambia la versión.
