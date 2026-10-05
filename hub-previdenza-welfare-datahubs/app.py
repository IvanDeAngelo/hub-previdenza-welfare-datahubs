import os
import markdown
import anthropic
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response, stream_with_context

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hub-previdenza-welfare-2026')

USERS = {
    'admin@datahubs.it': 'Admin2026!'
}

KB_FILES = {
    'posizione_contributiva': 'posizione_contributiva.md',
    'accompagnamento_pensione': 'accompagnamento_pensione.md',
    'durc_online': 'durc_online.md',
    'cigo': 'cigo.md',
    'fondi_sanitari_contrattuali': 'fondi_sanitari_contrattuali.md',
    'sanita_fiscale': 'sanita_fiscale.md',
}

def load_kb():
    kb_html = {}
    kb_raw = {}
    kb_dir = os.path.join(os.path.dirname(__file__), 'kb')
    for key, fname in KB_FILES.items():
        fpath = os.path.join(kb_dir, fname)
        if os.path.exists(fpath):
            with open(fpath, 'r', encoding='utf-8') as f:
                raw = f.read()
                kb_raw[key] = raw
                kb_html[key] = markdown.markdown(
                    raw,
                    extensions=['tables', 'nl2br']
                )
        else:
            kb_html[key] = '<p>Contenuto non disponibile.</p>'
            kb_raw[key] = ''
    return kb_html, kb_raw

KB, KB_RAW = load_kb()

SYSTEM_PROMPT = """Sei il P&W Advisor, l'assistente virtuale dell'Hub Previdenza e Welfare di DataHubs S.r.l.

Regole di comportamento ASSOLUTE:
1. Rispondi ESCLUSIVAMENTE sulla base della Knowledge Base certificata fornita. Nessuna informazione al di fuori del perimetro.
2. Non generare mai informazioni non presenti nella KB. Se la domanda non è coperta dalla KB, rispondi esattamente: "Questa informazione non è presente nella Knowledge Base. Per una risposta accurata, contatta la sede territoriale INPS competente."
3. Indica SEMPRE la fonte (numero circolare/messaggio e data) per ogni informazione restituita.
4. Rispondi in formato testo semplice, senza usare markdown (no #, no **, no ---, no tabelle markdown). Usa testo normale con a capo per separare i concetti.
5. Adatta la lunghezza: breve per domande di sintesi, dettagliata per approfondimenti.
6. Mantieni il contesto della conversazione per domande di follow-up.
7. Rispondi sempre in italiano.
8. Concludi sempre la risposta con: "Hai altre domande su questo tema o su altri argomenti della Knowledge Base?"

Knowledge Base disponibile:

""" + "\n\n---\n\n".join([f"TEMA: {k}\n{v}" for k, v in KB_RAW.items()])


@app.route('/')
def index():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('index.html', kb=KB)


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

    def generate():
        with client.messages.stream(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages
        ) as stream:
            for text in stream.text_stream:
                yield f"data: {text}\n\n"
        yield "data: [DONE]\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


if __name__ == '__main__':
    app.run(debug=False)
