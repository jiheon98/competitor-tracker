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

def load_json(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f)

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
            "price": product.get("buybox_winner", {}).get("price", {}).get("value"),
            "rating": product.get("rating"),
            "review_count": product.get("ratings_total"),
            "bsr": product.get("bestsellers_rank", [{}])[0].get("rank") if product.get("bestsellers_rank") else None,
            "in_stock": product.get("buybox_winner", {}) not in [None, {}]
        }
    except Exception as e:
        return {"asin": asin, "name": name, "error": str(e)}

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
        return []

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

def save_weekly_snapshot(competitor_data):
    weekly = load_json("weekly_history.json")
    today = str(datetime.now().date())
    weekly[today] = competitor_data
    if len(weekly) > 7:
        oldest = sorted(weekly.keys())[0]
        del weekly[oldest]
    save_json("weekly_history.json", weekly)

def get_checklist_actions():
    checklist = load_json("launch_checklist.json")
    if not checklist:
        return ""
    target = datetime.strptime(checklist["target_launch_date"], "%Y-%m-%d")
    days_left = (target - datetime.now()).days
    current_stage = checklist["current_stage"]
    stage = next((s for s in checklist["stages"] if s["id"] == current_stage), None)
    if not stage:
        return "🎉 All launch stages complete!"
    pending = [t for t in stage["tasks"] if not t["done"]]
    done_count = len(stage["tasks"]) - len(pending)
    total = len(stage["tasks"])
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        system=f"""You are an Amazon FBA launch coach. Seller is launching anti-aging device at $79.
Stage {current_stage}: {stage['name']}, {days_left} days until launch.
Completed {done_count}/{total} tasks. Pending: {json.dumps(pending)}
Give exactly 3 prioritized actions for TODAY. Be specific and urgent. Under 120 words.""",
        messages=[{"role": "user", "content": "Top 3 launch actions for today?"}]
    )
    return message.content[0].text

def send_discord(message):
    chunks = [message[i:i+1900] for i in range(0, len(message), 1900)]
    for chunk in chunks:
        requests.post(DISCORD_WEBHOOK, json={"content": chunk})

def get_brief(competitor_data, new_competitors):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    today = datetime.now().strftime("%B %d, %Y")
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=f"""You are an expert Amazon FBA strategist for a seller launching an anti-aging device at $79.
Today is {today}. Category: Wrinkle & Anti-Aging Devices.
Competitor data: {json.dumps(competitor_data)}
New entrants: {json.dumps(new_competitors)}
Be sharp, specific, actionable. No fluff.

Use this structure:
💰 PRICING
⭐ REVIEW VELOCITY
🆕 NEW COMPETITORS
📊 MARKET PULSE
🎯 ACTION ITEMS
1.
2.
3.
⚡ LAUNCH ADVANTAGE""",
        messages=[{"role": "user", "content": f"Daily brief for {today}."}]
    )
    return message.content[0].text

if __name__ == "__main__":
    print("📦 Fetching competitor data...")
    competitor_data = [fetch_product_data(c["asin"], c["name"]) for c in COMPETITORS]
    competitor_data = calculate_velocity(competitor_data)
    save_weekly_snapshot(competitor_data)
    new_competitors = fetch_new_competitors()

    print("🤖 Analyzing with Claude...")
    brief = get_brief(competitor_data, new_competitors)
    checklist = get_checklist_actions()

    today = datetime.now().strftime("%B %d, %Y")
    header = f"**🔍 DAILY BRIEF — {today}**\n\n**📌 SNAPSHOT**\n"
    for c in competitor_data:
        price = f"${c['price']:.2f}" if c.get('price') else "N/A"
        reviews = f"{c['review_count']:,}" if c.get('review_count') else "N/A"
        velocity = c.get('review_velocity', 0)
        vel_str = f"+{velocity}" if velocity > 0 else str(velocity)
        bsr = f"#{c['bsr']:,}" if c.get('bsr') else "N/A"
        stock = "✅" if c.get('in_stock') else "❌ OOS"
        header += f"• **{c['name']}**: {price} | ⭐{c.get('rating', 'N/A')} ({reviews} reviews, {vel_str} today) | BSR {bsr} | {stock}\n"

    full_message = header + "\n" + brief
    if checklist:
        full_message += f"\n\n**🚀 TODAY'S LAUNCH ACTIONS**\n{checklist}"

    print("📨 Sending to Discord...")
    send_discord(full_message)
    print("✅ Done!")