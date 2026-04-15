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

# ── File helpers ──────────────────────────────────────────────────────────────

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

# ── Rainforest API calls ──────────────────────────────────────────────────────

def fetch_product_data(asin, name):
    url = "https://api.rainforestapi.com/request"
    params = {
        "api_key": RAINFOREST_KEY,
        "type": "product",
        "asin": asin,
        "amazon_domain": "amazon.com"
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        product = data.get("product", {})
        return {
            "asin": asin,
            "name": name,
            "title": product.get("title", "Unknown"),
            "price": product.get("buybox_winner", {}).get("price", {}).get("value"),
            "rating": product.get("rating"),
            "review_count": product.get("ratings_total"),
            "bsr": product.get("bestsellers_rank", [{}])[0].get("rank") if product.get("bestsellers_rank") else None,
            "bsr_category": product.get("bestsellers_rank", [{}])[0].get("category") if product.get("bestsellers_rank") else None,
            "in_stock": product.get("buybox_winner", {}) not in [None, {}]
        }
    except Exception as e:
        print(f"Error fetching {asin}: {e}")
        return {"asin": asin, "name": name, "error": str(e)}

def fetch_reviews(asin, name, num_reviews=30):
    url = "https://api.rainforestapi.com/request"
    params = {
        "api_key": RAINFOREST_KEY,
        "type": "reviews",
        "asin": asin,
        "amazon_domain": "amazon.com",
        "sort_by": "most_recent"
    }
    try:
        response = requests.get(url, params=params, timeout=15)
        data = response.json()
        reviews = data.get("reviews", [])[:num_reviews]
        return {
            "name": name,
            "asin": asin,
            "reviews": [{
                "rating": r.get("rating"),
                "title": r.get("title", ""),
                "body": r.get("body", "")[:400],
                "date": r.get("date", ""),
                "verified": r.get("verified_purchase", False)
            } for r in reviews]
        }
    except Exception as e:
        print(f"Error fetching reviews for {asin}: {e}")
        return {"name": name, "asin": asin, "reviews": [], "error": str(e)}

def fetch_any_product(query):
    url = "https://api.rainforestapi.com/request"
    is_asin = len(query.split()) == 1 and len(query) == 10 and query.isalnum()
    if is_asin:
        params = {
            "api_key": RAINFOREST_KEY,
            "type": "product",
            "asin": query,
            "amazon_domain": "amazon.com"
        }
        try:
            response = requests.get(url, params=params, timeout=15)
            data = response.json()
            product = data.get("product", {})
            return [{
                "name": product.get("title", "Unknown")[:80],
                "asin": query,
                "price": product.get("buybox_winner", {}).get("price", {}).get("value"),
                "rating": product.get("rating"),
                "review_count": product.get("ratings_total"),
                "bsr": product.get("bestsellers_rank", [{}])[0].get("rank") if product.get("bestsellers_rank") else None,
            }]
        except Exception as e:
            return [{"error": str(e)}]
    else:
        params = {
            "api_key": RAINFOREST_KEY,
            "type": "search",
            "search_term": query,
            "amazon_domain": "amazon.com"
        }
        try:
            response = requests.get(url, params=params, timeout=15)
            data = response.json()
            results = data.get("search_results", [])[:5]
            return [{
                "name": r.get("title", "Unknown")[:80],
                "asin": r.get("asin"),
                "price": r.get("price", {}).get("value"),
                "rating": r.get("rating"),
                "review_count": r.get("ratings_total"),
                "bsr": None
            } for r in results]
        except Exception as e:
            return [{"error": str(e)}]

def fetch_new_competitors():
    url = "https://api.rainforestapi.com/request"
    params = {
        "api_key": RAINFOREST_KEY,
        "type": "search",
        "search_term": "anti aging face device red light therapy",
        "amazon_domain": "amazon.com"
    }
    try:
        response = requests.get(url, params=params, timeout=15)
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
    except Exception as e:
        print(f"Error fetching new competitors: {e}")
        return []

# ── Price change detection ────────────────────────────────────────────────────

def check_price_changes(competitor_data):
    price_history = load_json("price_history.json")
    alerts = []
    for product in competitor_data:
        asin = product["asin"]
        current_price = product.get("price")
        previous_price = price_history.get(asin, {}).get("price")
        if current_price and previous_price and current_price != previous_price:
            diff = current_price - previous_price
            pct = (diff / previous_price) * 100
            direction = "🔴 DROPPED" if diff < 0 else "🟢 RAISED"
            alerts.append(
                f"⚡ **PRICE ALERT — {product['name']}**\n"
                f"{direction} from ${previous_price:.2f} → ${current_price:.2f} ({pct:+.1f}%)"
            )
        price_history[asin] = {"price": current_price, "date": str(datetime.now())}
    save_json("price_history.json", price_history)
    return alerts

# ── Review velocity ───────────────────────────────────────────────────────────

def calculate_velocity(competitor_data):
    history = load_json("review_history.json")
    for product in competitor_data:
        asin = product["asin"]
        current = product.get("review_count") or 0
        previous = history.get(asin, {}).get("review_count", current)
        product["review_velocity"] = current - previous
        history[asin] = {"review_count": current, "date": str(datetime.now().date())}
    save_json("review_history.json", history)
    return competitor_data

# ── Weekly snapshot ───────────────────────────────────────────────────────────

def save_weekly_snapshot(competitor_data):
    weekly = load_json("weekly_history.json")
    today = str(datetime.now().date())
    weekly[today] = competitor_data
    if len(weekly) > 7:
        oldest = sorted(weekly.keys())[0]
        del weekly[oldest]
    save_json("weekly_history.json", weekly)

# ── Claude calls ──────────────────────────────────────────────────────────────

def ask_claude(system_prompt, user_message, history=None):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    messages = history if history else [{"role": "user", "content": user_message}]
    try:
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system_prompt,
            messages=messages
        )
        return message.content[0].text
    except Exception as e:
        return f"Error calling Claude: {e}"

