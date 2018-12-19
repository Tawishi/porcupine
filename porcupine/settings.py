import codecs
import collections.abc
import json
import logging
import os
import sys
import types

import pygments.styles
import pygments.util    # contains an exception that pygments raises
import pythotk as tk

# get_main_window must be imported from
# porcupine because it imports this before exposing the getter
# functions, and the getter functions cannot be imported from
# porcupine._run because that imports this file (lol)
# that's why "import porcupine"
import porcupine
from porcupine import dirs, filetypes, images


log = logging.getLogger(__name__)


# this is a custom exception because plain ValueError is often raised
# when something goes wrong unexpectedly
class InvalidValue(Exception):
    """Raise this in a callback if the value is invalid.

    Example::

        wat_config = settings.get_section('Wat Wat')

        def validate_indent(indent):
            if indent not in ('tabs', 'spaces'):
                raise settings.InvalidValue(
                    "invalid indent %r, should be 'tabs' or 'spaces'"
                    % repr(indent))

        wat_config.add_option('indent', default='spaces')
        wat_config.connect('indent', validate_indent)

    .. note::
        Be sure to connect validator callbacks before anything else is
        connected. The callbacks are ran in the same order as they are
        connected, and running other callbacks with an invalid value is
        probably not what you want.

    There's no need to do checks like ``isinstance(indent, str)``.
    Python is a dynamically typed language.

    You can also catch ``InvalidValue`` to check if setting a value
    succeeded::

        try:
            wat_config['indent'] = 'wat wat'
        except InvalidValue:
            print("'wat wat' is not a valid indent :(")
    """


# globals ftw
_sections = {}
_loaded_json = {}
_dialog = None          # the "Porcupine Settings" window
_notebook = None        # main widget in the dialog


def get_section(section_name):
    """Return a section object, creating it if it doesn't exist yet.

    The *section_name* is a title of a tab in the *Porcupine Settings*
    dialog, such as ``'General'`` or ``'File Types'``.
    """
    _init()
    try:
        return _sections[section_name]
    except KeyError:
        _sections[section_name] = _ConfigSection(section_name)
        return _sections[section_name]


