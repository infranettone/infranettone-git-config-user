"""GTK 3 window: pick a base folder, see the git identity of every repo below it, fix it."""

from __future__ import annotations

import re
import subprocess
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import __version__, core  # noqa: E402

APP_ID = "com.infranettone.GitConfigUser"

STATUS_TEXT = {
    core.STATUS_OK: ("emblem-ok-symbolic", "Correcto"),
    core.STATUS_WRONG: ("dialog-error-symbolic", "Incorrecto"),
    core.STATUS_UNKNOWN: ("dialog-question-symbolic", "Desconocido"),
    core.STATUS_MISSING: ("dialog-warning-symbolic", "Sin configurar"),
    core.STATUS_ERROR: ("dialog-error-symbolic", "Error"),
}
FILTERS = [("all", "Todos"), ("problems", "Solo con problemas"), (core.STATUS_OK, "Correctos"),
           (core.STATUS_WRONG, "Incorrectos"), (core.STATUS_UNKNOWN, "Desconocidos"),
           (core.STATUS_MISSING, "Sin configurar")]
SCOPE_TEXT = {"local": "local", "worktree": "worktree", "global": "global", "system": "sistema",
              "command": "línea de comandos", "": "—"}

# ListStore columns
C_PATH, C_ICON, C_STATUS_TXT, C_REL, C_NAME, C_EMAIL, C_SCOPE, C_EXPECTED, C_REMOTE, C_STATUS, C_TIP = range(11)


