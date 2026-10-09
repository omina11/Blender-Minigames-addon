"""Game loader.

Every .py file in this folder (whose name doesn't start with "_") is a game.
Drop a new file here and it automatically gets its own dropdown in the UI.

A game module must define:
    GAME_NAME : str                  label of the dropdown
    GAME_ICON : str (optional)       Blender icon name for the dropdown header
    draw_ui(layout, context)         draws the content of the dropdown
    register() / unregister()        register/unregister the game's own
                                     classes, properties and handlers

See _template.py for a starting point.
"""

import importlib
import pkgutil
import sys
import traceback

REQUIRED = ("GAME_NAME", "draw_ui", "register", "unregister")

ACTIVE = []   # modules that loaded and registered successfully


def _discover():
    found = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        full = "%s.%s" % (__name__, info.name)
        try:
            if full in sys.modules:
                mod = importlib.reload(sys.modules[full])
            else:
                mod = importlib.import_module(full)
            missing = [a for a in REQUIRED if not hasattr(mod, a)]
            if missing:
                print("[Minigames] '%s' skipped, missing: %s" % (info.name, ", ".join(missing)))
                continue
            found.append(mod)
        except Exception:
            print("[Minigames] failed to load '%s':" % info.name)
            traceback.print_exc()
    return sorted(found, key=lambda m: m.GAME_NAME.lower())


def register():
    ACTIVE.clear()
    for mod in _discover():
        try:
            mod.register()
            ACTIVE.append(mod)
        except Exception:
            print("[Minigames] failed to register '%s':" % mod.__name__)
            traceback.print_exc()


def unregister():
    for mod in reversed(ACTIVE):
        try:
            mod.unregister()
        except Exception:
            traceback.print_exc()
    ACTIVE.clear()