def get_daily_brief(competitor_data, new_competitors):
    today = datetime.now().strftime("%B %d, %Y")
    system = f"""You are an expert Amazon FBA strategist for a seller launching an anti-aging device at $79.
Today is {today}. Category: Wrinkle & Anti-Aging Devices.
My product: {json.dumps(MY_PRODUCT)}
Competitor data: {json.dumps(competitor_data)}
New entrants today: {json.dumps(new_competitors)}
Be sharp, specific, and actionable. No fluff.

Use this exact structure:
💰 PRICING
[Analyze current prices vs my $79 and what it means]

⭐ REVIEW VELOCITY
[New reviews since yesterday and signals]

🆕 NEW COMPETITORS
[Any new entrants or "None detected"]

📊 MARKET PULSE
[BSR trends and demand signals]

🎯 ACTION ITEMS
1. [Action]
2. [Action]
3. [Action]

⚡ LAUNCH ADVANTAGE
[One specific gap to exploit today]"""
    return ask_claude(system, f"Give me today's full market brief for {today}.")

def get_weekly_summary(weekly_data):
    system = f"""You are an expert Amazon FBA strategist.
Analyze this week of competitor data and give a strategic weekly summary.
Weekly data: {json.dumps(weekly_data)}

Use this structure:
📅 WEEK IN REVIEW
[Overall summary of what happened]

📈 PRICE TRENDS
[How prices moved this week]

⭐ REVIEW GROWTH
[Review velocity trends]

📊 BSR MOMENTUM
[Ranking trends and what they mean]

🎯 NEXT WEEK STRATEGY
1. [Action]
2. [Action]
3. [Action]"""
    return ask_claude(system, "Give me this week's summary.")

def analyze_reviews(all_reviews, question):
    system = f"""You are an expert Amazon FBA strategist and consumer insights analyst.
The user is launching an anti-aging device at $79 and wants insights from competitor reviews.
Review data: {json.dumps(all_reviews)}

When analyzing reviews:
- Identify the top 3 most common complaints (these are YOUR opportunities)
- Identify the top 3 most praised features (these are table stakes you must match)
- Suggest specific listing copy improvements based on what customers want
- Identify any recurring keywords customers use that should be in your listing
- Rate each competitor's review sentiment 1-10"""
    return ask_claude(system, question)

def analyze_product(product_data, question):
    system = f"""You are an expert Amazon FBA strategist specializing in Beauty & Personal Care.
The user is launching an anti-aging device at $79.
Product data: {json.dumps(product_data)}
Compare to their $79 price point where relevant. Be specific and actionable."""
    return ask_claude(system, question)

def answer_strategy_question(competitor_data, question, history):
    system = f"""You are an expert Amazon FBA strategist — the user's personal market advisor.
They are launching an anti-aging device at $79 in the Wrinkle & Anti-Aging Devices category.
My product details: {json.dumps(MY_PRODUCT)}
Current live competitor data: {json.dumps(competitor_data)}

Be conversational, direct, and specific. Reference the live data when relevant.
If asked about pricing, factor in competitor prices. If asked about reviews, note the counts.
You have memory of this conversation so reference previous messages when relevant."""
    return ask_claude(system, question, history)

# ── Discord helpers ───────────────────────────────────────────────────────────

def split_message(text, limit=1900):
    return [text[i:i+limit] for i in range(0, len(text), limit)]

async def send_to_channel(channel, text):
    for chunk in split_message(text):
        await channel.send(chunk)