class Window(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Git Config User")
        self.set_default_size(1100, 680)
        self.settings = core.load_settings()
        self.scanner = core.Scanner()
        self.repos: list[core.RepoInfo] = []
        self._scanning = False
        self._timer = 0

        header = Gtk.HeaderBar(show_close_button=True, title="Git Config User",
                               subtitle=self.settings.base_dir or "Elige una carpeta base")
        self.set_titlebar(header)
        self.header = header

        folder_btn = Gtk.Button(label="Carpeta base…")
        folder_btn.connect("clicked", lambda *_: self.on_choose_folder())
        header.pack_start(folder_btn)
        self.refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        self.refresh_btn.set_tooltip_text("Volver a analizar ahora")
        self.refresh_btn.connect("clicked", lambda *_: self.rescan(force=True))
        header.pack_start(self.refresh_btn)
        self.spinner = Gtk.Spinner()
        header.pack_start(self.spinner)
        profiles_btn = Gtk.Button(label="Perfiles…")
        profiles_btn.connect("clicked", lambda *_: self.on_profiles())
        header.pack_end(profiles_btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, border_width=10)
        self.add(box)

        # Summary + filters
        top = Gtk.Box(spacing=8)
        self.summary = Gtk.Label(xalign=0, hexpand=True)
        top.pack_start(self.summary, True, True, 0)
        self.search = Gtk.SearchEntry(placeholder_text="Buscar ruta, nombre, email o remoto")
        self.search.set_size_request(300, -1)
        self.search.connect("search-changed", lambda *_: self.filter.refilter())
        top.pack_start(self.search, False, False, 0)
        self.status_filter = Gtk.ComboBoxText()
        for key, text in FILTERS:
            self.status_filter.append(key, text)
        self.status_filter.set_active_id("all")
        self.status_filter.connect("changed", lambda *_: self.filter.refilter())
        top.pack_start(self.status_filter, False, False, 0)
        box.pack_start(top, False, False, 0)

        # Repo table
        self.store = Gtk.ListStore(str, str, str, str, str, str, str, str, str, str, str)
        self.filter = self.store.filter_new()
        self.filter.set_visible_func(self._visible)
        sorted_model = Gtk.TreeModelSort(model=self.filter)
        sorted_model.set_sort_column_id(C_REL, Gtk.SortType.ASCENDING)
        self.view = Gtk.TreeView(model=sorted_model, tooltip_column=C_TIP, enable_search=False)
        self.view.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.view.get_selection().connect("changed", lambda *_: self._update_actions())
        self.view.connect("row-activated", lambda _v, path, _c: self._open_folder(path))

        col = Gtk.TreeViewColumn("Estado")
        icon = Gtk.CellRendererPixbuf()
        col.pack_start(icon, False)
        col.add_attribute(icon, "icon-name", C_ICON)
        txt = Gtk.CellRendererText()
        col.pack_start(txt, True)
        col.add_attribute(txt, "text", C_STATUS_TXT)
        col.set_sort_column_id(C_STATUS_TXT)
        self.view.append_column(col)
        for title, column, expand in (("Repositorio", C_REL, True), ("user.name", C_NAME, False),
                                      ("user.email", C_EMAIL, False), ("Definido en", C_SCOPE, False),
                                      ("Perfil esperado", C_EXPECTED, False)):
            renderer = Gtk.CellRendererText(ellipsize=3 if expand else 0)  # 3 = Pango.EllipsizeMode.END
            c = Gtk.TreeViewColumn(title, renderer, text=column)
            c.set_resizable(True)
            c.set_expand(expand)
            c.set_sort_column_id(column)
            self.view.append_column(c)
        scroll = Gtk.ScrolledWindow(vexpand=True, shadow_type=Gtk.ShadowType.IN)
        scroll.add(self.view)
        box.pack_start(scroll, True, True, 0)

        # Actions on the selection
        actions = Gtk.Box(spacing=6)
        actions.pack_start(Gtk.Label(label="Seleccionados:"), False, False, 0)
        self.profile_combo = Gtk.ComboBoxText()
        actions.pack_start(self.profile_combo, False, False, 0)
        self.assign_btn = Gtk.Button(label="Asignar perfil (config local)")
        self.assign_btn.get_style_context().add_class("suggested-action")
        self.assign_btn.connect("clicked", lambda *_: self.on_assign())
        actions.pack_start(self.assign_btn, False, False, 0)
        self.expected_btn = Gtk.Button(label="Aplicar el perfil esperado")
        self.expected_btn.set_tooltip_text("Pone en cada repo el perfil cuyas reglas coinciden con él")
        self.expected_btn.connect("clicked", lambda *_: self.on_apply_expected())
        actions.pack_start(self.expected_btn, False, False, 0)
        self.unset_btn = Gtk.Button(label="Quitar config local")
        self.unset_btn.set_tooltip_text("Borra user.name y user.email de .git/config; se hereda la global")
        self.unset_btn.connect("clicked", lambda *_: self.on_unset())
        actions.pack_start(self.unset_btn, False, False, 0)
        footer = Gtk.Label(xalign=1)
        footer.set_markup(f"<small>v{__version__}</small>")
        actions.pack_end(footer, False, False, 0)
        box.pack_start(actions, False, False, 0)

        self.message = Gtk.Label(xalign=0, wrap=True, selectable=True)
        box.pack_start(self.message, False, False, 0)

        self._fill_profile_combo()
        self._update_actions()
        self._restart_timer()
        if self.settings.base_dir:
            self.rescan()
        else:
            self.summary.set_markup("Elige una <b>carpeta base</b> para empezar.")

    # -- scanning ------------------------------------------------------------

    def _restart_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add_seconds(max(2, self.settings.interval), self._tick)

    def _tick(self) -> bool:
        if self.settings.base_dir and self.get_visible():
            self.rescan()
        return True

    def rescan(self, force: bool = False) -> None:
        base = self.settings.base_dir
        if not base or self._scanning:
            return
        if not Path(base).is_dir():
            self.summary.set_markup(f"La carpeta base <b>{GLib.markup_escape_text(base)}</b> no existe.")
            return
        if force:
            self.scanner.invalidate()
        self._scanning = True
        self.spinner.start()

        def work():
            try:
                repos, error = self.scanner.scan(Path(base)), None
            except Exception as e:  # surface anything unexpected
                repos, error = [], e
            GLib.idle_add(done, repos, error)

        def done(repos, error):
            self._scanning = False
            self.spinner.stop()
            if error is not None:
                self._say(f"Error al analizar: {error}", error=True)
            elif base == self.settings.base_dir:
                self.repos = repos
                self._render()
            return False

        threading.Thread(target=work, daemon=True).start()

    def _render(self) -> None:
        selected = set(self._selected_paths())
        base = Path(self.settings.base_dir)
        counts = dict.fromkeys(STATUS_TEXT, 0)
        self.store.clear()
        for r in self.repos:
            status, expected = core.evaluate(r, self.settings.profiles)
            counts[status] += 1
            icon, text = STATUS_TEXT[status]
            rel = str(r.path.relative_to(base)) if r.path != base else "."
            scope = SCOPE_TEXT.get(r.email.scope or r.name.scope, r.email.scope)
            if r.name.scope and r.email.scope and r.name.scope != r.email.scope:
                scope = f"{SCOPE_TEXT.get(r.name.scope)} / {SCOPE_TEXT.get(r.email.scope)}"
            tip = [f"<b>{GLib.markup_escape_text(str(r.path))}</b>"]
            if r.remote:
                shown = re.sub(r"://[^/@]*@", "://", r.remote)  # never show embedded tokens
                tip.append(f"Remoto: {GLib.markup_escape_text(shown)}")
            for label, v in (("user.name", r.name), ("user.email", r.email)):
                if v.origin:
                    tip.append(f"{label} de {GLib.markup_escape_text(v.origin)}")
            if r.error:
                tip.append(GLib.markup_escape_text(r.error))
            self.store.append([str(r.path), icon, text, rel, r.name.value or "—", r.email.value or "—",
                               scope, expected.label if expected else "", r.remote, status, "\n".join(tip)])
        problems = len(self.repos) - counts[core.STATUS_OK]
        parts = [f"<b>{len(self.repos)}</b> repositorios", f"{counts[core.STATUS_OK]} correctos"]
        for status, label in ((core.STATUS_WRONG, "incorrectos"), (core.STATUS_UNKNOWN, "desconocidos"),
                              (core.STATUS_MISSING, "sin configurar"), (core.STATUS_ERROR, "con error")):
            if counts[status]:
                parts.append(f"<span foreground='#c01c28'>{counts[status]} {label}</span>")
        if not problems and self.repos:
            parts.append("todo en orden")
        self.summary.set_markup(" · ".join(parts))
        self._reselect(selected)
        self._update_actions()

    def _visible(self, model, it, _data) -> bool:
        mode = self.status_filter.get_active_id()
        status = model[it][C_STATUS]
        if mode == "problems" and status == core.STATUS_OK:
            return False
        if mode not in ("all", "problems") and status != mode:
            return False
        q = self.search.get_text().strip().lower()
        if q:
            haystack = " ".join(model[it][c] or "" for c in (C_REL, C_NAME, C_EMAIL, C_REMOTE)).lower()
            return q in haystack
        return True

    # -- selection -----------------------------------------------------------

    def _selected_paths(self) -> list[str]:
        model, rows = self.view.get_selection().get_selected_rows()
        return [model[r][C_PATH] for r in rows]

    def _reselect(self, paths: set[str]) -> None:
        if not paths:
            return
        sel = self.view.get_selection()
        model = self.view.get_model()
        it = model.get_iter_first()
        while it is not None:
            if model[it][C_PATH] in paths:
                sel.select_iter(it)
            it = model.iter_next(it)

    def _update_actions(self) -> None:
        has_sel = bool(self._selected_paths())
        self.assign_btn.set_sensitive(has_sel and bool(self.settings.profiles))
        self.expected_btn.set_sensitive(has_sel)
        self.unset_btn.set_sensitive(has_sel)

    def _open_folder(self, tree_path) -> None:
        path = self.view.get_model()[tree_path][C_PATH]
        Gio.AppInfo.launch_default_for_uri(Path(path).as_uri(), None)

    # -- actions -------------------------------------------------------------

    def _say(self, text: str, error: bool = False) -> None:
        esc = GLib.markup_escape_text(text)
        self.message.set_markup(f"<span foreground='#c01c28'>{esc}</span>" if error else esc)

    def _confirm(self, text: str, detail: str) -> bool:
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.OK_CANCEL, text=text)
        dlg.format_secondary_text(detail)
        ok = dlg.run() == Gtk.ResponseType.OK
        dlg.destroy()
        return ok

    def _apply(self, jobs: list[tuple[str, callable]], verb: str) -> None:
        errors = []
        for path, job in jobs:
            try:
                job()
            except (OSError, subprocess.SubprocessError) as e:
                detail = getattr(e, "stderr", "") or str(e)
                errors.append(f"{path}: {detail.strip()}")
            self.scanner.invalidate(Path(path))
        if errors:
            self._say(f"{verb} con errores:\n" + "\n".join(errors), error=True)
        else:
            self._say(f"{verb}: {len(jobs)} repositorio(s).")
        self.rescan()

    def on_assign(self) -> None:
        idx = self.profile_combo.get_active()
        paths = self._selected_paths()
        if idx < 0 or not paths:
            return
        p = self.settings.profiles[idx]
        if not self._confirm(f"¿Asignar {p.label}?",
                             f"Se escribe user.name y user.email en la config local de {len(paths)} repositorio(s)."):
            return
        self._apply([(r, lambda r=r: core.set_local_identity(Path(r), p.name, p.email)) for r in paths],
                    "Perfil asignado")

    def on_apply_expected(self) -> None:
        by_path = {str(r.path): r for r in self.repos}
        jobs, skipped = [], 0
        for path in self._selected_paths():
            exp = core.expected_profile(by_path[path], self.settings.profiles) if path in by_path else None
            if exp is None:
                skipped += 1
                continue
            jobs.append((path, lambda path=path, exp=exp: core.set_local_identity(Path(path), exp.name, exp.email)))
        if not jobs:
            self._say("Ningún repositorio seleccionado coincide con las reglas de un perfil.", error=True)
            return
        extra = f" {skipped} no coinciden con ninguna regla y se dejan igual." if skipped else ""
        if self._confirm("¿Aplicar el perfil esperado?",
                         f"Se escribe la identidad en la config local de {len(jobs)} repositorio(s).{extra}"):
            self._apply(jobs, "Perfil esperado aplicado")

    def on_unset(self) -> None:
        paths = self._selected_paths()
        if paths and self._confirm("¿Quitar la identidad local?",
                                   f"Se borran user.name y user.email de la config local de {len(paths)} "
                                   "repositorio(s). Pasarán a usar la config global."):
            self._apply([(r, lambda r=r: core.unset_local_identity(Path(r))) for r in paths],
                        "Config local quitada")

    def on_choose_folder(self) -> None:
        dlg = Gtk.FileChooserNative(title="Carpeta base", transient_for=self,
                                    action=Gtk.FileChooserAction.SELECT_FOLDER, accept_label="Elegir")
        if self.settings.base_dir:
            dlg.set_current_folder(self.settings.base_dir)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            self.settings.base_dir = dlg.get_filename()
            core.save_settings(self.settings)
            self.header.set_subtitle(self.settings.base_dir)
            self.repos = []
            self.store.clear()
            self.summary.set_text("Analizando…")
            self.rescan(force=True)
        dlg.destroy()

    def on_profiles(self) -> None:
        dlg = ProfilesDialog(self, self.settings)
        if dlg.run() == Gtk.ResponseType.OK:
            self.settings.profiles = dlg.profiles()
            self.settings.interval = dlg.interval.get_value_as_int()
            core.save_settings(self.settings)
            self._fill_profile_combo()
            self._restart_timer()
            self._render()
        dlg.destroy()

    def _fill_profile_combo(self) -> None:
        self.profile_combo.remove_all()
        for p in self.settings.profiles:
            self.profile_combo.append_text(p.label)
        if self.settings.profiles:
            self.profile_combo.set_active(0)
        self._update_actions()