class _ConfigSection(collections.abc.MutableMapping):

    def __init__(self, name):
        if _notebook is None:
            raise RuntimeError("%s._init() wasn't called" % __name__)

        self.content_frame = tk.Frame(_notebook)
        self.notebook_tab = tk.NotebookTab(self.content_frame, text=name)
        _notebook.append(self.notebook_tab)

        self._name = name
        self._infos = {}
        self._var_cache = {}

    def add_option(self, key, default, *, reset=True):
        """Add a new option without adding widgets to the setting dialog.

        ``section[key]`` will be *default* unless something else is
        specified.

        If *reset* is True, the setting dialog's reset button sets this
        option to *default*.

        .. note::
            The *reset* argument should be False for settings that
            cannot be changed with the dialog. That way, clicking the
            reset button resets only the settings that are shown in the
            dialog.
        """
        var = tk.BooleanVar()   # true when the triangle is showing
        var.set(False)
        self._infos[key] = types.SimpleNamespace(
            default=default,        # not validated
            reset=reset,
            callbacks=[],
            errorvar=var,
        )

    def __setitem__(self, key, value):
        info = self._infos[key]

        old_value = self[key]
        try:
            _loaded_json[self._name][key] = value
        except KeyError:
            _loaded_json[self._name] = {key: value}

        if value != old_value:
            log.debug("%s: %r was set to %r, running callbacks",
                      self._name, key, value)
            for func in info.callbacks:
                try:
                    func(value)
                except InvalidValue as e:
                    _loaded_json[self._name][key] = old_value
                    raise e
                except Exception:
                    try:
                        func_name = func.__module__ + '.' + func.__qualname__
                    except AttributeError:
                        func_name = repr(func)
                    log.exception("%s: %s(%r) didn't work", self._name,
                                  func_name, value)

    def __getitem__(self, key):
        try:
            return _loaded_json[self._name][key]
        except KeyError:
            return self._infos[key].default

    def __delitem__(self, key):    # the abc requires this
        raise TypeError("cannot delete options")

    def __iter__(self):
        return iter(self._infos.keys())

    def __len__(self):
        return len(self._infos)

    def reset(self, key):
        """Set ``section[key]`` back to the default value.

        The value is always reset to the *default* argument passed to
        :meth:`add_option`, regardless of its *reset* argument.

        Resetting is useful for e.g. key bindings. For example, pressing
        Ctrl+0 resets ``font_size``.
        """
        self[key] = self._infos[key].default

    def connect(self, key, callback, run_now=True):
        """
        Schedule ``callback(section[key])`` to be called when the value
        of an option changes.

        If *run_now* is True, ``callback(section[key])`` is also called
        immediately when ``connect()`` is called, and the option is
        reset if the callback raises :exc:`InvalidValue`.

        .. note::
            *run_now* is True by default, so if you don't want to run
            the callback right away you need an explicit
            ``run_now=False``.

        More than one callback can be connected to the same key.
        """
        if run_now:
            try:
                callback(self[key])
            except InvalidValue:
                try:
                    func_name = (callback.__module__ + '.' +
                                 callback.__qualname__)
                except AttributeError:
                    func_name = repr(callback)
                log.warning(
                    "%s: %r value %r is invalid according to %s, resetting"
                    % (self._name, key, self[key], func_name))
                self.reset(key)
        self._infos[key].callbacks.append(callback)

    def disconnect(self, key, callback):
        """Undo a :meth:`~connect` call."""
        self._infos[key].callbacks.remove(callback)

    # returns an image the same size as the triangle image, but empty
    # the image can't be e.g. a global variable because pythotk must not get
    # initialized when this is imported
    @staticmethod
    def _get_fake_triangle(cache=[]):
        if not cache:
            cache.append(tk.Image(
                width=images.get('triangle').width,
                height=images.get('triangle').height))
        return cache[0]

    def get_var(self, key, var_type=tk.StringVar):
        """Return a pythotk variable that is bound to an option.

        Changing the value of the variable updates the config section,
        and changing the value in the section also sets the variable's
        value.

        This returns a ``StringVar`` by default, but you can use the
        ``var_type`` argument to change that. For example,
        ``var_type=tk.BooleanVar`` is suitable for an option that is meant to
        be True or False.

        If an invalid value is set to the variable, it is not set to the
        section but the triangles in the frames returned by
        :meth:`add_frame` are shown.

        Calling this function multiple times with different ``var_type``
        arguments raises :exc:`TypeError`.
        """
        if key in self._var_cache:
            if not isinstance(self._var_cache[key], var_type):
                raise TypeError("get_var(%r, var_type) was called multiple "
                                "times with different var types" % key)
            return self._var_cache[key]

        info = self._infos[key]
        var = var_type()

        def var2config(junk):
            try:
                value = var.get()
            except ValueError:
                info.errorvar.set(True)
                return

            try:
                self[key] = value
            except InvalidValue:
                info.errorvar.set(True)
                return

            info.errorvar.set(False)

        self.connect(key, var.set)      # runs var.set
        var.write_trace.connect(var2config)

        self._var_cache[key] = var
        return var

    def add_frame(self, triangle_key):
        """Add a :class:`pythotk.Frame` to the dialog and return it.

        The frame will contain a label that displays a |triangle| when
        the value of the variable from :meth:`get_var` is invalid. The
        triangle label is packed with ``side='right'``.

        For example, :meth:`add_checkbutton` works roughly like this::

            frame = section.add_frame(key)
            var = section.get_var(key, tk.BooleanVar)
            tk.Checkbutton(frame, text, variable=var).pack(side='left')
        """
        frame = tk.Frame(self.content_frame)
        frame.pack(fill='x')

        if triangle_key is not None:
            errorvar = self._infos[triangle_key].errorvar
            triangle_label = tk.Label(frame)
            triangle_label.pack(side='right')

            def on_errorvar_changed(var):
                if var.get():
                    triangle_label.config['image'] = images.get('triangle')
                else:
                    triangle_label.config['image'] = self._get_fake_triangle()

            errorvar.write_trace.connect(on_errorvar_changed)
            on_errorvar_changed(errorvar)

        return frame

    def add_checkbutton(self, key, text):
        """Add a :class:`pythotk.Checkbutton` that sets an option to a bool."""
        var = self.get_var(key, tk.BooleanVar)
        tk.Checkbutton(self.add_frame(key), text=text,
                       variable=var).pack(side='left')

    def add_entry(self, key, text):
        """Add a :class:`pythotk.Entry` that sets an option to a string."""
        frame = self.add_frame(key)
        tk.Label(frame, text=text).pack(side='left')
        tk.Entry(frame, textvariable=self.get_var(key)).pack(side='right')

    def add_combobox(self, key, choices, text, *, case_sensitive=True):
        """Add a :class:`pythotk.Combobox` that sets an option to a string.

        The combobox will contain each string in *choices*.

        A `validator callback <Validating>`_ that ensures the value is
        in *choices* is also added. If *case_sensitive* is False,
        :meth:`str.casefold` is used when comparing the strings.
        """
        def validator(value):
            if case_sensitive:
                ok = (value in choices)
            else:
                ok = (value.casefold() in map(str.casefold, choices))
            if not ok:
                raise InvalidValue("%r is not a valid %r value"
                                   % (value, key))

        self.connect(key, validator)

        frame = self.add_frame(key)
        tk.Label(frame, text=text).pack(side='left')
        tk.Combobox(frame, values=choices,
                    textvariable=self.get_var(key)).pack(side='right')

    def add_spinbox(self, key, minimum, maximum, text):
        """Add a :class:`pythotk.Spinbox` that sets an option to an integer.

        The *minimum* and *maximum* arguments are used as the bounds for
        the spinbox. A `validator callback <Validating>`_ that makes
        sure the value is between them is also added.

        Note that *minimum* and *maximum* are inclusive, so
        ``minimum=3, maximum=5`` means that 3, 4 and 5 are valid values.
        """
        def validator(value):
            if value < minimum:
                raise InvalidValue("%r is too small" % value)
            if value > maximum:
                raise InvalidValue("%r is too big" % value)

        self.connect(key, validator)

        frame = self.add_frame(key)
        tk.Label(frame, text=text).pack(side='left')
        tk.Spinbox(frame, textvariable=self.get_var(key, tk.IntVar),
                   from_=minimum, to=maximum).pack(side='right')


