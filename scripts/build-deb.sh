#!/usr/bin/env bash
# Builds build/git-config-user_<version>_all.deb with dpkg-deb (pure Python, no compile).
set -euo pipefail
cd "$(dirname "$0")/.."

version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' src/git_config_user/__init__.py)"
[ -n "$version" ] || { echo "No se pudo leer la versión" >&2; exit 1; }
[ -n "${DEB_VERSION_SUFFIX:-}" ] && version="$version+$DEB_VERSION_SUFFIX"

root="build/pkg"
rm -rf build && mkdir -p "$root/DEBIAN"
install -Dm755 bin/git-config-user "$root/usr/bin/git-config-user"
for f in src/git_config_user/*.py; do
  install -Dm644 "$f" "$root/usr/lib/git-config-user/git_config_user/$(basename "$f")"
done
install -Dm644 packaging/git-config-user.desktop "$root/usr/share/applications/git-config-user.desktop"
install -Dm644 packaging/git-config-user.svg "$root/usr/share/icons/hicolor/scalable/apps/git-config-user.svg"
install -Dm644 README.md "$root/usr/share/doc/git-config-user/README.md"
install -Dm644 LICENSE "$root/usr/share/doc/git-config-user/copyright"

cat > "$root/DEBIAN/control" <<CONTROL
Package: git-config-user
Version: $version
Section: devel
Priority: optional
Architecture: all
Depends: python3 (>= 3.9), python3-gi, gir1.2-gtk-3.0, git
Maintainer: Raul Adamuz <radamuzc@gmail.com>
Homepage: https://github.com/infranettone/infranettone-git-config-user
Description: Revisa qué git config user usa cada repositorio local
 Analiza de forma recursiva una carpeta base y muestra, para cada
 repositorio git, el user.name y el user.email efectivos y de dónde salen.
 Con perfiles y reglas por remoto o ruta avisa de los repositorios con la
 identidad equivocada o sin configurar, y la corrige en la config local.
CONTROL

dpkg-deb --root-owner-group --build "$root" "build/git-config-user_${version}_all.deb"
echo "build/git-config-user_${version}_all.deb"