class ProfilesDialog(Gtk.Dialog):
    """Edit the list of identities (name, email, rules)."""

    def __init__(self, parent: Gtk.Window, settings: core.Settings):
        super().__init__(title="Perfiles", transient_for=parent, modal=True)
        self.set_default_size(820, 440)
        self.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        self.add_button("Guardar", Gtk.ResponseType.OK).get_style_context().add_class("suggested-action")
        box = self.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)

        help_lbl = Gtk.Label(xalign=0, wrap=True)
        help_lbl.set_markup(
            "Cada perfil es una identidad que usas. Las <b>reglas</b> son patrones separados por comas "
            "que se comparan con la URL del remoto <i>origin</i> y con la ruta del repositorio, por ejemplo "
            "<tt>*github.com/infranettone/*</tt> o <tt>/home/yo/trabajo/*</tt>. Si un repo coincide con las "
            "reglas de un perfil y tiene otra identidad, sale como <b>Incorrecto</b>. El primer perfil que "
            "coincide gana. Edita las celdas con doble clic.")
        box.pack_start(help_lbl, False, False, 0)

        self.store = Gtk.ListStore(str, str, str)
        for p in settings.profiles:
            self.store.append([p.name, p.email, ", ".join(p.patterns)])
        self.view = Gtk.TreeView(model=self.store, reorderable=True)
        for i, title in enumerate(("Nombre (user.name)", "Email (user.email)", "Reglas")):
            r = Gtk.CellRendererText(editable=True)
            r.connect("edited", self._edited, i)
            c = Gtk.TreeViewColumn(title, r, text=i)
            c.set_resizable(True)
            c.set_expand(i == 2)
            self.view.append_column(c)
        scroll = Gtk.ScrolledWindow(vexpand=True, shadow_type=Gtk.ShadowType.IN)
        scroll.add(self.view)
        box.pack_start(scroll, True, True, 0)

        row = Gtk.Box(spacing=6)
        add = Gtk.Button(label="Añadir perfil")
        add.connect("clicked", lambda *_: self._add())
        row.pack_start(add, False, False, 0)
        remove = Gtk.Button(label="Quitar")
        remove.connect("clicked", lambda *_: self._remove())
        row.pack_start(remove, False, False, 0)
        self.interval = Gtk.SpinButton.new_with_range(2, 3600, 1)
        self.interval.set_value(settings.interval)
        row.pack_end(Gtk.Label(label="s"), False, False, 0)
        row.pack_end(self.interval, False, False, 0)
        row.pack_end(Gtk.Label(label="Volver a analizar cada"), False, False, 0)
        box.pack_start(row, False, False, 0)
        self.show_all()

    def _edited(self, _r, path, text, col) -> None:
        self.store[path][col] = text.strip()

    def _add(self) -> None:
        it = self.store.append(["Nombre Apellido", "tu@email.com", ""])
        self.view.set_cursor(self.store.get_path(it), self.view.get_column(0), True)

    def _remove(self) -> None:
        model, it = self.view.get_selection().get_selected()
        if it is not None:
            model.remove(it)

    def profiles(self) -> list[core.Profile]:
        result = []
        for name, email, patterns in self.store:
            if name or email:
                result.append(core.Profile(name, email, [p.strip() for p in patterns.split(",") if p.strip()]))
        return result


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.get_active_window() or Window(self)
        win.show_all()
        win.present()


def main() -> int:
    return App().run(None)
