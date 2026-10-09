import json
import os
from pathlib import Path
from datetime import datetime


class RecordManager:
    """Universal game record manager - saves inside the add-on folder."""

    def __init__(self, game_name=None):
        """
        Initialise the manager.
        Args:
            game_name (str): Optional. If None or "auto", the name of the current game class is used.
        """
        self.game_name = game_name or _get_auto_game_name()

        # Find where this Python file lives (the add-on folder)
        base_dir = os.path.dirname(os.path.abspath(__file__))

        # Use a 'save' folder inside the current project directory
        self.save_dir = os.path.join(base_dir, 'save')

        # Create the folder if it does not exist
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

            print(f"Record folder created at: {self.save_dir}")

    @property
    def save_file(self):
        """Return the path of the save file."""
        # One separate file per game (e.g. battleship_record.json, tictactoe_record.json)
        game_name = getattr(self, 'game_name', 'default')
        return os.path.join(self.save_dir, f"{game_name}_record.json")

    def _load_file(self):
        """Load the data from the JSON file, or return an empty structure."""
        save_path = self.save_file

        try:
            with open(save_path, 'r') as f:
                data = json.load(f)
                data.setdefault("best_time", None)
                return data
        except FileNotFoundError:
            # First time this game is saved
            return {
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "highest_score": 0,
                "last_win_date": None,
                "total_games_played": 0,
                "best_time": None
            }

    def _save_file(self, data):
        """Write the data to the JSON file."""
        try:
            with open(self.save_file, 'w') as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            print(f"Warning: could not write the save file: {e}")

    def add_win_record(self, score=0, best_time=None):
        """Record a win."""
        records = self._load_file()
        records["wins"] += 1
        records["total_games_played"] += 1

        # If a score is given (e.g. ships sunk, points, ...), update the best score
        if score > records.get("highest_score", 0):
            records["highest_score"] = score

        if best_time is not None:
            bt = records.get("best_time")
            if bt is None or best_time < bt:
                records["best_time"] = round(best_time, 1)

        # Store the date of the last win
        records["last_win_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        self._save_file(records)
        return records

    def add_lose_record(self):
        """Record a loss."""
        records = self._load_file()
        records["losses"] += 1
        records["total_games_played"] += 1
        self._save_file(records)
        return records

    def add_draw_record(self):
        """Record a draw (used e.g. by Tic Tac Toe)."""
        records = self._load_file()
        records["draws"] += 1
        records["total_games_played"] += 1
        self._save_file(records)
        return records

    def reset_record(self):
        """Reset every record to its default value."""
        confirm = input(
            "Are you sure you want to reset the records? Type 'y' to confirm: ")

        if confirm.lower() == 'y':
            self._save_file({
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "highest_score": 0,
                "last_win_date": None,
                "total_games_played": 0,
                "best_time": None
            })
            return True
        else:
            print("Reset cancelled.")
            return False

    def get_records(self):
        """Return the current records."""
        return self._load_file()

# Helper that detects the game name automatically


def _get_auto_game_name():
    """
    Try to get the name of the calling class (when called from a class __init__).
    If it is not available, use 'default'.
    """
    import inspect
    try:
        frame = inspect.currentframe().f_back.f_back
        return frame.f_locals.get('self', None).__class__.__name__.lower() if hasattr(frame.f_locals['self'], '__class__') else "default"
    except Exception:
        return "default"

# Helper that creates the 'save' folder automatically


def _ensure_save_folder(base_dir):
    """Make sure the save folder exists."""
    save_path = os.path.join(base_dir, 'save')
    if not os.path.exists(save_path):
        try:
            os.makedirs(save_path)
            return True
        except OSError as e:
            print(f"Warning: could not create the folder: {e}")
    return False
