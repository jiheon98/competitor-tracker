import os
import json
import anthropic
import requests
import discord
from discord.ext import tasks
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
RAINFOREST_KEY = os.getenv("RAINFOREST_API_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))

with open("competitors.json") as f:
    config = json.load(f)

MY_PRODUCT = config["my_product"]
COMPETITORS = config["competitors"]

HISTORY_FILE = "review_history.json"
PRICE_FILE = "price_history.json"

# ── History helpers ───────────────────────────────────────────────────────────

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

# ── Fetch product data ────────────────────────────────────────────────────────

def fetch_product_data(asin, name):
    url = "https://api.rainforestapi.com/request"
    params = {
        "api_key": RAINFOREST_KEY,
        "type": "product",
        "asin": asin,
        "amazon_domain": "amazon.com"
    }
    response = requests.get(url, params=params)
    data = response.json()
    product = data.get("product", {})
    return {
        "asin": asin,
        "name": name,
        "title": product.get("title", "Unknown"),
        "price": product.get("buybox_winner", {}).get("price", {}).get("value"),
        "rating": product.get("rating", None),
        "review_count": product.get("ratings_total", None),
        "bsr": product.get("bestsellers_rank", [{}])[0].get("rank") if product.get("bestsellers_rank") else None,
        "in_stock": product.get("buybox_winner", {}) is not None and product.get("buybox_winner", {}) != {}
    }

def fetch_any_product(asin_or_name):
    url = "https://api.rainforestapi.com/request"
    # If it looks like an ASIN use product lookup, otherwise search
    if len(asin_or_name) == 10 and asin_or_name.isalnum():
        params = {
            "api_key": RAINFOREST_KEY,
            "type": "product",
            "asin": asin_or_name,
            "amazon_domain": "amazon.com"
        }
        response = requests.get(url, params=params)
        data = response.json()
        product = data.get("product", {})
        return [{
            "name": product.get("title", "Unknown")[:60],
            "asin": asin_or_name,
            "price": product.get("buybox_winner", {}).get("price", {}).get("value"),
            "rating": product.get("rating"),
            "review_count": product.get("ratings_total"),
            "bsr": product.get("bestsellers_rank", [{}])[0].get("rank") if product.get("bestsellers_rank") else None,
        }]
    else:
        params = {
            "api_key": RAINFOREST_KEY,
            "type": "search",
            "search_term": asin_or_name,
            "amazon_domain": "amazon.com"
        }
        response = requests.get(url, params=params)
        data = response.json()
        results = data.get("search_results", [])[:3]
        return [{
            "name": r.get("title", "Unknown")[:60],
            "asin": r.get("asin"),
            "price": r.get("price", {}).get("value"),
            "rating": r.get("rating"),
            "review_count": r.get("ratings_total"),
            "bsr": None
        } for r in results]

def fetch_new_competitors():
    url = "https://api.rainforestapi.com/request"
    params = {
        "api_key": RAINFOREST_KEY,
        "type": "search",
        "search_term": "anti aging face device red light therapy",
        "amazon_domain": "amazon.com"
    }
    response = requests.get(url, params=params)
    data = response.json()
    results = data.get("search_results", [])
    known_asins = [c["asin"] for c in COMPETITORS]
    new = []
    for r in results[:20]:
        asin = r.get("asin")
        reviews = r.get("ratings_total", 999)
        if asin and asin not in known_asins and reviews < 100:
            new.append({
                "name": r.get("title", "Unknown")[:60],
                "asin": asin,
                "price": r.get("price", {}).get("value"),
                "reviews": reviews,
            })
    return new[:3]

# ── Price change detection ────────────────────────────────────────────────────

def check_price_changes(competitor_data):
    price_history = load_json(PRICE_FILE)
    alerts = []
    for product in competitor_data:
        asin = product["asin"]
        current_price = product["price"]
        previous_price = price_history.get(asin, {}).get("price")
        if current_price and previous_price and current_price != previous_price:
            diff = current_price - previous_price
            pct = (diff / previous_price) * 100
            direction = "🔴 DROPPED" if diff < 0 else "🟢 RAISED"
            alerts.append(
                f"⚡ **PRICE ALERT — {product['name']}**\n"
                f"{direction} from ${previous_price:.2f} → ${current_price:.2f} "
                f"({pct:+.1f}%)"
            )
        price_history[asin] = {"price": current_price, "date": str(datetime.now())}
    save_json(PRICE_FILE, price_history)
    return alerts

# ── Review velocity ───────────────────────────────────────────────────────────

def calculate_velocity(competitor_data):
    history = load_json(HISTORY_FILE)
    for product in competitor_data:
        asin = product["asin"]
        current = product["review_count"] or 0
        previous = history.get(asin, {}).get("review_count", current)
        product["review_velocity"] = current - previous
        history[asin] = {"review_count": current, "date": str(datetime.now().date())}
    save_json(HISTORY_FILE, history)
    return competitor_data

# ── Weekly summary data ───────────────────────────────────────────────────────

def load_weekly_data():
    weekly = load_json("weekly_history.json")
    return weekly

def save_weekly_snapshot(competitor_data):
    weekly = load_weekly_data()
    today = str(datetime.now().date())
    weekly[today] = competitor_data
    # Keep only last 7 days
    if len(weekly) > 7:
        oldest = sorted(weekly.keys())[0]
        del weekly[oldest]
    save_json("weekly_history.json", weekly)

# ── Claude analysis ───────────────────────────────────────────────────────────

def ask_claude(system_prompt, user_message, history=None):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    messages = history if history else [{"role": "user", "content": user_message}]
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=system_prompt,
        messages=messages
    )
    return message.content[0].text

