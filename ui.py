"""Sidebar UI: one main panel ("Minigames") with one collapsible
sub-panel (dropdown) per game, built automatically from the game modules."""

import bpy

MAIN_ID = "MINIGAMES_PT_main"
_panel_classes = []


class MINIGAMES_PT_main(bpy.types.Panel):
    bl_idname = MAIN_ID
    bl_label = "Minigames"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Minigames"

    def draw(self, context):
        if not _panel_classes:
            self.layout.label(text="No games installed", icon='INFO')


def _make_panel(game):
    ident = game.__name__.rsplit(".", 1)[-1]
    icon = getattr(game, "GAME_ICON", 'PLAY')

    def draw_header(self, context):
        try:
            self.layout.label(text="", icon=icon)
        except Exception:
            pass

    def draw(self, context):
        try:
            game.draw_ui(self.layout, context)
        except Exception as e:  # a broken game must not break the whole panel
            self.layout.label(text="Error: %s" % e, icon='ERROR')

    name = "MINIGAMES_PT_%s" % ident
    return type(name, (bpy.types.Panel,), {
        "bl_idname": name,
        "bl_label": game.GAME_NAME,
        "bl_space_type": 'VIEW_3D',
        "bl_region_type": 'UI',
        "bl_parent_id": MAIN_ID,
        "bl_options": {'DEFAULT_CLOSED'},
        "draw_header": draw_header,
        "draw": draw,
    })


def register(games):
    bpy.utils.register_class(MINIGAMES_PT_main)
    for game in games:
        cls = _make_panel(game)
        bpy.utils.register_class(cls)
        _panel_classes.append(cls)


def unregister():
    for cls in reversed(_panel_classes):
        bpy.utils.unregister_class(cls)
    _panel_classes.clear()
    bpy.utils.unregister_class(MINIGAMES_PT_main)
