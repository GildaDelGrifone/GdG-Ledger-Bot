import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from telegram import BotCommand
from config import settings
from core.models import CustomCommandConfig, CustomCommandDefaults

logger = logging.getLogger(__name__)


def load_custom_commands(commands_dir: Optional[str] = None) -> Dict[str, CustomCommandConfig]:
    """
    Legge tutti i file di configurazione (.yaml, .yml, .json) nella cartella custom_commands/
    e restituisce un dizionario [nome_comando -> CustomCommandConfig]
    """
    dir_path = Path(commands_dir or settings.CUSTOM_COMMANDS_DIR)
    commands: Dict[str, CustomCommandConfig] = {}

    if not dir_path.exists():
        logger.info(f"Directory comandi personalizzati non esistente: {dir_path}. Creazione in corso...")
        dir_path.mkdir(parents=True, exist_ok=True)
        return commands

    for file_path in dir_path.iterdir():
        if not file_path.is_file():
            continue

        if file_path.suffix.lower() in [".yaml", ".yml"]:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
            except Exception as e:
                logger.error(f"Errore caricamento file YAML {file_path}: {e}")
                continue
        elif file_path.suffix.lower() == ".json":
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.error(f"Errore caricamento file JSON {file_path}: {e}")
                continue
        else:
            continue

        if not isinstance(data, dict):
            continue

        # Se il campo command non è specificato nel file, usa il nome del file (senza estensione)
        cmd_name = data.get("command") or file_path.stem
        cmd_name = cmd_name.strip().lstrip("/").lower()

        try:
            config = CustomCommandConfig(
                command=cmd_name,
                description=data.get("description", f"Macro per comando /{cmd_name}"),
                defaults=CustomCommandDefaults(**data.get("defaults", {}))
            )
            commands[cmd_name] = config
            logger.info(f"Caricato comando personalizzato: /{cmd_name} da {file_path.name}")
        except Exception as e:
            logger.error(f"Errore validazione configurazione per {file_path.name}: {e}")

    return commands


def build_bot_commands_list(custom_commands: Dict[str, CustomCommandConfig]) -> List[BotCommand]:
    """
    Costruisce l'elenco di comandi Telegram standard e custom da registrare con setMyCommands
    """
    commands = [
        BotCommand("write", "Registra nuova transazione nel foglio"),
        BotCommand("w", "Scorciatoia per /write"),
        BotCommand("transfer", "Sposta contanti tra Cassa Rossa e Cassa Blu"),
        BotCommand("cassa", "Mostra i saldi di Cassa Rossa e Cassa Blu"),
        BotCommand("sat_list", "Elenco transazioni Satispay per data"),
        BotCommand("sat_list_range", "Elenco transazioni Satispay per intervallo"),
        BotCommand("sat_get", "Richiama notifica Satispay dato ID"),
        BotCommand("sumup_list", "Elenco transazioni SumUp per data"),
        BotCommand("sumup_list_range", "Elenco transazioni SumUp per intervallo"),
        BotCommand("sumup_get", "Richiama notifica SumUp dato codice"),
        BotCommand("cancel", "Annulla operazione in corso"),
        BotCommand("help", "Mostra istruzioni e comandi disponibili"),
    ]

    for name, cfg in custom_commands.items():
        # Telegram accetta nomi di comandi solo minuscoli, numeri e underscore, lunghi 1-32 caratteri
        clean_name = name.lower()[:32]
        desc = cfg.description[:256] if cfg.description else f"Comando /{name}"
        commands.append(BotCommand(clean_name, desc))

    return commands