def build_snapshot_header(competitor_data):
    today = datetime.now().strftime("%B %d, %Y")
    header = f"**🔍 DAILY BRIEF — {today}**\n\n**📌 LIVE SNAPSHOT**\n"
    for c in competitor_data:
        price = f"${c['price']:.2f}" if c.get('price') else "N/A"
        reviews = f"{c['review_count']:,}" if c.get('review_count') else "N/A"
        velocity = c.get('review_velocity', 0)
        vel_str = f"+{velocity}" if velocity > 0 else str(velocity)
        bsr = f"#{c['bsr']:,}" if c.get('bsr') else "N/A"
        stock = "✅" if c.get('in_stock') else "❌ OOS"
        header += f"• **{c['name']}**: {price} | ⭐{c.get('rating', 'N/A')} ({reviews} reviews, {vel_str} today) | BSR {bsr} | {stock}\n"
    header += "\n💬 *Ask me anything — strategy questions, product lookups, review analysis*\n\n"
    return header

# ── Discord bot ───────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)
conversation_histories = {}

@bot.event
async def on_ready():
    print(f"✅ Bot online as {bot.user}")
    price_check_task.start()
    weekly_summary_task.start()
    daily_brief_task.start()

# ── Scheduled tasks ───────────────────────────────────────────────────────────

@tasks.loop(hours=24)
async def daily_brief_task():
    now = datetime.now()
    if now.hour == 7:
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            return
        await channel.send("⏳ Fetching your daily brief...")
        competitor_data = [fetch_product_data(c["asin"], c["name"]) for c in COMPETITORS]
        competitor_data = calculate_velocity(competitor_data)
        save_weekly_snapshot(competitor_data)
        new_competitors = fetch_new_competitors()
        brief = get_daily_brief(competitor_data, new_competitors)
        header = build_snapshot_header(competitor_data)
        await send_to_channel(channel, header + brief)

@tasks.loop(hours=1)
async def price_check_task():
    try:
        competitor_data = [fetch_product_data(c["asin"], c["name"]) for c in COMPETITORS]
        alerts = check_price_changes(competitor_data)
        if alerts:
            channel = bot.get_channel(DISCORD_CHANNEL_ID)
            if channel:
                for alert in alerts:
                    await channel.send(alert)
    except Exception as e:
        print(f"Price check error: {e}")

@tasks.loop(hours=168)
async def weekly_summary_task():
    now = datetime.now()
    if now.weekday() == 6 and now.hour == 8:
        channel = bot.get_channel(DISCORD_CHANNEL_ID)
        if not channel:
            return
        await channel.send("📅 Generating your weekly summary...")
        weekly_data = load_json("weekly_history.json")
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
        text = user_input.lower()

        # ── Review analysis ───────────────────────────────────────────────────
        if any(k in text for k in ["review", "complaint", "feedback", "customers say", "weakness", "problem", "issue", "negative", "positive"]):
            await message.channel.send("📝 Fetching competitor reviews... (this takes ~20 seconds)")
            all_reviews = [fetch_reviews(c["asin"], c["name"]) for c in COMPETITORS]
            response = analyze_reviews(all_reviews, user_input)
            await send_to_channel(message.channel, response)
            return

        # ── Product lookup ────────────────────────────────────────────────────
        words = user_input.split()
        is_asin = len(words) == 1 and len(words[0]) == 10 and words[0].isalnum()
        is_search = any(k in text for k in ["look up", "search for", "find product", "what about", "check this", "analyze this", "asin"])
        if is_asin or is_search:
            search_term = words[0] if is_asin else user_input
            await message.channel.send(f"🔍 Looking up `{search_term}`...")
            product_data = fetch_any_product(search_term)
            response = analyze_product(product_data, user_input)
            await send_to_channel(message.channel, response)
            return

        # ── Daily brief on demand ─────────────────────────────────────────────
        if any(k in text for k in ["daily brief", "morning brief", "today's brief", "send brief"]):
            await message.channel.send("⏳ Generating brief...")
            competitor_data = [fetch_product_data(c["asin"], c["name"]) for c in COMPETITORS]
            competitor_data = calculate_velocity(competitor_data)
            new_competitors = fetch_new_competitors()
            brief = get_daily_brief(competitor_data, new_competitors)
            header = build_snapshot_header(competitor_data)
            await send_to_channel(message.channel, header + brief)
            return

        # ── Weekly summary on demand ──────────────────────────────────────────
        if any(k in text for k in ["weekly", "week summary", "this week", "week in review"]):
            await message.channel.send("📅 Generating weekly summary...")
            weekly_data = load_json("weekly_history.json")
            if weekly_data:
                summary = get_weekly_summary(weekly_data)
                await send_to_channel(message.channel, f"**📅 WEEKLY SUMMARY**\n\n{summary}")
            else:
                await message.channel.send("No weekly data yet — check back after a few days of running!")
            return

        # ── General strategy question ─────────────────────────────────────────
        conversation_histories[channel_id].append({
            "role": "user",
            "content": user_input
        })
        recent_history = conversation_histories[channel_id][-10:]
        competitor_data = [fetch_product_data(c["asin"], c["name"]) for c in COMPETITORS]
        response = answer_strategy_question(competitor_data, user_input, recent_history)
        conversation_histories[channel_id].append({
            "role": "assistant",
            "content": response
        })

    await send_to_channel(message.channel, response)

# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)