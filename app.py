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

#--------------------------
@app.route('/get_user_info', methods=['GET'])
def get_user_info():
    """Retorna o e-mail do usuário logado, se disponível."""
    
    # Primeiro, tentamos recuperar o e-mail do cabeçalho da requisição
    user_email = request.headers.get('X-User-Email')
    
    # Alternativa: Tenta recuperar via query string se não estiver no cabeçalho
    if not user_email:
        user_email = request.args.get('email')  # Exemplo: /get_user_info?email=usuario@email.com
    
    if not user_email:
        return jsonify({"error": "Usuário não autenticado. O e-mail não foi enviado pelo Wix."}), 401

    return jsonify({"email": user_email})
#--------------------------

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except ValueError:
        return jsonify({'error': 'Invalid payload'}), 400
    except stripe.error.SignatureVerificationError:
        return jsonify({'error': 'Invalid signature'}), 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        print(f"✅ Payment received for {session['amount_total']} cents!")
        # TODO: Add logic to update the user's subscription in your database

    return jsonify({'status': 'success'}), 200

# ✅ Token pricing for GPT-4-Turbo
TOKEN_PRICING = {
    "input": 0.01 / 1000,  # $0.01 per 1,000 input tokens
    "output": 0.03 / 1000,  # $0.03 per 1,000 output tokens
}

# ✅ Usage tracking (resets daily)
user_usage = {}  # { "user_id": {"tokens": 0, "cost": 0.00, "messages": 0, "last_message_time": None, "date": "YYYY-MM-DD"} }
DAILY_LIMIT = 0.15  # $X por usuário por dia
MESSAGE_LIMIT = 20  #X mensagens por dia
COOLDOWN_TIME = 5  #X segundos entre mensagens

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
        user_message = data["message"].strip()[:150]  # Limita mensagem a 150 caracteres
        today = datetime.utcnow().strftime("%Y-%m-%d")

        if user_id not in user_usage:
            user_usage[user_id] = {"tokens": 0, "cost": 0.00, "messages": 0, "last_message_time": None, "date": today}

        # ✅ Enforce daily message limit
        if user_usage[user_id]["messages"] >= MESSAGE_LIMIT:
            return jsonify({"response": f"⚠️ Você atingiu o limite diário de {MESSAGE_LIMIT} mensagens. Tente novamente amanhã."}), 429

        # ✅ Enforce cooldown time
        last_message_time = user_usage[user_id]["last_message_time"]
        if last_message_time:
            time_since_last = (datetime.utcnow() - last_message_time).total_seconds()
            if time_since_last < COOLDOWN_TIME:
                return jsonify({"response": f"⏳ Aguarde {COOLDOWN_TIME - int(time_since_last)} segundos antes de enviar outra mensagem."}), 429

        instructions = load_instructions()

        # ✅ Enviar mensagem para o OpenAI
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

        # ✅ Recuperar resposta e tokens usados
        messages = client.beta.threads.messages.list(thread_id=thread.id)
        run_details = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        usage = getattr(run_details, "usage", {})  # Se não existir, retorna um dicionário vazio
        
        input_tokens = getattr(usage, "prompt_tokens", 0)
        output_tokens = getattr(usage, "completion_tokens", 0)
        total_tokens = input_tokens + output_tokens
        
        # ✅ Calcular custo real antes de permitir mensagem
        cost = (input_tokens * TOKEN_PRICING["input"]) + (output_tokens * TOKEN_PRICING["output"])
        new_cost = user_usage[user_id]["cost"] + cost
        
        # ✅ Bloquear se ultrapassar o limite de custo
        if new_cost >= DAILY_LIMIT:
            return jsonify({"response": f"⚠️ Você atingiu o limite diário de créditos. Tente novamente amanhã."}), 429
        
        # ✅ Atualizar rastreamento de uso
        user_usage[user_id]["tokens"] += total_tokens
        user_usage[user_id]["cost"] = new_cost  # Atualiza custo total
        user_usage[user_id]["messages"] += 1
        user_usage[user_id]["last_message_time"] = datetime.utcnow()

        # ✅ Processar resposta do AI com a formatação correta
        if messages.data:
            ai_response = messages.data[0].content[0].text.value.strip()
        
            # ✅ Limpeza e formatação da resposta
            ai_response = re.sub(r"https?:\/\/\S+", "", ai_response)  # Remove URLs
            ai_response = re.sub(r"\*\*(.*?)\*\*", r"\1", ai_response)  # Remove bold
            ai_response = re.sub(r"\*(.*?)\*", r"\1", ai_response)  # Remove itálico
            ai_response = re.sub(r"[【】\[\]†?]", "", ai_response)  # Remove símbolos especiais
            ai_response = re.sub(r"\d+:\d+[A-Za-z]?", "", ai_response)  # Remove padrões numéricos como 4:4A
            ai_response = " ".join(ai_response.split()[:300])  # Limita a 300 tokens
        
            # ✅ Melhorando a formatação de listas e itens
            ai_response = re.sub(r"\n?\d+\.\s*", "\n• ", ai_response)  # Transforma listas numeradas em bullet points
            ai_response = re.sub(r"(-\s+)", "\n• ", ai_response)  # Garante que traços também virem bullet points
            ai_response = re.sub(r"(?<!\n)\•", "\n•", ai_response)  # Garante quebra de linha antes de bullet points soltos
            ai_response = re.sub(r"(?<=[.!?])\s+", "\n\n", ai_response)  # Adiciona quebra de linha após cada frase
        
            # ✅ Garante que @username e descrição fiquem na mesma linha
            ai_response = re.sub(r"•\s*@(\w+)\s*\n\s*", r"• @\1 - ", ai_response)
        
            # ✅ Garante que "Dr." não fique isolado em uma linha separada
            ai_response = re.sub(r"\bDr\.\s*\n\s*", "Dr. ", ai_response)
        
            # ✅ Remove bullet points vazios
            ai_response = re.sub(r"\n•\s*\n", "\n", ai_response)
        
        else:
            ai_response = "⚠️ Erro: O assistente não retornou resposta válida."

        return jsonify({"response": ai_response, "tokens_used": total_tokens, "cost": round(new_cost, 4)})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500
