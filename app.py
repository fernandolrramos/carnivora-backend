import openai
import os
import time
from flask import Flask, request, jsonify
from flask_cors import CORS
import re
#import stripe

app = Flask(__name__)
CORS(app)

#------------------------------------

# Set your Stripe secret key (store this securely)
#stripe.api_key = os.getenv("STRIPE_SECRET_KEY")  # Use environment variables

# Webhook secret (get this from Stripe Dashboard)
#WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

#@app.route('/webhook', methods=['POST'])
#def stripe_webhook():
#    payload = request.get_data(as_text=True)
#    sig_header = request.headers.get('Stripe-Signature')
#
#    try:
#        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
#    except ValueError:
#        return jsonify({'error': 'Invalid payload'}), 400
#    except stripe.error.SignatureVerificationError:
#        return jsonify({'error': 'Invalid signature'}), 400
#
#    # ✅ Handle successful checkout
#    if event['type'] == 'checkout.session.completed':
#       session = event['data']['object']
#       print(f"✅ Payment received for {session['amount_total']} cents!")
#       # TODO: Add logic to update the user’s subscription in your database
#
#    return jsonify({'status': 'success'}), 200

#------------------------------------

# ✅ OpenAI API Key and Assistant ID
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

# ✅ Check if API keys exist
if not OPENAI_API_KEY:
    raise ValueError("⚠️ Error: OPENAI_API_KEY is not set. Make sure it is properly configured.")

if not ASSISTANT_ID:
    raise ValueError("⚠️ Error: ASSISTANT_ID is not set. Make sure it is properly configured.")

# ✅ Initialize OpenAI Client
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# ✅ Track user requests to limit abuse
user_requests = {}

def load_instructions():
    with open('instructions.md','r',encoding='utf-8') as file:
        return file.read()

instructions = load_instructions()

@app.route("/", methods=["GET"])
def home():
    return "Flask backend is running!", 200

@app.route("/chat", methods=["POST"])
def chat():
    try:
        user_ip = request.remote_addr  

        # ✅ Safely decode request body to avoid Unicode errors
        try:
            raw_data = request.data.decode("utf-8", errors="ignore")
        except Exception as e:
            print(f"❌ Decoding Error: {e}")
            return jsonify({"response": "Erro: Falha ao decodificar a mensagem."}), 400
        
        print("📩 Received Request!")
        print("Request Data:", raw_data)

        data = request.get_json(silent=True)
        if not data or "message" not in data:
            return jsonify({"response": "Erro: Nenhuma mensagem fornecida."}), 400

        user_message = data["message"].strip()
        if not user_message:
            return jsonify({"response": "Erro: Mensagem vazia recebida."}), 400

        # ✅ Limit users to 50 requests per day
        if user_ip not in user_requests:
            user_requests[user_ip] = 0
        if user_requests[user_ip] >= 50:
            return jsonify({"response": "⚠️ Limite diário de 50 mensagens atingido. Tente novamente amanhã."}), 429
        user_requests[user_ip] += 1

        # ✅ Load assistant instructions
        instructions = load_instructions()

        # ✅ Create a new OpenAI Assistant thread and add user message
        thread = client.beta.threads.create(
            messages=[
                {"role": "user", "content": user_message}
            ]
        )
        print(f"✅ Thread created: {thread.id}")

        # ✅ Start AI processing
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID,
            instructions=f"Pergunta do usuário: {user_message}\n\n{instructions}",
            parameters={"tool_choice": "auto"}  # ✅ Corrected placement
        )
        print(f"⏳ Run started: {run.id}")

        # ✅ Wait for AI response
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            print(f"Checking run status: {run_status.status}")

            if run_status.status == "completed":
                break
            elif run_status.status == "requires_action":
                return jsonify({"response": "⚠️ O assistente precisa de mais informações para responder."}), 400
            elif run_status.status == "failed":
                return jsonify({"response": "⚠️ Erro ao processar a resposta do assistente."}), 500

            time.sleep(2)

        # ✅ Retrieve latest AI response
        messages = client.beta.threads.messages.list(thread_id=thread.id)
        if messages.data:
            latest_message = sorted(messages.data, key=lambda x: x.created_at, reverse=True)[0]
            ai_response = latest_message.content[0].text.value.strip()
        else:
            ai_response = "⚠️ Erro: O assistente não retornou resposta válida."

        return jsonify({"response": ai_response})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
