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
        print(f"✅ Thread created: {thread.id}")

        # ✅ Start AI processing with **limited tokens** to control cost
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=ASSISTANT_ID,
            instructions=instructions,  # ✅ Removed user_message from instructions to prevent repetition
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

            time.sleep(3)

        # ✅ Retrieve AI response
        messages = client.beta.threads.messages.list(thread_id=thread.id)

        if messages.data:
            ai_response = messages.data[0].content[0].text.value.strip()

            # ✅ Limit AI response to 300 tokens
            ai_response = " ".join(ai_response.split()[:300])

            # ✅ Format text for better readability
            ai_response = ai_response.replace("- ", "\n- ")  # Ensure list items appear on new lines
            ai_response = ai_response.replace("**", "")  # Remove bold markers
            ai_response = ai_response.replace(". ", ".\n\n")  # Add line breaks after sentences
            ai_response = ai_response.replace(":", ":\n")  # Add new line after colons for lists

        else:
            ai_response = "⚠️ Erro: O assistente não retornou resposta válida."

        return jsonify({"response": ai_response})

    except Exception as e:
        return jsonify({"response": f"Erro interno do servidor: {str(e)}"}), 500
