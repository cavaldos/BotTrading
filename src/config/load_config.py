import os
import yaml # pyright: ignore[reportMissingModuleSource]


def load_config(config_path=None):
    if config_path is None:
        config_path = 'src/config/dev.yaml'
    
    if not os.path.exists(config_path):
        print(f"Config file not found at {config_path}.")
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            return config if config else {}
    except Exception as e:
        print(f"Error reading config file: {e}.")
        return {}