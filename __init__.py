bl_info = {
    "name": "Minigames",
    "author": "Ester Milanese, Gattalupa",
    "version": (0, 0, 1),
    "blender": (4, 5, 0),
    "location": "3D Viewport > Sidebar (N) > Minigames",
    "description": "A collection of minigames playable inside Blender",
    "category": "Game",
}

from . import games, ui


def register():
    games.register()
    ui.register(games.ACTIVE)


def unregister():
    ui.unregister()
    games.unregister()
