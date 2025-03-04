import openai
import os
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
import re
import stripe
from datetime import datetime, timedelta
import requests  # Precisamos disso para chamar a API do Wix

# ✅ Planos de Assinatura
SUBSCRIPTION_PLANS = {
    "basic": {
        "daily_limit": 0.01,  # Exemplo: $0.01 de limite diário
        "message_limit": 20,  # 20 mensagens por dia
    },
    "premium": {
        "daily_limit": 0.5,  # Exemplo: $0.03 de limite diário (3x mais)
        "message_limit": 60,  # 60 mensagens por dia
    }
}

# ✅ Função para buscar o plano de assinatura do usuário no Wix
def get_user_plan(user_id):
    """Busca o plano do usuário no Wix."""
    try:
        wix_api_url = "https://www.wixapis.com/members/get"  # Ajuste conforme necessário
        headers = {
            "Authorization": "Bearer SEU_WIX_API_TOKEN",  # Trocar pelo token correto do Wix
            "Content-Type": "application/json"
        }
        response = requests.post(wix_api_url, headers=headers, json={"email": user_id})
        data = response.json()

        plan = data.get("subscriptionPlan", "basic")  # Retorna "basic" se não encontrar

        print(f"✅ Plano obtido do Wix para {user_id}: {plan}")  # Adicionando log para depuração
        return plan
    except Exception as e:
        print(f"⚠️ Erro ao buscar plano no Wix para {user_id}: {str(e)}")
        return "basic"  # Se houver erro, assume plano básico

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
COOLDOWN_TIME = 2  # X seconds between messages

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

        # ✅ Obtém o plano do usuário a partir do Wix
        user_plan = get_user_plan(user_id)
        DAILY_LIMIT = SUBSCRIPTION_PLANS[user_plan]["daily_limit"]
        MESSAGE_LIMIT = SUBSCRIPTION_PLANS[user_plan]["message_limit"]

        print(f"🔹 {user_id} está no plano: {user_plan} (Limite diário: {DAILY_LIMIT}, Mensagens: {MESSAGE_LIMIT})")

        if user_id not in user_usage:
            user_usage[user_id] = {"tokens": 0, "cost": 0.00, "messages": 0, "last_message_time": None, "date": today}

        # ✅ Enforce daily message limit
        if user_usage[user_id]["messages"] >= MESSAGE_LIMIT:
            return jsonify({"response": "⚠️ Você atingiu o limite diário de mensagens. Tente novamente amanhã ou selecione outro plano de assinatura para continuar utilizando a IA Carnívora."}), 429

        # ✅ Enforce daily cost limit
        if user_usage[user_id]["cost"] >= DAILY_LIMIT:
            return jsonify({"response": "⚠️ Você atingiu o limite diário de mensagens. Tente novamente amanhã ou selecione outro plano de assinatura para continuar utilizando a IA Carnívora."}), 429

        # ✅ Continua o processamento normal...

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
            
            # ✅ Shorten AI response and clean formatting
            ai_response = re.sub(r"https?:\/\/\S+", "", ai_response)  # Remove standalone URLs
            ai_response = re.sub(r"\(@([A-Za-z0-9_.]+)\($", r"(@\1)", ai_response)  # Fix incomplete Instagram handles
            ai_response = re.sub(r"\*\*(.*?)\*\*", r"\1", ai_response)  # Remove bold
            ai_response = re.sub(r"\*(.*?)\*", r"\1", ai_response)  # Remove italics
            ai_response = re.sub(r"[【】\[\]†?]", "", ai_response)  # Remove symbols
        else:
            ai_response = "⚠️ Erro: O assistente não retornou resposta válida."

        return jsonify({"response": ai_response})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500
