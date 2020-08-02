import json


class Config:
    def __init__(self):
        self.refresh_config()

    def get_token(self):
        return self.config.get("token", "")

    def get_status_message(self):
        return self.config.get("status_message", "")

    def get_command_prefix(self):
        return self.config.get("command_prefix", ".")

    def refresh_config(self):
        with open("config.json", "r") as config:
            try:
                self.config = json.loads(config.read())
            except json.JSONDecodeError:
                self.config = {}
