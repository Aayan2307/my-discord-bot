import os
from dotenv import load_dotenv
load_dotenv()
token = os.getenv("DISCORD_TOKEN")
bot.run(token)

PREFIX = "t!"
BOT_COLOR = discord.Color.blue()

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Google Sheets
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
gc = gspread.authorize(creds)
sheet = gc.open("TFS Auction").worksheet("Players")
teams = gc.open("TFS Auction").worksheet("Teams")

TIER_MIN = {"A": 30, "B": 15, "C": 5}  # M
bid_timers = {}  # key = player.lower() → asyncio.Task


# ── helpers ─────────────────────────────────────────────────────────
def e(title: str, desc: str, color: discord.Color = BOT_COLOR):
    """Quick embed helper."""
    return discord.Embed(title=title, description=desc, color=color)


def find_player_row(name: str):
    for i, rec in enumerate(sheet.get_all_records(), start=2):
        if rec["Player"].strip().lower() == name.strip().lower():
            return i, rec
    return None, None


def find_team_row(name: str):
    for i, rec in enumerate(teams.get_all_records(), start=2):
        if rec["Team"].split(" (")[0].strip().lower() == name.strip().lower():
            return i, rec
    return None, None


def is_recruiter(member: discord.Member):
    perms = member.guild_permissions
    role_ok = any(r.name.lower() == "recruiter" for r in member.roles)
    return perms.administrator or perms.manage_guild or role_ok
# ── auto‑sell coroutine (replace entire function) ───────────────────────────
async def auto_sell(player: str, channel: discord.TextChannel):
    """Auto‑sell a player after 60 s of no new bids, with full rule checks."""
    await asyncio.sleep(60)

    # Fetch player & team data
    p_row, prec = find_player_row(player)
    if not prec:
        bid_timers.pop(player.lower(), None)
        return                          # Player deleted manually

    team  = prec["Team"]
    price = int(prec["Price"] or 0)
    tier  = prec["Tier"]

    # No active bid or already sold
    if not team or team.endswith("(SOLD)"):
        bid_timers.pop(player.lower(), None)
        return

    t_row, tdata = find_team_row(team)
    if not tdata:                       # Safety check
        await channel.send(embed=e("Auto‑Sell Error",
                                   f"Team **{team}** not in Teams sheet.",
                                   discord.Color.red()))
        bid_timers.pop(player.lower(), None)
        return

    budget = int(tdata["Budget"] or 0)
    tier_a = int(tdata["TierA Count"] or 0)

    # --- Rule checks --------------------------------------------------------
    fails = []
    if price > budget:
        fails.append(
            f"You need {price}M to bid on {player}, you currently have {budget}M."
        )
    if tier == "A" and tier_a >= 2:
        fails.append(
            "Team cannot go through with the purchase as they already have 2 Tier‑A players."
        )

    if fails:
        await channel.send(embed=e("Auto‑Sell Blocked", "\n".join(fails),
                                   discord.Color.red()))
        bid_timers.pop(player.lower(), None)
        return

    # --- Finalise sale ------------------------------------------------------
    sheet.update_cell(p_row, 4, f"{team} (SOLD)")
    teams.update_cell(t_row, 2, budget - price)
    if tier == "A":
        teams.update_cell(t_row, 3, tier_a + 1)

    await channel.send(embed=e("⏰ Auto‑Sold",
                               f"**{player}** sold to **{team}** for **{price}M** "
                               "after 60 seconds.",
                               discord.Color.orange()))
    bid_timers.pop(player.lower(), None)
# ── events ──────────────────────────────────────────────────────────
@client.event
async def on_ready():
    print("Bot online:", client.user)


