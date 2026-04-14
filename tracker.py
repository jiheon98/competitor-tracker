import os
import json
import anthropic
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")
RAINFOREST_KEY = os.getenv("RAINFOREST_API_KEY")
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")

with open("competitors.json") as f:
    config = json.load(f)

MY_PRODUCT = config["my_product"]
COMPETITORS = config["competitors"]

# ── Fetch data from Rainforest API ────────────────────────────────────────────

def fetch_product_data(asin):
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
        "title": product.get("title", "Unknown"),
        "price": product.get("buybox_winner", {}).get("price", {}).get("value"),
        "rating": product.get("rating", None),
        "review_count": product.get("ratings_total", None),
        "bsr": product.get("bestsellers_rank", [{}])[0].get("rank") if product.get("bestsellers_rank") else None,
        "bsr_category": product.get("bestsellers_rank", [{}])[0].get("category") if product.get("bestsellers_rank") else None,
"in_stock": product.get("buybox_winner", {}) is not None and product.get("buybox_winner", {}) != {}    }

# ── Analyze with Claude ───────────────────────────────────────────────────────

def analyze_with_claude(competitor_data):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    today = datetime.now().strftime("%B %d, %Y")
    data_summary = json.dumps(competitor_data, indent=2)

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system="""You are an expert Amazon FBA strategist specializing in Beauty & Personal Care.
You give sharp, specific, actionable daily briefings to a seller launching a new 
anti-aging device at $79. Be direct and concise. No fluff.

Structure your response EXACTLY like this:

💰 PRICING
[Current competitor prices and what they mean for your $79 price point]

⭐ REVIEW ACTIVITY
[Notable review counts, ratings, trends worth noting]

📊 MARKET PULSE
[BSR rankings and what they signal about demand and competition]

🎯 YOUR ACTION ITEMS FOR TODAY
1. [Most important action]
2. [Second action]
3. [Third action]

⚡ LAUNCH ADVANTAGE
[One specific gap or opportunity you can exploit based on today's data]""",
        messages=[{
            "role": "user",
            "content": f"""Today is {today}.

My product: {MY_PRODUCT['name']} — launching at ${MY_PRODUCT['price']}
Category: {MY_PRODUCT['category']}

Here is today's competitor data:
{data_summary}

Give me today's briefing."""
        }]
    )
    return message.content[0].text

# ── Send to Discord ───────────────────────────────────────────────────────────

def send_to_discord(analysis, competitor_data):
    today = datetime.now().strftime("%B %d, %Y")

    header = f"**🔍 COMPETITOR BRIEF — {today}**\n\n**📌 SNAPSHOT**\n"
    for c in competitor_data:
        price = f"${c['price']:.2f}" if c['price'] else "N/A"
        reviews = f"{c['review_count']:,}" if c['review_count'] else "N/A"
        bsr = f"#{c['bsr']:,}" if c['bsr'] else "N/A"
        stock = "✅ In Stock" if c['in_stock'] else "❌ Out of Stock"
        header += f"• **{c['name']}**: {price} | ⭐{c['rating']} ({reviews} reviews) | BSR {bsr} | {stock}\n"

    full_message = header + "\n" + analysis
    chunks = [full_message[i:i+1900] for i in range(0, len(full_message), 1900)]
    for chunk in chunks:
        requests.post(DISCORD_WEBHOOK, json={"content": chunk})

    print(f"✅ Brief sent to Discord at {datetime.now().strftime('%H:%M')}")

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("📦 Fetching competitor data...")
    competitor_data = []
    for competitor in COMPETITORS:
        print(f"   Fetching {competitor['name']}...")
        data = fetch_product_data(competitor["asin"])
        data["name"] = competitor["name"]
        competitor_data.append(data)

    print("🤖 Analyzing with Claude...")
    analysis = analyze_with_claude(competitor_data)

    print("📨 Sending to Discord...")
    send_to_discord(analysis, competitor_data)