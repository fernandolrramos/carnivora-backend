import openai
import os
import time
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
   # ✅ Handle successful checkout
#    if event['type'] == 'checkout.session.completed':
#       session = event['data']['object']
#       print(f"✅ Payment received for {session['amount_total']} cents!")
#       # TODO: Add logic to update the user’s subscription in your database
#
#   return jsonify({'status': 'success'}), 200
#------------------------------------

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

    # ✅ Handle relevant Stripe events
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        print(f"✅ Payment received for {session['amount_total']} cents!")
        # TODO: Add logic to update the user's subscription in your database

    return jsonify({'status': 'success'}), 200


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
# Use regex to extract Instagram profiles
#instagram_profiles = re.findall(r'\*\*([^*]+)\*\*: \[([^]]+)\]\((https://www.instagram.com/[^)]+)\)', content)

# Convert the profile list into HTML format
#profile_html = "<ul>"
#for name, handle, url in instagram_profiles:
#    profile_html += f'<li><a href="{url}" target="_blank">{name}</a> - {handle}</li>'
#profile_html += "</ul>"

#return content + "\n" + profile_html

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
        if len(user_message) > 200:
           user_message = user_message[:200] + "..."

        # ✅ Ensure the message is correctly formatted
        if not user_message:
            return jsonify({"response": "Erro: Mensagem vazia recebida."}), 400

        # ✅ Limit users to 50 requests per day
        if user_ip not in user_requests:
            user_requests[user_ip] = 0

        if user_requests[user_ip] >= 50:
            return jsonify({"response": "⚠️ Limite diário de 50 mensagens atingido. Tente novamente amanhã."}), 429

        user_requests[user_ip] += 1

        # ✅ AI Assistant Instructions for Portuguese + Context Awareness
        instructions = load_instructions()

        # ✅ Create a new OpenAI Assistant thread
        thread = client.beta.threads.create(messages=[{"role": "user", "content": user_message}])
        # Limit to 3 most recent messages to avoid long conversations
        messages = client.beta.threads.messages.list(thread_id=thread.id)
        if len(messages.data) > 3:
           messages.data = messages.data[-3:]
        print(f"✅ Thread created: {thread.id}")

        # ✅ Start AI processing with **improved** instructions
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID,
            instructions=f"Pergunta do usuário: {user_message}\n\n{instructions}",
            tool_choice="auto",
        )

        print(f"⏳ Run started: {run.id}")

        # ✅ Wait for AI response
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            print(f"Checking run status: {run_status.status}")

            if run_status.status == "completed":
                break
            elif run_status.status == "failed":
                return jsonify({"response": "⚠️ Erro ao processar a resposta do assistente."}), 500

            time.sleep(3)

        # ✅ Retrieve AI response
        messages = client.beta.threads.messages.list(thread_id=thread.id)

        if messages.data:
            ai_response = messages.data[0].content[0].text.value.strip()

            # ✅ Remove unwanted special characters like 【4:0†? and other artifacts
            ai_response = re.sub(r"[【】\[\]†?]", "", ai_response)  # Removes symbols like 【 】 † ? and brackets
        
            # ✅ Remove numbers attached to words that look like citation markers (e.g., 4:4A)
            ai_response = re.sub(r"\d+:\d+[A-Za-z]?", "", ai_response)  # Removes patterns like 4:4A or 5:2B
        
            # ✅ Limit AI response to 300 tokens
            ai_response = " ".join(ai_response.split()[:300])
        
            # ✅ Ensure each sentence appears on a new line
            ai_response = re.sub(r"(?<!\d)\.\s+", ".\n\n", ai_response)  # Add new lines after periods (excluding decimal numbers)
        
            # ✅ Ensure list items remain properly formatted
            ai_response = re.sub(r"-\s+", "\n- ", ai_response)  # Keep bullet points formatted

            # ✅ Prevent "Dr." and similar abbreviations from triggering a new line
            ai_response = re.sub(r"(?<!Dr)(?<!Sr)(?<!Sra)(?<!Prof)(?<!etc)(?<!vs)\.\s+", ".\n\n", ai_response, flags=re.IGNORECASE)
            
        else:
            ai_response = "⚠️ Erro: O assistente não retornou resposta válida."

        return jsonify({"response": ai_response})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

# ✅ Ensure Gunicorn finds the app instance
application = app  # 🔥 Add this line

