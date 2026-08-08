from blipx.core.application import BlipXApplication
from blipx.core.config import ConfigManager

def main():
    config = ConfigManager()
    config.load()

    print("=" * 50)
    print("Configuration Loaded")
    print("=" * 50)

    print("Project :", config.get("project.name"))
    print("Version :", config.get("project.version"))
    print("Reasoning Model :", config.get("reasoning.model"))
    print("Codec :", config.get("codec.model"))
    print()

    app = BlipXApplication()
    app.start()


if __name__ == "__main__":
    main()
