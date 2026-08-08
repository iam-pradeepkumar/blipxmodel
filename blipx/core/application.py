from blipx.utils.logger import app_logger

class BlipXApplication:

    def __init__(self):
        self.name = "BlipX"
        self.version = "0.1.0"

    def start(self):
        app_logger.info("=" * 50)
        app_logger.info(f"{self.name} v{self.version}")
        app_logger.info("=" * 50)
        app_logger.info("Initializing BlipX...")
        app_logger.success("Application started successfully!")

    def stop(self):
        app_logger.warning("Shutting down BlipX...")
