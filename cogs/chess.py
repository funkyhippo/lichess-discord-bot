import discord
from discord.ext import commands
import logging
import os
import random
from string import ascii_letters
import asyncio
import websockets
import json
import chess
import aiohttp
import time
from datetime import datetime

TIMEOUT = 60
TIMEOUT_CHECK_INTERVAL = 3
DRAW_THROTTLE = 1
BASE_BOARD_STATE = {"fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR", "uci": None}
BOARD_SIZE = 120
GAME_TIME = 240
HEARTBEAT_INTERVAL = 5
HEARTBEAT_FAIL_COUNT = GAME_TIME // HEARTBEAT_INTERVAL


class Chess(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sessions = {}

    @commands.command()
    async def sessions(self, ctx):
        output = ["Sessions:"]
        if self.sessions:
            for session in self.sessions:
                output.append(
                    f"{session}: {self.sessions[session][0]} vs. {self.sessions[session][1]}"
                )
            output = "\n".join(output)
            await ctx.send(f"```{output}```")
        else:
            await ctx.send("No sessions.")

    @commands.max_concurrency(4)
    @commands.command()
    async def chess(self, ctx, opponent: discord.Member):
        """Initiate a new lichess game with an opponent. The initiator will always be white."""
        if ctx.author == opponent:
            return await ctx.send("You can't play with yourself.")
        players = set(
            [user for session in self.sessions for user in self.sessions[session]]
        )
        if ctx.author in players:
            return await ctx.send("You're already playing a game!")
        elif opponent in players:
            return await ctx.send("The opponent is already playing a game!")

        game = None

        async with aiohttp.ClientSession() as s:
            form = aiohttp.FormData()
            form.add_field("clock.limit", GAME_TIME)
            form.add_field("clock.increment", 0)
            async with s.post(
                "https://lichess.org/api/challenge/open", data=form
            ) as res:
                if res.status == 200:
                    data = await res.json()
                    game = data["challenge"]["id"]
                    await ctx.author.send(
                        f"Your unique URL is in the next message, and will be deleted in {TIMEOUT} seconds."
                    )
                    await ctx.author.send(data["urlWhite"], delete_after=TIMEOUT)
                    await opponent.send(
                        f"{ctx.message.jump_url}\nYou've been invited to play Chess! Your unique URL is in the next message, and will be deleted in {TIMEOUT} seconds."
                    )
                    await opponent.send(data["urlBlack"], delete_after=TIMEOUT)
                    await ctx.send(
                        f"Confirming game with opponents, please wait. This invite will time out in {TIMEOUT} seconds."
                    )
                    self.sessions[game] = [ctx.author, opponent]
                else:
                    return await ctx.send("Failed to create.")

        logging.info(f"Created a new lichess game with ID: {game}")
        await self.wait_for_start(ctx, game)
        logging.info(f"Cleaning up game: {game}")
        del self.sessions[game]

    async def wait_for_start(self, ctx, game: str):
        async with aiohttp.ClientSession() as s:
            ready = False
            for _ in range(TIMEOUT // TIMEOUT_CHECK_INTERVAL):
                async with s.get(f"https://lichess.org/game/export/{game}") as res:
                    if res.status == 200:
                        ready = True
                        break
                    else:
                        await asyncio.sleep(TIMEOUT_CHECK_INTERVAL)
                    logging.info(f"Game status check: {res.status}")
            if ready:
                await self.game(ctx, game)
            else:
                logging.info("Challengers failed to ready up, cancelling.")
                await ctx.send("The challengers failed to join the game.")

    async def game(self, ctx, game):
        sri = "".join(random.sample(ascii_letters, 10))
        shard = random.randint(1, 5)
        try:
            async with websockets.connect(
                f"wss://socket{shard}.lichess.org/watch/{game}/white/v5?sri={sri}v=100",
                ssl=True,
            ) as ws:
                sem = asyncio.Semaphore(1)
                moves = [BASE_BOARD_STATE]
                signal = 9
                ping = asyncio.create_task(self.ping(ws))
                draws = asyncio.create_task(self.queue_draws(ctx, ws, sem, moves, game))
                msg = None
                try:
                    async for ms in ws:
                        logging.debug(f"Data from socket: {ms}")
                        if ms == "0":
                            signal += 1
                            if signal >= HEARTBEAT_FAIL_COUNT:
                                await ctx.send(f"`{game}`: cancelled due to inactivity.")
                                raise RuntimeError("Heartbeat didn't receive any actions for too long.")
                        else:
                            signal = 0
                            payload = json.loads(ms)
                            status = None
                            if payload.get("t", None) == "end":
                                status = payload.get("d", None)
                                if not status:
                                    status = "Draw!"
                                if status == "white":
                                    status = f"{self.sessions[game][0].mention} won!"
                                elif status == "black":
                                    status = f"{self.sessions[game][1].mention} won!"
                            elif payload.get("t", None) == "crowd":
                                if all(
                                    [not presence for presence in payload["d"].values()]
                                ):
                                    status = "Both players disconnected."
                            else:
                                if "d" in payload and "fen" in payload["d"]:
                                    moves.append(payload["d"])
                                    logging.info(f"Pushed move: {payload['d']}")
                                    logging.info(f"Moves available: {len(moves)}")
                                    sem.release()
                                else:
                                    logging.warn(f"Data is malformed: {payload}")
                            if status:
                                await ctx.send(f"`{game}`: {status}")
                except Exception:
                    while len(moves):
                        await asyncio.sleep(0)  # Finish the rest of the turns
                    ping.cancel()
                    draws.cancel()
                    try:
                        await ping
                    except asyncio.CancelledError:
                        logging.info("Heartbeat successfully cancelled.")
                    try:
                        await draws
                    except asyncio.CancelledError:
                        logging.info("Drawing task successfully cancelled.")
                    await ws.close()
                    raise
        except Exception as err:
            logging.warn(err)

    async def ping(self, ws):
        logging.info("Ping task started.")
        try:
            while not ws.closed:
                try:
                    logging.debug("Sending keepalive.")
                    await ws.send(json.dumps({"t": "p", "l": 20}))
                    await asyncio.sleep(HEARTBEAT_INTERVAL)
                except websockets.ConnectionClosedOK:
                    pass
        except asyncio.CancelledError:
            logging.info("Ping task received cancellation.")
            raise

    async def queue_draws(self, ctx, ws, sem, moves, game):
        logging.info("Draw task started.")
        try:
            msg = None
            while await sem.acquire():
                d = moves.pop(0)
                logging.info(f"Refreshing board: {d}")
                board = chess.Board(d["fen"])
                em = discord.Embed(
                    description=f"```{str(board)}```\n[watch on lichess.org](https://lichess.org/{game})",
                    timestamp=datetime.now(),
                )
                url = f"https://backscattering.de/web-boardimage/board.png?fen={d['fen']}&size={BOARD_SIZE}"
                if d["uci"]:
                    url += f"&lastMove={d['uci']}"
                em.set_thumbnail(url=url)
                em.set_footer(text="Powered by lichess.org")
                em.add_field(
                    name="Players",
                    value=f":white_large_square: {self.sessions[game][0].mention}\n:black_large_square: {self.sessions[game][1].mention}",
                )
                if not msg:
                    msg = await ctx.send(content=None, embed=em)
                else:
                    await msg.edit(content=None, embed=em)
                await asyncio.sleep(DRAW_THROTTLE * len(self.sessions))
        except asyncio.CancelledError:
            logging.info("Draw task received cancellation.")
            raise


def setup(bot):
    chess = Chess(bot)
    bot.add_cog(chess)
