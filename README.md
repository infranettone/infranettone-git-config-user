# Git Config User

App de escritorio para Ubuntu que muestra qué `user.name` y `user.email` usa cada uno de tus repositorios git locales, y avisa cuando alguno no es el que toca.

## Por qué

Muchos desarrolladores usan varias identidades: la del trabajo, la personal, la de cada cliente… Basta con clonar un repo y olvidarse de `git config user.email` para que los commits salgan con el correo equivocado, y eso no se ve hasta que ya están publicados.

Git Config User analiza una **carpeta base** de forma recursiva y lista todos los repositorios que hay dentro con:

- el `user.name` y `user.email` **efectivos** (lo que git usará al hacer commit);
- **de dónde salen**: config local del repo, global (`~/.gitconfig`), un `includeIf`, del sistema…;
- su **estado** respecto a tus perfiles.

| Estado | Significado |
| --- | --- |
| Correcto | La identidad es la de un perfil y, si hay reglas, la del perfil que toca. |
| Incorrecto | El repo coincide con las reglas de un perfil pero tiene otra identidad. |
| Desconocido | La identidad no es la de ninguno de tus perfiles. |
| Sin configurar | Falta `user.name` o `user.email`: git no dejaría hacer commit. |

## Uso

1. Abre **Git Config User** desde el menú de aplicaciones (o ejecuta `git-config-user`).
2. Pulsa **Carpeta base…** y elige la carpeta donde tienes los repos (por ejemplo `~/repos`). Se queda guardada.
3. En **Perfiles…** añade tus identidades. A cada una le puedes poner **reglas**: patrones separados por comas que se comparan con la URL del remoto `origin` y con la ruta del repo.

   | Nombre | Email | Reglas |
   | --- | --- | --- |
   | Ana López | ana@empresa.com | `*github.com/empresa/*, /home/ana/trabajo/*` |
   | Ana López | ana@personal.dev | `*github.com/analopez/*` |

   Las URL SSH (`git@github.com:empresa/api.git`) se comparan también como `github.com/empresa/api.git`, así que la misma regla sirve para SSH y HTTPS. Si varios perfiles coinciden, gana el primero (se pueden reordenar arrastrando).
4. La tabla se actualiza sola cada pocos segundos (configurable en **Perfiles…**) y con el botón de refrescar. Solo se vuelve a leer un repo cuando cambia su `.git/config`, la config global o un fichero incluido, así que funciona bien con cientos de repos.
5. Selecciona uno o varios repos y:
   - **Asignar perfil**: escribe el perfil elegido en la config local del repo (`git config --local`);
   - **Aplicar el perfil esperado**: pone en cada repo el perfil cuyas reglas coinciden con él;
   - **Quitar config local**: borra `user.name` y `user.email` del repo para que use la global.

   Doble clic en un repo abre su carpeta. Pasa el ratón por encima para ver la ruta, el remoto y los ficheros de los que sale cada valor.

La app solo escribe en la config **local** de los repos que selecciones y siempre pide confirmación. Nunca toca `~/.gitconfig`. No se recorren `node_modules`, `target`, `.venv` ni directorios parecidos, ni se siguen enlaces simbólicos.

La configuración de la app está en `~/.config/infranettone-git-config-user/settings.json`.

## Instalación

Descarga el `.deb` de la última [release](https://github.com/infranettone/infranettone-git-config-user/releases) e instálalo:

```sh
sudo apt install ./git-config-user_*_all.deb
```

Dependencias: `python3`, `python3-gi`, `gir1.2-gtk-3.0` y `git`. Ubuntu Desktop ya las trae, salvo `git`.

## Sin la app

```sh
# Identidad efectiva de un repo y de dónde sale
git -C ruta/al/repo config --show-scope --show-origin --get-regexp '^user\.'

# Identidad automática por carpeta (en ~/.gitconfig)
[includeIf "gitdir:~/trabajo/"]
    path = ~/.gitconfig-trabajo
```

La app detecta las identidades que vienen de `includeIf`, así que las dos cosas se pueden combinar.

## Desarrollo

```sh
python3 -m unittest discover -s tests -v    # tests (usan un HOME temporal, no tocan tu config)
PYTHONPATH=src python3 -m git_config_user   # ejecutar la UI sin instalar
./scripts/build-deb.sh                       # build/git-config-user_<versión>_all.deb
```

## Publicar una versión

Sube `__version__` en `src/git_config_user/__init__.py` y haz push a `main`. El workflow `release.yml` construye el `.deb` y publica la release `v<versión>` si no existe.

## Licencia

[MIT](LICENSE).
