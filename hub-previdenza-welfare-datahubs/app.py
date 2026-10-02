import os
import anthropic
from flask import Flask, render_template, request, redirect, url_for, session, jsonify

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hub-previdenza-welfare-2026')

# Credenziali fisse
USERS = {
    'admin@datahubs.it': 'Admin2026!'
}

# Carica KB
def load_kb():
    kb = {}
    kb_dir = os.path.join(os.path.dirname(__file__), 'kb')
    for fname in os.listdir(kb_dir):
        if fname.endswith('.txt'):
            with open(os.path.join(kb_dir, fname), 'r', encoding='utf-8') as f:
                kb[fname.replace('.txt', '')] = f.read()
    return kb

KB = load_kb()

SYSTEM_PROMPT = """Sei il P&W Advisor, l'assistente virtuale dell'Hub Previdenza e Welfare di DataHubs S.r.l.

Regole di comportamento ASSOLUTE:
1. Rispondi ESCLUSIVAMENTE sulla base della Knowledge Base certificata fornita. Nessuna informazione al di fuori del perimetro.
2. Non generare mai informazioni non presenti nella KB (zero allucinazioni). Se la domanda non è coperta dalla KB, rispondi: "Questa informazione non è presente nella Knowledge Base. Per una risposta accurata, contatta la sede territoriale INPS competente."
3. Indica SEMPRE la fonte (titolo del documento, numero circolare/messaggio e data) per ogni informazione restituita.
4. Adatta la lunghezza della risposta: breve per domande di sintesi, dettagliata per approfondimenti.
5. Mantieni il contesto della conversazione per domande di follow-up.
6. Rispondi sempre in italiano.
7. Sei preciso e professionale. Non inventi, non supponi, non estrapoli.

Knowledge Base disponibile:
""" + "\n\n---\n\n".join(KB.values())


@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('index.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        if email in USERS and USERS[email] == password:
            session['user'] = email
            return redirect(url_for('index'))
        else:
            error = 'Credenziali non valide. Riprova.'
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/ask', methods=['POST'])
def ask():
    if 'user' not in session:
        return jsonify({'error': 'Non autorizzato'}), 401

    data = request.get_json()
    messages = data.get('messages', [])

    if not messages:
        return jsonify({'error': 'Nessun messaggio'}), 400

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'API key non configurata'}), 500

    client = anthropic.Anthropic(api_key=api_key)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages
        )
        return jsonify({'response': response.content[0].text})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    app.run(debug=False)
