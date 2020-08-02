from discord.ext import commands
import discord
import logging
import os
from config import Config


class Bot(commands.Bot):
    def __init__(self, config: Config, *args, **kargs):
        self.config = config
        super().__init__(
            *args,
            activity=discord.Activity(
                type=discord.ActivityType.playing, name=config.get_status_message()
            ),
            command_prefix=config.get_command_prefix(),
        )
        self.load_extensions()

    async def on_ready(self):
        logging.info("Bot ready.")

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.errors.CommandNotFound):
            pass
        else:
            logging.warn(error)
            await ctx.send("An error occurred with the command.")

    def run(self):
        super().run(self.config.get_token())

    def load_extensions(self):
        for extension in os.listdir("cogs"):
            if extension.endswith(".py"):
                self.load_extension(f"cogs.{extension[:-3]}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    config = Config()
    client = Bot(config)
    client.run()
