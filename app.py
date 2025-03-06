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
WIX_API_KEY = os.getenv("WIX_API_KEY")
WIX_COLLECTION_URL = "https://www.wixapis.com/data/v1/collections/ChatUsage"
HEADERS = {
    "Authorization": f"Bearer {WIX_API_KEY}",
    "Content-Type": "application/json"
}

# ✅ Definição dos limites globais para controle de uso
DAILY_LIMIT = 0.22  # Limite de custo diário ($)
MESSAGE_LIMIT = 20  # Limite de mensagens por dia
COOLDOWN_TIME = 5   # Tempo mínimo entre mensagens (segundos)

# ✅ Dicionário global para rastrear o uso dos usuários
user_usage = {}

def load_instructions():
    """Carrega as instruções do arquivo instructions.md"""
    with open('instructions.md', 'r', encoding='utf-8') as file:
        return file.read()

def get_user_chat_usage(email):
    """Obtém os dados de uso do usuário do Wix CMS"""
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

    if "items" in response_json and response_json["items"]:
        return response_json["items"][0]
    else:
        print("⚠️ Nenhum dado encontrado para o usuário no Wix CMS.")
        return None

def update_user_chat_usage(email, tokens, cost, messages):
    """Atualiza os dados de uso do usuário no Wix CMS"""
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
        response = requests.patch(WIX_COLLECTION_URL, json=update_payload, headers=HEADERS)

    else:
        new_data = {
            "email": email,
            "tokensUsados": tokens,
            "custoTotal": cost,
            "mensagensEnviadas": messages,
            "ultimaMensagem": datetime.utcnow().isoformat(),
            "dataReset": today
        }

        response = requests.post(WIX_COLLECTION_URL, json={"items": [new_data]}, headers=HEADERS)

    if response.status_code != 200:
        print(f"⚠️ Erro ao atualizar usuário no Wix CMS: {response.json()}")

def reset_usage():
    """Reseta os dados de uso diariamente."""
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
            return jsonify({"response": f"⚠️ Você atingiu o limite diário de {MESSAGE_LIMIT} mensagens. Tente novamente amanhã."}), 429

        last_message_time = user_usage[user_id]["last_message_time"]
        if last_message_time:
            time_since_last = (datetime.utcnow() - last_message_time).total_seconds()
            if time_since_last < COOLDOWN_TIME:
                return jsonify({"response": f"⏳ Aguarde {COOLDOWN_TIME - int(time_since_last)} segundos antes de enviar outra mensagem."}), 429

        instructions = load_instructions()

        # ✅ Aguarda a resposta da OpenAI corretamente
        max_attempts = 10  # Limite de tentativas
        attempts = 0
        
        while attempts < max_attempts:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            
            if run_status.status == "completed":
                break
            elif run_status.status in ["failed", "cancelled"]:
                return jsonify({"response": f"⚠️ O assistente falhou ao processar a resposta. Status: {run_status.status}"}), 500
            elif run_status.status == "queued":
                print(f"⏳ Resposta ainda na fila... Tentativa {attempts + 1}/{max_attempts}")
            
            attempts += 1
            time.sleep(3)  # Espera 3 segundos antes de tentar de novo
        
        if run_status.status != "completed":
            return jsonify({"response": f"⚠️ O assistente demorou muito para responder. Status final: {run_status.status}."}), 500

        # ✅ Processar resposta do AI mantendo formatação
        if run.status == "completed":
            try:
                messages = client.beta.threads.messages.list(thread_id=thread.id)
        
                # ✅ LOG: Exibir a resposta completa para debug
                print("🔍 Debug: Resposta completa do OpenAI:", messages)
        
                if messages and messages.data and len(messages.data) > 0:
                    ai_response = messages.data[0].content[0].text.value.strip()
                else:
                    ai_response = "⚠️ O assistente não retornou uma resposta válida."
        
            except Exception as e:
                ai_response = f"⚠️ Erro ao processar resposta da IA: {str(e)}"
        
            # ✅ Limpeza e formatação da resposta
            ai_response = re.sub(r"https?:\/\/\S+", "", ai_response)  # Remove URLs
            ai_response = re.sub(r"\*\*(.*?)\*\*", r"\1", ai_response)  # Remove bold
            ai_response = re.sub(r"\*(.*?)\*", r"\1", ai_response)  # Remove itálico
            ai_response = re.sub(r"[【】\[\]†?]", "", ai_response)  # Remove símbolos especiais
            ai_response = re.sub(r"\d+:\d+[A-Za-z]?", "", ai_response)  # Remove padrões numéricos
            ai_response = " ".join(ai_response.split()[:300])  # Limita a 300 tokens
            ai_response = re.sub(r"\n?\d+\.\s*", "\n• ", ai_response)  # Formatação bullet points
            ai_response = re.sub(r"(-\s+)", "\n• ", ai_response)  # Formatação bullet points
            ai_response = re.sub(r"(?<!\n)\•", "\n•", ai_response)  # Garantia de quebra de linha
            ai_response = re.sub(r"(?<=[.!?])\s+", "\n\n", ai_response)  # Adiciona espaçamento entre frases
        
        else:
            ai_response = f"⚠️ O assistente não conseguiu gerar uma resposta. Status: {run.status}. Tente novamente."

        return jsonify({"response": ai_response})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500
