import openai
import os
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
import re
import stripe
import requests
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

# ✅ Configuração da API do Wix
#WIX_API_KEY = os.getenv("WIX_API_KEY")
WIX_API_KEY = "IST.eyJraWQiOiJQb3pIX2FDMiIsImFsZyI6IlJTMjU2In0.eyJkYXRhIjoie1wiaWRcIjpcImJkNTViMTZlLWZiZjEtNDUxYy1iM2EwLTMzNDYzOWFmMGM3OVwiLFwiaWRlbnRpdHlcIjp7XCJ0eXBlXCI6XCJhcHBsaWNhdGlvblwiLFwiaWRcIjpcIjE0ODYwMjc4LTUxMDAtNGM1YS1hZWY1LTA0NTA5ODEyNGMxMVwifSxcInRlbmFudFwiOntcInR5cGVcIjpcImFjY291bnRcIixcImlkXCI6XCJkYTZhY2Y5Yi1mOTE4LTQ3M2YtYjhjMC1mMWFkMzFmZTRhYmRcIn19IiwiaWF0IjoxNzQxMjkwMTQ2fQ.KIbaBda0kXCbJavO2QbHwWrdK1oKrjeQExqVeFS3zSxezIOM19uAjU4OiMtqL3QH2I_dA9a85BM7Wvn46ZVwC7T48Rwh_pc1SNZaUlzlJKyQ8E94KktouWwdf7m1Y7atbBUp4TXfYtISDynCa1ZddPsTXxqOQ6Q-uHKqAQUdoid9ZCTGO6b_5nzwmQdRAPmRXf76LWqMEzN1kmVvHco-cbhGRMtSHm-GpAtk0l10wG7Jqrpdbx6nOl5RT5Hn2A7A4cqC5qSApsSS9vVzXsHOTJxdQMQ6Ddk0b-5SU---IrZPEqCnTj7ui-zcQ8RW8n_jhnPJtnl4yznAOAseXIFtmw"
WIX_COLLECTION_URL = "https://www.wixapis.com/data/v1/collections/ChatUsage"
HEADERS = {
    "Authorization": f"Bearer {WIX_API_KEY}",
    "Content-Type": "application/json"
}

# ✅ Preços de tokens para GPT-4-Turbo
TOKEN_PRICING = {
    "input": 0.01 / 1000,  # $0.01 por 1.000 tokens de entrada
    "output": 0.03 / 1000,  # $0.03 por 1.000 tokens de saída
}

# ✅ Definição dos limites de uso
DAILY_LIMIT = 0.5  # Limite diário de custo ($)
MESSAGE_LIMIT = 1  # Limite diário de mensagens
COOLDOWN_TIME = 5   # Tempo mínimo entre mensagens (segundos)

# ✅ Dicionário para rastrear o uso dos usuários
user_usage = {}

def get_user_chat_usage(email):
    """ Obtém os dados de uso do usuário no Wix CMS """
    today = datetime.utcnow().strftime("%Y-%m-%d")

    query_payload = {
        "dataQuery": {
            "filter": {
                "operator": "and",
                "predicates": [
                    {"fieldName": "email", "operator": "eq", "value": email},
                    {"fieldName": "dataReset", "operator": "eq", "value": today}
                ]
            }
        }
    }

    response = requests.post(f"{WIX_COLLECTION_URL}/query", json=query_payload, headers=HEADERS)

    if response.status_code != 200:
        print(f"⚠️ Erro ao buscar usuário no Wix CMS: Código {response.status_code}, Resposta: {response.text}")
        return None

    try:
        response_json = response.json()
    except ValueError:
        print(f"❌ Erro: Resposta inválida do Wix CMS. Resposta: {response.text}")
        return None

    return response_json["items"][0] if "items" in response_json and response_json["items"] else None

def update_user_chat_usage(email, tokens, cost, messages):
    """ Atualiza os dados de uso do usuário no Wix CMS """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    
    user_data = get_user_chat_usage(email)
    
    if user_data:
        item_id = user_data["_id"]

        updated_data = {
            "tokensUsados": user_data["tokensUsados"] + tokens,
            "custoTotal": user_data["custoTotal"] + cost,
            "mensagensEnviadas": user_data["mensagensEnviadas"] + messages,
            "ultimaMensagem": datetime.utcnow().isoformat()
        }

        update_payload = {"items": [{"_id": item_id, **updated_data}]}
        requests.patch(WIX_COLLECTION_URL, json=update_payload, headers=HEADERS)

    else:
        new_data = {
            "email": email,
            "tokensUsados": tokens,
            "custoTotal": cost,
            "mensagensEnviadas": messages,
            "ultimaMensagem": datetime.utcnow().isoformat(),
            "dataReset": today
        }

        requests.post(WIX_COLLECTION_URL, json={"items": [new_data]}, headers=HEADERS)

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
        user_message = data["message"].strip()[:150]
        today = datetime.utcnow().strftime("%Y-%m-%d")

        user_data = get_user_chat_usage(user_id)
        
        if user_data:
            user_usage[user_id] = {
                "tokens": user_data["tokensUsados"],
                "cost": user_data["custoTotal"],
                "messages": user_data["mensagensEnviadas"],
                "last_message_time": user_data["ultimaMensagem"],
                "date": today
            }
        else:
            user_usage[user_id] = {"tokens": 0, "cost": 0.00, "messages": 0, "last_message_time": None, "date": today}

        if user_usage[user_id]["messages"] >= MESSAGE_LIMIT:
            return jsonify({"response": f"⚠️ Você atingiu o limite diário de {MESSAGE_LIMIT} mensagens."}), 429

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
        usage = getattr(run, "usage", {})

        input_tokens = getattr(usage, "prompt_tokens", 0)
        output_tokens = getattr(usage, "completion_tokens", 0)
        total_tokens = input_tokens + output_tokens
        cost = (input_tokens * TOKEN_PRICING["input"]) + (output_tokens * TOKEN_PRICING["output"])

        if user_usage[user_id]["cost"] + cost >= DAILY_LIMIT:
            return jsonify({"response": "⚠️ Você atingiu o limite diário de créditos."}), 429

        update_user_chat_usage(user_id, total_tokens, cost, 1)

        ai_response = messages.data[0].content[0].text.value.strip() if messages.data else "⚠️ Erro: O assistente não retornou resposta válida."

        return jsonify({"response": ai_response, "tokens_used": total_tokens, "cost": round(user_usage[user_id]['cost'] + cost, 4)})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500
