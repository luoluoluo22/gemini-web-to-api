# src/app/config.py
import configparser
import logging

logger = logging.getLogger(__name__)


def load_config(config_file: str = "config.conf") -> configparser.ConfigParser:
    # Disable interpolation to allow % characters in cookies
    config = configparser.ConfigParser(interpolation=None)
    try:
        # FIX: Explicitly specify UTF-8 encoding to prevent UnicodeDecodeError on Windows.
        # This is the standard and most compatible way to handle text files across platforms.
        import os
        if not os.path.exists(config_file):
            # Try to find config in parent directory if running from src
            parent_config = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), config_file)
            if os.path.exists(parent_config):
                config_file = parent_config
        
        abs_path = os.path.abspath(config_file)
        # print(f"DEBUG: Loading config from: {abs_path}")
        logger.info(f"Loading config from: {abs_path}")
        
        config.read(config_file, encoding="utf-8")
        # print(f"DEBUG: Config sections: {config.sections()}")

    except FileNotFoundError:
        logger.warning(
            f"Config file '{config_file}' not found. Creating a default one."
        )
    except Exception as e:
        logger.error(f"Error reading config file: {e}")

    # Set default sections and values if they don't exist
    if "Browser" not in config:
        config["Browser"] = {"name": "chrome"}
    if "Cookies" not in config:
        config["Cookies"] = {}
    if "AI" not in config:
        config["AI"] = {"default_model_gemini": "gemini-3.0-pro"}
    if "Proxy" not in config:
        config["Proxy"] = {"http_proxy": ""}

    # Disable auto-save to prevent overwriting user config and data loss
    # try:
    #     with open(config_file, "w", encoding="utf-8") as f:
    #         config.write(f)
    #     # logger.info("Configuration loaded/updated successfully.")
    # except Exception as e:
    #     logger.error(f"Error writing to config file: {e}")

    return config


# Load configuration globally
CONFIG = load_config()