def _needs_reset():
    for section in _sections.values():
        for key, info in section._info.items():
            if info.default != section[key]:
                return True
    return False


def _do_reset():
    if not _needs_reset:
        tk.dialog.info("Reset Settings",
                       "You are already using the default settings.",
                       parent=_dialog)
        return

    if not tk.dialog.yes_no("Reset Settings",
                            "Are you sure you want to reset all settings?",
                            parent=_dialog):
        return

    for section in _sections.values():
        for key, info in section._infos.items():
            if info.reset:
                section[key] = info.default

    tk.dialog.info("Reset Settings", "All settings were reset to defaults.",
                   parent=_dialog)


def _validate_encoding(name):
    try:
        codecs.lookup(name)
    except LookupError as e:
        raise InvalidValue from e


def _validate_pygments_style_name(name):
    try:
        pygments.styles.get_style_by_name(name)
    except pygments.util.ClassNotFound as e:
        raise InvalidValue(str(e)) from None


def _init():
    global _dialog
    global _notebook

    if _dialog is not None:
        # already initialized
        return

    _dialog = tk.Window("Porcupine Settings")
    _dialog.withdraw()        # hide it for now
    _dialog.on_delete_window.disconnect(tk.quit)
    _dialog.on_delete_window.connect(_dialog.withdraw)
    _dialog.geometry(500, 350)

    _notebook = tk.Notebook(_dialog)
    _notebook.pack(fill='both', expand=True)
    tk.Separator(_dialog).pack(fill='x')
    buttonframe = tk.Frame(_dialog)
    buttonframe.pack(fill='x')
    for text, command in [("Reset", _do_reset), ("OK", _dialog.withdraw)]:
        tk.Button(buttonframe, text=text, command=command).pack(side='right')

    assert not _loaded_json
    try:
        with open(os.path.join(dirs.configdir, 'settings.json'), 'r') as file:
            _loaded_json.update(json.load(file))
    except FileNotFoundError:
        pass      # use defaults everywhere

    general = get_section('General')   # type: _ConfigSection

    fixedfont = tk.NamedFont('TkFixedFont')
    font_families = sorted(tk.Font.families())

    general.add_option('font_family', fixedfont.family)
    general.add_combobox('font_family', font_families, "Font Family:",
                         case_sensitive=False)

    # negative font sizes have a special meaning in tk and the size is negative
    # by default, that's why the stupid hard-coded default size 10
    general.add_option('font_size', 10)
    general.add_spinbox('font_size', 3, 1000, "Font Size:")

    # when font_family changes:  fixedfont.family = new_family
    # when font_size changes:    fixedfont.size = new_size
    # FIXME: should create a new font instead of using TkFixedFont for these
    general.connect(
        'font_family', lambda family: setattr(fixedfont, 'family', family))
    general.connect(
        'font_size', lambda size: setattr(fixedfont, 'size', size))

    # TODO: file-specific encodings
    general.add_option('encoding', 'UTF-8')
    general.add_entry('encoding', "Encoding of opened and saved files:")
    general.connect('encoding', _validate_encoding)

    general.add_option('pygments_style', 'default', reset=False)
    general.connect('pygments_style', _validate_pygments_style_name)

    filetypes_section = get_section('File Types')
    label1 = tk.Label(filetypes_section.content_frame, (
        "Currently there's no GUI for changing filetype specific settings, "
        "but they're stored in filetypes.ini and you can edit it yourself."))
    label2 = tk.Label(filetypes_section.content_frame, (
        "\nYou can use the following option to choose which filetype "
        "Porcupine should use when you create a new file in Porcupine. You "
        "can change the filetype after creating the file clicking Filetypes "
        "in the menu bar."))

    def wrap_automatically(event):
        label1.config['wraplength'] = label2.config['wraplength'] = event.width

    filetypes_section.content_frame.bind(
        '<Configure>', wrap_automatically, event=True)

    def edit_it():
        # porcupine/tabs.py imports this file
        # these local imports feel so evil xD  MUHAHAHAA!!!
        from porcupine import get_tab_manager, tabs

        path = os.path.join(dirs.configdir, 'filetypes.ini')
        manager = get_tab_manager()
        filetab = tabs.FileTab.open_file(manager, path)
        manager.append_and_select(filetab)
        _dialog.withdraw()

    label1.pack(fill='x')
    tk.Button(filetypes_section.content_frame, text="Edit filetypes.ini",
              command=edit_it).pack(anchor='center')
    label2.pack(fill='x')

    names = [filetype.name for filetype in filetypes.get_all_filetypes()]
    filetypes_section.add_option('default_filetype', 'Plain Text')
    filetypes_section.add_combobox('default_filetype', names,
                                   "Default filetype for new files:")


def show_dialog():
    """Show the "Porcupine Settings" dialog.

    This function is called when the user opens the dialog from the menu.
    """
    _init()

    # hide sections with no widgets in the content_frame
    # hide() and unhide() preserve order
    for name, section in _sections.items():
        if section.content_frame.winfo_children():
            section.notebook_tab.unhide()
        else:
            section.notebook_tab.hide()

    _dialog.transient = porcupine.get_main_window()
    _dialog.deiconify()


def save():
    """Save the settings to the config file.

    Note that :func:`porcupine.run` always calls this before it returns,
    so usually you don't need to worry about this yourself.
    """
    if _loaded_json:
        # there's something to save
        # if two porcupines are running and the user changes settings
        # differently in them, the settings of the one that's closed
        # first are discarded
        with open(os.path.join(dirs.configdir, 'settings.json'), 'w') as file:
            json.dump(_loaded_json, file)


# docs/settings.rst relies on this
# FIXME: the [source] links of section methods don't work :(
if 'sphinx' in sys.modules:
    section = _ConfigSection
