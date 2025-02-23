import openai
import os
import time
import sqlite3  # ✅ SQLite for persistent tracking
from flask import Flask, request, jsonify
from flask_cors import CORS
import re
import stripe

try:
    import stripe
    print("✅ Stripe is installed and can be imported.")
except ImportError:
    print("❌ Stripe is NOT installed.")

app = Flask(__name__)
CORS(app)

# ✅ Set up SQLite for request tracking
conn = sqlite3.connect("requests.db", check_same_thread=False)
cursor = conn.cursor()

# ✅ Create table to track user requests
cursor.execute("""
    CREATE TABLE IF NOT EXISTS request_limits (
        ip TEXT PRIMARY KEY,
        count INTEGER DEFAULT 0,
        last_request TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
""")
conn.commit()

# ✅ Functions to manage user requests
def get_user_request_count(ip):
    cursor.execute("SELECT count FROM request_limits WHERE ip = ?", (ip,))
    result = cursor.fetchone()
    return result[0] if result else 0

def increment_user_request(ip):
    if get_user_request_count(ip) == 0:
        cursor.execute("INSERT INTO request_limits (ip, count) VALUES (?, 1)", (ip,))
    else:
        cursor.execute("UPDATE request_limits SET count = count + 1 WHERE ip = ?", (ip,))
    conn.commit()

def reset_user_requests():
    cursor.execute("DELETE FROM request_limits")
    conn.commit()

# ✅ Route to manually reset limits (use a cron job to automate this)
@app.route("/reset_limits", methods=["POST"])
def reset_limits():
    reset_user_requests()
    return jsonify({"status": "success", "message": "Request limits reset."})

# ✅ Load environment variables
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")
DAILY_LIMIT = int(os.getenv("DAILY_MESSAGE_LIMIT", 20))  # Default 20 if not set

if not OPENAI_API_KEY or not ASSISTANT_ID:
    raise ValueError("⚠️ Missing OpenAI credentials. Check environment variables.")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

def load_instructions():
    with open('instructions.md', 'r', encoding='utf-8') as file:
        return file.read()

instructions = load_instructions()

@app.route("/", methods=["GET"])
def home():
    return "Flask backend is running!", 200

@app.route("/chat", methods=["POST"])
def chat():
    try:
        user_ip = request.remote_addr  
        user_count = get_user_request_count(user_ip)

        # ✅ Check daily request limit
        if user_count >= DAILY_LIMIT:
            return jsonify({"response": f"⚠️ Limite diário de {DAILY_LIMIT} mensagens atingido. Tente novamente amanhã."}), 429

        increment_user_request(user_ip)  # ✅ Increment request count for the user

        # ✅ Process AI request
        data = request.get_json(silent=True)
        if not data or "message" not in data:
            return jsonify({"response": "Erro: Nenhuma mensagem fornecida."}), 400

        user_message = data["message"].strip()
        if len(user_message) > 200:
            user_message = user_message[:200] + "..."

        thread = client.beta.threads.create(messages=[{"role": "user", "content": user_message}])
        messages = client.beta.threads.messages.list(thread_id=thread.id)

        if len(messages.data) > 3:
            messages.data = messages.data[-3:]

        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID,
            instructions=f"Pergunta do usuário: {user_message}\n\n{instructions}",
            tool_choice="auto",
        )

        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if run_status.status == "completed":
                break
            elif run_status.status == "failed":
                return jsonify({"response": "⚠️ Erro ao processar a resposta do assistente."}), 500
            time.sleep(3)

        messages = client.beta.threads.messages.list(thread_id=thread.id)

        if messages.data:
            ai_response = messages.data[0].content[0].text.value.strip()

            # ✅ Remove document names (e.g., "arquivo.pdf")
            ai_response = re.sub(r"\b[A-Za-z0-9\s]+\.pdf\b", "", ai_response).strip()

            # ✅ Remove unwanted artifacts
            ai_response = re.sub(r"[【】\[\]†?]", "", ai_response)

            # ✅ Remove citation markers (e.g., 4:4A)
            ai_response = re.sub(r"\d+:\d+[A-Za-z]?", "", ai_response)

            # ✅ Format list items with symbols (instead of numbers)
            ai_response = re.sub(r"\n?\d+\.\s*", "\n• ", ai_response)

            # ✅ Prevent "Dr." and similar abbreviations from triggering a new line
            ai_response = re.sub(r"(?<!Dr)(?<!Sr)(?<!Sra)(?<!Prof)(?<!etc)(?<!vs)\.\s+", ".\n\n", ai_response, flags=re.IGNORECASE)

            # ✅ Remove Instagram links but keep usernames
            ai_response = re.sub(r"\(https?:\/\/www\.instagram\.com\/[^\)]+\)", "", ai_response)

            # ✅ Remove any other standalone URLs
            ai_response = re.sub(r"https?:\/\/\S+", "", ai_response)

            # ✅ Remove Markdown bold (**text**) and italics (*text*)
            ai_response = re.sub(r"\*\*(.*?)\*\*", r"\1", ai_response)
            ai_response = re.sub(r"\*(.*?)\*", r"\1", ai_response)

        else:
            ai_response = "⚠️ Erro: O assistente não retornou resposta válida."

        return jsonify({"response": ai_response})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

# ✅ Ensure Gunicorn finds the app instance
application = app  # 🔥 Required for deployment
