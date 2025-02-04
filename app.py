import openai
import os
import time
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

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
        instructions = (
            "Você é um assistente especializado na dieta carnívora e deve sempre responder em **português**. "
            "Assuma que todas as perguntas são sobre a dieta carnívora, mesmo que o usuário não mencione isso. "
            "Se a pergunta for sobre álcool, responda: **'Bebidas alcoólicas não fazem parte da dieta carnívora.'** "
            "Se a pergunta for sobre bebidas, responda: **'Na carnívora restrita, bebe-se apenas água (mineral ou com gás). Alguns carnívoros tomam chás ou café, mas sucos não fazem parte da dieta carnívora.'** "
            "Se a pergunta for sobre vegetais ou alimentos vegetais, responda: **'A dieta carnívora permite apenas alimentos de origem animal.'** "
            "Responda com frases curtas e diretas (máximo 2 frases). Sempre conecte a pergunta ao contexto da dieta carnívora."
            "Se houver informações nos arquivos carregados, use esses dados para responder antes de recorrer ao conhecimento geral."
            "Nunca mencione que houve erro ao acessar os documentos."
            "Se não puder encontrar uma resposta nos documentos, responda normalmente sem indicar que usará conhecimento geral."
            "Evite frases como ""parece que ocorreu um erro"" ou ""vou tentar processar sua solicitação de outra forma""."
            "Sempre forneça uma resposta clara e confiante sobre a dieta carnívora."
            "Se uma informação não estiver disponível, apenas diga que ""não há evidências suficientes"" em vez de mencionar um erro."
            "Não diga ""O que gostaria de saber sobre os arquivos enviados?"" ou qualquer variação dessa frase."
            "Quando o usuário perguntar sobre fibras, consulte o capítulo ""Chapter 9: Myth II — Fiber Is Necessary for a Healthy Gut"" do pdf The Carnivore code e responda de acordo com o que está escrito"
            "Quando o usuário perguntar sobre enfermidades, consulte os capítulos ""Chapter 10: Myth III — Red Meat Will Shorten Your Life"" e ""Chapter 11: Myth IV — Red Meat Causes the Heart to Explode"" do pdf The Carnivore code e responda de acordo com o que está escrito"
        )

        # ✅ Create a new OpenAI Assistant thread
        thread = client.beta.threads.create()
        print(f"✅ Thread created: {thread.id}")

        # ✅ Start AI processing with **improved** instructions
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID,
            instructions=f"Pergunta do usuário: {user_message}\n\n{instructions}",
            tool_choice="auto"
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

            time.sleep(2)

        # ✅ Retrieve AI response
        messages = client.beta.threads.messages.list(thread_id=thread.id)

        if messages.data:
            ai_response = messages.data[0].content[0].text.value.strip()
        else:
            ai_response = "⚠️ Erro: O assistente não retornou resposta válida."

        return jsonify({"response": ai_response})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
