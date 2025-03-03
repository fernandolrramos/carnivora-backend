import openai
import os
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
import re
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

# ✅ Token pricing for GPT-4-Turbo
TOKEN_PRICING = {
    "input": 0.01 / 1000,  # $0.01 per 1,000 input tokens
    "output": 0.03 / 1000,  # $0.03 per 1,000 output tokens
}

# ✅ Usage tracking (resets daily)
user_usage = {}  # { "user_id": {"tokens": 0, "cost": 0.00, "messages": 0, "last_message_time": None, "date": "YYYY-MM-DD"} }
DAILY_LIMIT = 0.50  # $0.50 per user per day
MESSAGE_LIMIT = 20  # 20 messages per user per day
COOLDOWN_TIME = 15  # 15 seconds between messages

def reset_usage():
    """Resets usage data daily."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    for user_id in list(user_usage.keys()):
        if user_usage[user_id]["date"] != today:
            del user_usage[user_id]

@app.route("/chat", methods=["POST"])
def chat():
    try:
        reset_usage()
        data = request.get_json(silent=True)
        if not data or "message" not in data or "user_id" not in data:
            return jsonify({"response": "Erro: Nenhuma mensagem fornecida ou usuário não identificado."}), 400

        user_id = data["user_id"].strip()
        user_message = data["message"].strip()[:200]
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

        thread = client.beta.threads.create(messages=[{"role": "user", "content": user_message}])
        messages = client.beta.threads.messages.list(thread_id=thread.id)

        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID,
            instructions=f"Pergunta do usuário: {user_message}",
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
            
            # ✅ Clean AI response to remove unwanted formatting
            ai_response = re.sub(r"[【】\[\]†?]", "", ai_response)  # Removes symbols like 【 】 † ? and brackets
            ai_response = re.sub(r"\d+:\d+[A-Za-z]?", "", ai_response)  # Removes citation markers
            ai_response = " ".join(ai_response.split()[:300])  # Limit response to 300 tokens
            ai_response = re.sub(r"(?<!Dr)(?<!Sr)(?<!Sra)(?<!Prof)(?<!etc)(?<!vs)\.\s+", ".\n\n", ai_response, flags=re.IGNORECASE)
            ai_response = re.sub(r"\n?\d+\.\s*", "\n• ", ai_response)  # Replace numbered lists with bullets
            ai_response = re.sub(r"-\s+", "\n- ", ai_response)  # Ensure bullet points are formatted
            ai_response = re.sub(r"https?:\/\/\S+", "", ai_response)  # Remove standalone URLs
            ai_response = re.sub(r"\*\*(.*?)\*\*", r"\1", ai_response)  # Remove bold
            ai_response = re.sub(r"\*(.*?)\*", r"\1", ai_response)  # Remove italics
        else:
            ai_response = "⚠️ Erro: O assistente não retornou resposta válida."

        input_tokens = len(user_message.split()) * 1.3
        output_tokens = len(ai_response.split()) * 1.3

        input_cost = input_tokens * TOKEN_PRICING["input"]
        output_cost = output_tokens * TOKEN_PRICING["output"]
        total_cost = input_cost + output_cost

        user_usage[user_id]["tokens"] += (input_tokens + output_tokens)
        user_usage[user_id]["cost"] += total_cost
        user_usage[user_id]["messages"] += 1
        user_usage[user_id]["last_message_time"] = datetime.utcnow()

        if user_usage[user_id]["cost"] >= DAILY_LIMIT:
            return jsonify({"response": f"⚠️ Você atingiu o limite diário de ${DAILY_LIMIT:.2f}. Tente novamente amanhã."}), 429

        return jsonify({"response": ai_response})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500