def get_daily_brief(competitor_data, new_competitors):
    today = datetime.now().strftime("%B %d, %Y")
    system = f"""You are an expert Amazon FBA strategist for a seller launching an anti-aging device at $79.
Today is {today}. Category: Wrinkle & Anti-Aging Devices.
Competitors: {json.dumps(competitor_data)}
New entrants: {json.dumps(new_competitors)}
Be sharp, specific, and actionable. No fluff.

Use this structure:
💰 PRICING
⭐ REVIEW VELOCITY
🆕 NEW COMPETITORS
📊 MARKET PULSE
🎯 ACTION ITEMS (numbered 1-3)
⚡ LAUNCH ADVANTAGE"""
    return ask_claude(system, f"Give me today's full market brief for {today}.")

def get_weekly_summary(weekly_data):
    system = f"""You are an expert Amazon FBA strategist. Analyze this week of competitor data and give a weekly summary.
Weekly data: {json.dumps(weekly_data)}
Focus on: price trends over the week, review growth rates, BSR momentum, and what it means for next week's strategy.

Use this structure:
📅 WEEK IN REVIEW
📈 PRICE TRENDS
⭐ REVIEW GROWTH
📊 BSR MOMENTUM
🎯 NEXT WEEK STRATEGY (3 specific actions)"""
    return ask_claude(system, "Give me this week's summary.")

def analyze_any_product(product_data, question):
    system = f"""You are an expert Amazon FBA strategist specializing in Beauty & Personal Care.
The user is launching an anti-aging device at $79 and wants to know about this product.
Product data: {json.dumps(product_data)}
Be specific and compare to their $79 price point where relevant."""
    return ask_claude(system, question)

# ── Discord bot ───────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
conversation_histories = {}

def split_message(text, limit=1900):
    return [text[i:i+limit] for i in range(0, len(text), limit)]

async def send_to_channel(channel, text):
    for chunk in split_message(text):
        await channel.send(chunk)

@bot.event
async def on_ready():
    print(f"✅ Bot online as {bot.user}")
    daily_brief_task.start()
    price_check_task.start()
    weekly_summary_task.start()

