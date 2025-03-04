import openai
import os
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
import re
import stripe
from datetime import datetime, timedelta

app = Flask(__name__)
CORS(app)

# ✅ OpenAI API Key and Assistant ID
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

if not OPENAI_API_KEY:
    raise ValueError("⚠️ Error: OPENAI_API_KEY is not set. Make sure it is properly configured.")
if not ASSISTANT_ID:
    raise ValueError("⚠️ Error: ASSISTANT_ID is not set. Make sure it is properly configured.")

client = openai.OpenAI(api_key=OPENAI_API_KEY)

# ✅ Stripe Configuration
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# ✅ Token pricing for GPT-4-Turbo
TOKEN_PRICING = {
    "input": 0.01 / 1000,  # $0.01 per 1,000 input tokens
    "output": 0.03 / 1000,  # $0.03 per 1,000 output tokens
}

# ✅ Usage tracking (resets daily)
user_usage = {}  # { "user_id": {"tokens": 0, "cost": 0.00, "messages": 0, "last_message_time": None, "date": "YYYY-MM-DD"} }
DAILY_LIMIT = 0.01  # $0.50 per user per day
MESSAGE_LIMIT = 20  # X messages per user per day
COOLDOWN_TIME = 5  # 5 seconds between messages

def reset_usage():
    """Resets usage data daily."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    for user_id in list(user_usage.keys()):
        if user_usage[user_id]["date"] != today:
            del user_usage[user_id]

def load_instructions():
    with open('instructions.md','r',encoding='utf-8') as file:
        return file.read()

@app.route("/chat", methods=["POST"])
def chat():
    try:
        reset_usage()
        data = request.get_json(silent=True)
        if not data or "message" not in data or "user_id" not in data:
            return jsonify({"response": "Erro: Nenhuma mensagem fornecida ou usuário não identificado."}), 400

        user_id = data["user_id"].strip()
        user_message = data["message"].strip()[:150]  # Shorter messages
        today = datetime.utcnow().strftime("%Y-%m-%d")

        if user_id not in user_usage:
            user_usage[user_id] = {"tokens": 0, "cost": 0.00, "messages": 0, "last_message_time": None, "date": today}

        # ✅ Enforce daily message limit
        if user_usage[user_id]["messages"] >= MESSAGE_LIMIT:
            return jsonify({"response": f"⚠️ Você atingiu o limite diário de {MESSAGE_LIMIT} mensagens. Tente novamente amanhã."}), 429

        # ✅ Enforce daily cost limit
        if user_usage[user_id]["cost"] >= DAILY_LIMIT:
            return jsonify({"response": f"⚠️ Você atingiu o limite diário de ${DAILY_LIMIT:.2f}. Tente novamente amanhã."}), 429

        # ✅ Enforce cooldown time
        last_message_time = user_usage[user_id]["last_message_time"]
        if last_message_time:
            time_since_last = (datetime.utcnow() - last_message_time).total_seconds()
            if time_since_last < COOLDOWN_TIME:
                return jsonify({"response": f"⏳ Aguarde {COOLDOWN_TIME - int(time_since_last)} segundos antes de enviar outra mensagem."}), 429

        instructions = load_instructions()
        
        thread = client.beta.threads.create(messages=[{"role": "user", "content": user_message}])
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
        else:
            ai_response = "⚠️ Erro: O assistente não retornou resposta válida."

        # ✅ Retrieve token usage and cost calculation
        run_details = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        usage = run_details.usage
        
        if usage:
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens
            cost = (input_tokens * TOKEN_PRICING["input"]) + (output_tokens * TOKEN_PRICING["output"])
        else:
            input_tokens = 0
            output_tokens = 0
            cost = 0.00
        
        # ✅ Update user usage tracking
        user_usage[user_id]["tokens"] += input_tokens + output_tokens
        user_usage[user_id]["cost"] += cost
        user_usage[user_id]["messages"] += 1
        user_usage[user_id]["last_message_time"] = datetime.utcnow()
        
        return jsonify({"response": ai_response, "tokens_used": input_tokens + output_tokens, "cost": round(cost, 4)})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500