@client.event
async def on_message(msg: discord.Message):
    if msg.author.bot or not msg.content.startswith(PREFIX):
        return

    args = msg.content[len(PREFIX):].split()
    if not args:
        return
    cmd = args[0].lower()
    author_team = msg.author.display_name.strip()
 # --- HELP ---
    if cmd == "help":
        await msg.channel.send(embed=e(
            "TFS Auction – Commands",
            "**t!addplayer <name> <tier> <sr>** – Recruiter only\n"
            "**t!players [tier]** – list players\n"
            "**t!bid <name> <amt>** – place / raise bid\n"
            "**t!sell <name>** – finalise sale\n"
            "**t!leader <name>** – current highest bid or SOLD\n"
            "**t!budget** – your team budget\n\n"
            "Uncontested bids auto‑sell after 60 seconds."
        ))
        return

    # --- PLAYERS list ---
    if cmd == "players":
        tier_filter = args[1].upper() if len(args) == 2 else None
        if tier_filter and tier_filter not in TIER_MIN:
            await msg.channel.send(embed=e("Error", "Tier must be A, B, or C", discord.Color.red()))
            return

        lines = []
        for rec in sheet.get_all_records():
            if tier_filter and rec["Tier"] != tier_filter:
                continue
            status = "OPEN"
            if rec["Team"]:
                status = "SOLD" if rec["Team"].endswith("(SOLD)") \
                         else f"{rec['Team']} {rec['Price']}M"
            lines.append(f"• **{rec['Player']}** (Tier {rec['Tier']}, SR {rec['SR']}) – {status}")

        chunks, current = [], "**Player List**\n"
        for line in lines or ["No players found."]:
            if len(current) + len(line) + 1 > 1900:
                chunks.append(current); current = ""
            current += line + "\n"
        chunks.append(current)
        for c in chunks:
            await msg.channel.send(embed=e("Player List", c))
        return

    # --- ADDPLAYER ---
    if cmd == "addplayer":
        if len(args) != 4 or not is_recruiter(msg.author):
            await msg.channel.send(embed=e("Error",
                "Usage: t!addplayer <name> <tier> <sr> (Recruiter only)", discord.Color.red()))
            return
        name, tier, sr = args[1], args[2].upper(), args[3]
        if tier not in TIER_MIN or not sr.isdigit():
            await msg.channel.send(embed=e("Error", "Invalid tier or SR.", discord.Color.red())); return
        if find_player_row(name)[1]:
            await msg.channel.send(embed=e("Error", "Player already exists.", discord.Color.red())); return
        sheet.append_row([name, tier, sr, "", 0])
        await msg.channel.send(embed=e("Player Added",
            f"**{name}** (Tier {tier}, SR {sr}) added.", discord.Color.green()))
        return

    # --- BID -----------------------------------------------------------------
    if cmd == "bid":
        if len(args) != 3 or not args[2].isdigit():
            await msg.channel.send(embed=e("Error",
                "Usage: t!bid <name> <amount>", discord.Color.red()))
            return

        player, amount = args[1], int(args[2])

        # Get player
        p_row, prec = find_player_row(player)
        if not prec or prec["Team"].endswith("(SOLD)"):
            await msg.channel.send(embed=e("Error",
                "Player not found or already sold.", discord.Color.red()))
            return

        # Get team budget
        t_row, tdata = find_team_row(author_team)
        if not tdata:
            await msg.channel.send(embed=e("Error",
                "Your team is not in the Teams sheet.", discord.Color.red()))
            return
        my_budget = int(tdata["Budget"] or 0)

        # Budget check
        if amount > my_budget:
            await msg.channel.send(embed=e(
                "Insufficient Budget",
                (f"You need {amount}M to bid on {player}, "
                 f"you currently have {my_budget}M."),
                discord.Color.red()))
            return

        # Tier‑based minimums
        tier = prec["Tier"]
        current = int(prec["Price"] or 0)
        min_open = TIER_MIN[tier]
        if current == 0 and amount < min_open:
            await msg.channel.send(embed=e(
                "Error",
                f"Opening bid for Tier {tier} must be ≥ {min_open}M.",
                discord.Color.red()))
            return
        if current > 0 and amount <= current:
            await msg.channel.send(embed=e(
                "Error",
                f"Bid must beat {current}M.",
                discord.Color.red()))
            return

        # Update sheet
        sheet.update_cell(p_row, 4, author_team)
        sheet.update_cell(p_row, 5, amount)
        await msg.channel.send(embed=e(
            "Highest Bid",
            f"**{author_team}** leads **{player}** with **{amount}M**.",
            discord.Color.orange()))

        # Restart auto‑sell timer
        key = player.lower()
        if key in bid_timers:
            bid_timers[key].cancel()
        bid_timers[key] = asyncio.create_task(auto_sell(player, msg.channel))
        return
 # --- SELL ----------------------------------------------------------------
    if cmd == "sell":
        if len(args) != 2:
            await msg.channel.send(embed=e("Error",
                "Usage: t!sell <name>", discord.Color.red()))
            return

        player = args[1]
        p_row, prec = find_player_row(player)
        if not prec:
            await msg.channel.send(embed=e("Error",
                "Player not found.", discord.Color.red()))
            return

        team  = prec["Team"]
        price = int(prec["Price"] or 0)
        tier  = prec["Tier"]

        if not team or team.endswith("(SOLD)"):
            await msg.channel.send(embed=e("Error",
                "No active bid.", discord.Color.red()))
            return

        t_row, tdata = find_team_row(team)
        if not tdata:
            await msg.channel.send(embed=e("Error",
                "Team not listed.", discord.Color.red()))
            return

        budget = int(tdata["Budget"] or 0)
        tier_a = int(tdata["TierA Count"] or 0)

        # Budget + Tier‑A checks
        fails = []
        if price > budget:
            fails.append(
                f"You need {price}M to bid on {player}, you currently have {budget}M."
            )
        if tier == "A" and tier_a >= 2:
            fails.append(
                "Team cannot go through with the purchase as they already have 2 Tier‑A players."
            )

        if fails:
            await msg.channel.send(embed=e(
                "Team can’t complete purchase",
                "\n".join(fails),
                discord.Color.red()))
            return

        # Finalise sale
        sheet.update_cell(p_row, 4, f"{team} (SOLD)")
        teams.update_cell(t_row, 2, budget - price)
        if tier == "A":
            teams.update_cell(t_row, 3, tier_a + 1)

        await msg.channel.send(embed=e(
            "Sale Complete",
            f"**{player}** sold to **{team}** for **{price}M**.",
            discord.Color.green()))
        return
    # --- LEADER ---
    if cmd == "leader":
        if len(args) != 2:
            await msg.channel.send(embed=e("Error", "Usage: t!leader <name>", discord.Color.red())); return
        player = args[1]; rec = find_player_row(player)[1]
        if not rec:
            await msg.channel.send(embed=e("Error", "Player not found.", discord.Color.red())); return
        if not rec["Team"]:
            await msg.channel.send(embed=e("Leader", "No bids yet.", discord.Color.gold())); return
        sold = rec["Team"].endswith("(SOLD)")
        status = "SOLD" if sold else "Top Bid"
        await msg.channel.send(embed=e(status,
            f"{rec['Player']} – {rec['Team']} {rec['Price']}M"))
        return

    # --- BUDGET ---
    if cmd == "budget":
        t_row, tdata = find_team_row(author_team)
        if not tdata:
            await msg.channel.send(embed=e("Error", "Your team isn’t in Teams sheet.", discord.Color.red())); return
        await msg.channel.send(embed=e("Budget",
            f"**Remaining:** {tdata['Budget']}M\\n**Tier‑A Used:** {tdata['TierA Count']}/2",
            discord.Color.gold()))
        return

client.run(TOKEN)