import os

import yaml

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.yaml")

with open(CONFIG_PATH, encoding="utf-8") as fh:
    CONFIG = yaml.safe_load(fh)
