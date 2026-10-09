"""Template for a new game: copy this file, rename it (no leading underscore)
and fill it in. It will show up as a new dropdown in the Minigames tab."""

import bpy

GAME_NAME = "My Game"
GAME_ICON = 'PLAY'


class MINIGAMES_OT_mygame_play(bpy.types.Operator):
    bl_idname = "minigames.mygame_play"
    bl_label = "Play My Game"

    def execute(self, context):
        self.report({'INFO'}, "Welcome")
        return {'FINISHED'}


classes = (MINIGAMES_OT_mygame_play,)


def draw_ui(layout, context):
    layout.operator(MINIGAMES_OT_mygame_play.bl_idname)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
