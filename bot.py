#!/usr/bin/env python3
"""
Always-on Discord bot for the #kapacita channel.

/kapacita posts a PNG graph of free spots over time for the latest
Věda na hradě lecture, built from capacity_log.csv (written by main.py).

Env:
    DISCORD_BOT_TOKEN            bot token (required)
    DISCORD_GUILD_ID             server ID; commands sync there so they show up instantly

/kapacita only works in the #kapacita channel (KAPACITA_CHANNEL_ID).

Render the graph locally without Discord:

    python bot.py --render out.png
"""

import argparse
import asyncio
import io
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

from capacity import load_latest_lecture_log

PRAGUE = ZoneInfo("Europe/Prague")
KAPACITA_CHANNEL_ID = 1552626317432721408


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def render_graph(title: str, points: list[tuple[datetime, int]]) -> bytes:
    """Plot free spots over time (Prague time) and return PNG bytes."""
    times = [t.astimezone(PRAGUE).replace(tzinfo=None) for t, _ in points]
    free = [f for _, f in points]

    fig, ax = plt.subplots(figsize=(10, 5))
    try:
        ax.step(times, free, where="post", color="tab:blue")
        ax.plot(times, free, "o", color="tab:blue", markersize=3)
        ax.set_title(title)
        ax.set_ylabel("Free spots")
        ax.set_xlabel("Time (Europe/Prague)")
        ax.set_ylim(bottom=0, top=max(max(free), 1) * 1.1)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
        ax.grid(True, alpha=0.3)
        fig.autofmt_xdate()
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=120)
        return buf.getvalue()
    finally:
        plt.close(fig)


def build_graph() -> tuple[str, bytes] | None:
    """Return (caption, png) for the latest lecture, or None if nothing is logged."""
    log = load_latest_lecture_log()
    if not log:
        return None
    title, points = log
    last_time, last_free = points[-1]
    caption = (
        f"**{title}**\n"
        f"🎟️ Free spots now: **{last_free}** "
        f"(as of {last_time.astimezone(PRAGUE):%d.%m. %H:%M}, {len(points)} readings)"
    )
    return caption, render_graph(title, points)


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

def run_bot():
    import discord
    from discord import app_commands

    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        sys.exit("DISCORD_BOT_TOKEN not set")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    guild = discord.Object(id=int(guild_id)) if guild_id else None

    client = discord.Client(intents=discord.Intents.default())
    tree = app_commands.CommandTree(client)

    @tree.command(name="kapacita", description="Graph of free spots for the latest Věda na hradě lecture")
    async def kapacita(interaction: discord.Interaction):
        if interaction.channel_id != KAPACITA_CHANNEL_ID:
            await interaction.response.send_message(
                f"Use this in <#{KAPACITA_CHANNEL_ID}>.", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            result = await asyncio.to_thread(build_graph)
        except Exception as exc:
            print(f"❌ Graph failed: {exc}")
            await interaction.followup.send(f"❌ Couldn't build the graph: {exc}")
            return

        if not result:
            await interaction.followup.send("No capacity data logged yet.")
            return
        caption, png = result
        await interaction.followup.send(
            caption, file=discord.File(io.BytesIO(png), filename="kapacita.png")
        )

    @client.event
    async def setup_hook():
        if guild:
            tree.copy_global_to(guild=guild)
            synced = await tree.sync(guild=guild)
        else:
            # Global commands can take up to an hour to appear.
            synced = await tree.sync()
        print(f"🔄 Synced {len(synced)} command(s).")

    @client.event
    async def on_ready():
        print(f"✅ Logged in as {client.user}")

    client.run(token)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--render", metavar="PNG", help="Write the graph to a file instead of running the bot.")
    args = parser.parse_args()

    if args.render:
        result = build_graph()
        if not result:
            sys.exit("No capacity data logged yet.")
        caption, png = result
        with open(args.render, "wb") as f:
            f.write(png)
        print(caption)
        print(f"📈 Wrote {args.render}")
        return

    run_bot()


if __name__ == "__main__":
    main()
