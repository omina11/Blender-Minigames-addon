import json
import os
from pathlib import Path
from datetime import datetime


class RecordManager:
    """Gestore universale per record di gioco - Salva nella cartella dell'addon."""

    def __init__(self, game_name=None):
        """
        Inizializza il gestore.
        Args:
            game_name (str): Opzionale. Se None o "auto", usa il nome della classe del gioco corrente.
        """
        self.game_name = game_name or _get_auto_game_name()

        # Trova dove risiede questo file Python (cartella dell'addon)
        base_dir = os.path.dirname(os.path.abspath(__file__))

        # Crea una cartella 'save' nella directory del progetto corrente
        self.save_dir = os.path.join(base_dir, 'save')

        # Se non esiste la cartella, créala
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

            print(f"Cartella record creata in: {self.save_dir}")

    @property
    def save_file(self):
        """Ritorna il percorso del file di salvataggio."""
        # Crea un file distinto per ogni gioco (es. battleship_record.json, tictactoe_record.json)
        game_name = getattr(self, 'game_name', 'default')
        return os.path.join(self.save_dir, f"{game_name}_record.json")

    def _load_file(self):
        """Carica i dati dal file JSON o restituisce una struttura vuota."""
        save_path = self.save_file

        try:
            with open(save_path, 'r') as f:
                data = json.load(f)
                data.setdefault("best_time", None)
                return data
        except FileNotFoundError:
            # Prima volta che salvi in questa sessione
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
        """Salva i dati nel file JSON."""
        try:
            with open(self.save_file, 'w') as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            print(f"⚠️ Errore nella scrittura del file di salvataggio: {e}")

    def add_win_record(self, score=0, best_time=None):
        """Registra una vittoria."""
        records = self._load_file()
        records["wins"] += 1
        records["total_games_played"] += 1

        # Se c'è un punteggio (es. numero di navi affondate o mossa più veloce), aggiorna il record migliore
        if score > records.get("highest_score", 0):
            records["highest_score"] = score

        if best_time is not None:
            bt = records.get("best_time")
            if bt is None or best_time < bt:
                records["best_time"] = round(best_time, 1)

        # Salva la data dell'ultima vittoria
        records["last_win_date"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        self._save_file(records)
        return records

    def add_lose_record(self):
        """Registra una sconfitta."""
        records = self._load_file()
        records["losses"] += 1
        records["total_games_played"] += 1
        self._save_file(records)
        return records

    def add_draw_record(self):
        """Registra un pareggio (usato per Tic Tac Toe)."""
        records = self._load_file()
        records["draws"] += 1
        records["total_games_played"] += 1
        self._save_file(records)
        return records

    def reset_record(self):
        """Resetta tutti i record al valore predefinito."""
        confirm = input(
            "⚠️ Sei sicuro di voler resettare i record? Rispondi 'y' per confermare: ")

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
            print("Reset annullato.")
            return False

    def get_records(self):
        """Ritorna i record attuali."""
        return self._load_file()

# Funzione helper per rilevare il nome del gioco in automatico


def _get_auto_game_name():
    """
    Cerca di ottenere il nome della classe corrente (se viene chiamato da __init__ di una classe).
    Se non è disponibile, usa 'default'.
    """
    import inspect
    try:
        frame = inspect.currentframe().f_back.f_back
        return frame.f_locals.get('self', None).__class__.__name__.lower() if hasattr(frame.f_locals['self'], '__class__') else "default"
    except Exception:
        return "default"

# Funzione per creare la cartella 'save' automaticamente


def _ensure_save_folder(base_dir):
    """Assicura che la cartella di salvataggio esista."""
    save_path = os.path.join(base_dir, 'save')
    if not os.path.exists(save_path):
        try:
            os.makedirs(save_path)
            return True
        except OSError as e:
            print(f"⚠️ Errore nella creazione della cartella: {e}")
    return False