# ── Scheduled tasks ───────────────────────────────────────────────────────────

@tasks.loop(hours=24)
async def daily_brief_task():
    now = datetime.now()
    if now.hour == 7:
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            return
        await channel.send("📊 Fetching your daily brief...")
        competitor_data = [fetch_product_data(c["asin"], c["name"]) for c in COMPETITORS]
        competitor_data = calculate_velocity(competitor_data)
        save_weekly_snapshot(competitor_data)
        new_competitors = fetch_new_competitors()
        brief = get_daily_brief(competitor_data, new_competitors)

        header = f"**🔍 DAILY BRIEF — {now.strftime('%B %d, %Y')}**\n\n**📌 SNAPSHOT**\n"
        for c in competitor_data:
            price = f"${c['price']:.2f}" if c['price'] else "N/A"
            reviews = f"{c['review_count']:,}" if c['review_count'] else "N/A"
            velocity = f"+{c['review_velocity']}" if c.get('review_velocity', 0) > 0 else str(c.get('review_velocity', 0))
            bsr = f"#{c['bsr']:,}" if c['bsr'] else "N/A"
            stock = "✅" if c['in_stock'] else "❌ OOS"
            header += f"• **{c['name']}**: {price} | ⭐{c['rating']} ({reviews} reviews, {velocity} today) | BSR {bsr} | {stock}\n"
        header += "\n💬 *Ask me anything — type any question or an Amazon ASIN/product name*\n\n"
        await send_to_channel(channel, header + brief)

@tasks.loop(hours=1)
async def price_check_task():
    competitor_data = [fetch_product_data(c["asin"], c["name"]) for c in COMPETITORS]
    alerts = check_price_changes(competitor_data)
    if alerts:
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        if channel:
            for alert in alerts:
                await channel.send(alert)

@tasks.loop(hours=168)  # Once a week
async def weekly_summary_task():
    now = datetime.now()
    if now.weekday() == 6 and now.hour == 8:  # Sunday 8am
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            return
        await channel.send("📅 Generating your weekly summary...")
        weekly_data = load_weekly_data()
        if weekly_data:
            summary = get_weekly_summary(weekly_data)
            await send_to_channel(channel, f"**📅 WEEKLY SUMMARY**\n\n{summary}")

# ── Message handler ───────────────────────────────────────────────────────────

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if message.channel.id != DISCORD_CHANNEL_ID:
        return

    user_input = message.content.strip()
    if not user_input:
        return

    channel_id = str(message.channel.id)
    if channel_id not in conversation_histories:
        conversation_histories[channel_id] = []

    async with message.channel.typing():

        # Check if it's a product lookup (ASIN or product search)
        words = user_input.split()
        is_asin = len(words) == 1 and len(words[0]) == 10 and words[0].isalnum()
        is_product_search = any(keyword in user_input.lower() for keyword in [
            "look up", "search for", "find", "what about", "check", "analyze", "asin"
        ])

        if is_asin or is_product_search:
            search_term = words[0] if is_asin else user_input
            await message.channel.send(f"🔍 Looking up `{search_term}`...")
            product_data = fetch_any_product(search_term)
            response = analyze_any_product(product_data, user_input)
            await send_to_channel(message.channel, response)
            return

        # Otherwise treat as a strategy question with conversation history
        conversation_histories[channel_id].append({
            "role": "user",
            "content": user_input
        })
        recent_history = conversation_histories[channel_id][-10:]

        competitor_data = [fetch_product_data(c["asin"], c["name"]) for c in COMPETITORS]
        system = f"""You are an expert Amazon FBA strategist for a seller launching an anti-aging device at $79.
Current competitor data: {json.dumps(competitor_data)}
Be direct, specific, and conversational. Reference the live data when relevant."""

        response = ask_claude(system, user_input, recent_history)
        conversation_histories[channel_id].append({
            "role": "assistant",
            "content": response
        })

    await send_to_channel(message.channel, response)

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)