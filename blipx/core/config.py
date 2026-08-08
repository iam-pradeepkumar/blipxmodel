"""
Configuration Manager for BlipX
"""

from pathlib import Path
import yaml


class ConfigManager:
    def __init__(self, config_path="configs/default.yaml"):
        self.config_path = Path(config_path)
        self.config = {}

    def load(self):
        with open(self.config_path, "r") as file:
            self.config = yaml.safe_load(file)

        return self.config

    def get(self, key, default=None):
        keys = key.split(".")
        value = self.config

        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
            else:
                return default

        return value if value is not None else default
