import openai
import os
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
import re
import stripe
from datetime import datetime

try:
    import stripe
    print("✅ Stripe is installed and can be imported.")
except ImportError:
    print("❌ Stripe is NOT installed.")

app = Flask(__name__)
CORS(app)

# ✅ Set Stripe API Key
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")  # Ensure this exists in Render Environment Variables
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")  # Webhook Secret from Stripe Dashboard

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

    return jsonify({'status': 'success'}), 200

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
user_usage = {}  # { "user_id": {"tokens": 0, "cost": 0.00, "date": "YYYY-MM-DD"} }
DAILY_LIMIT = 0.50  # $0.50 per user per day

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

        user_id = data["user_id"]
        user_message = data["message"].strip()[:200]
        today = datetime.utcnow().strftime("%Y-%m-%d")

        if user_id not in user_usage:
            user_usage[user_id] = {"tokens": 0, "cost": 0.00, "date": today}

        if user_usage[user_id]["cost"] >= DAILY_LIMIT:
            return jsonify({"response": f"⚠️ Você atingiu o limite diário de ${DAILY_LIMIT:.2f}. Tente novamente amanhã."}), 429

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

            # ✅ Restore previous AI response processing
            ai_response = re.sub(r"[【】\[\]†?]", "", ai_response)
            ai_response = re.sub(r"\d+:\d+[A-Za-z]?", "", ai_response)
            ai_response = " ".join(ai_response.split()[:300])
            ai_response = re.sub(r"(?<!Dr)(?<!Sr)(?<!Sra)(?<!Prof)(?<!etc)(?<!vs)\.\s+", ".\n\n", ai_response, flags=re.IGNORECASE)
            ai_response = re.sub(r"\n?\d+\.\s*", "\n• ", ai_response)
            ai_response = re.sub(r"-\s+", "\n- ", ai_response)
            ai_response = re.sub(r"https?:\/\/\S+", "", ai_response)
            ai_response = re.sub(r"\*\*(.*?)\*\*", r"\1", ai_response)
            ai_response = re.sub(r"\*(.*?)\*", r"\1", ai_response)
        else:
            ai_response = "⚠️ Erro: O assistente não retornou resposta válida."

        return jsonify({"response": ai_response})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500
