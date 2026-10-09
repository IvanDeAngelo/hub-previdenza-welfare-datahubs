"""Applicazione Contact Center — Hub Previdenza e Welfare (Fase 1, uso interno).

Mockup per gli operatori delle sedi territoriali: coda dei case, P&W Advisor
collegato al case, Knowledge Base, consultazione libera e casi CRM.
Usa la stessa base documentale dell'Hub pubblico (cartella kb/).
"""
import os
import markdown
import anthropic
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response, stream_with_context

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'contact-center-datahubs-2026')

# Utenze abilitate (demo)
OPERATORS = {
    'operatore@datahubs.it': 'Operatore2026!',
    'admin@datahubs.it': 'Admin2026!',
}
MODEL = os.environ.get('MODEL', 'claude-sonnet-4-6')

KB_FILES = {
    'posizione_contributiva': 'posizione_contributiva.md',
    'accompagnamento_pensione': 'accompagnamento_pensione.md',
    'durc_online': 'durc_online.md',
    'cigo': 'cigo.md',
    'fondi_sanitari_contrattuali': 'fondi_sanitari_contrattuali.md',
    'sanita_fiscale': 'sanita_fiscale.md',
    'certificazione_parita_genere': 'certificazione_parita_genere.md',
}


def load_kb():
    kb_html, kb_raw = {}, {}
    kb_dir = os.path.join(os.path.dirname(__file__), 'kb')
    for key, fname in KB_FILES.items():
        fpath = os.path.join(kb_dir, fname)
        if os.path.exists(fpath):
            with open(fpath, 'r', encoding='utf-8') as f:
                raw = f.read()
            kb_raw[key] = raw
            kb_html[key] = markdown.markdown(raw, extensions=['tables', 'nl2br'])
        else:
            kb_raw[key] = ''
            kb_html[key] = '<p>Contenuto non disponibile.</p>'
    return kb_html, kb_raw


KB, KB_RAW = load_kb()
KB_TEXT = "\n\n---\n\n".join(
    [f"ID ARTICOLO: {k.replace('_', '-')}\n{v}" for k, v in KB_RAW.items()]
)

SYSTEM_PROMPT = """Sei il P&W Advisor, l'assistente interno dell'Applicazione Contact Center dell'Hub Previdenza e Welfare (DataHubs S.r.l.).
Supporti gli operatori delle sedi territoriali che gestiscono le richieste di aziende, HR, dirigenti e professionisti su previdenza, welfare aziendale e sanità integrativa.

Regole:
1. Usa SOLO la documentazione approvata fornita sotto. Il contesto del case (messaggi dell'utente, note dell'operatore) serve a interpretare la domanda, ma NON è una fonte: non ricavarne indicazioni previdenziali o di welfare.
2. Rispondi in modo operativo e sintetico: brevi paragrafi separati da una riga vuota, al massimo 4 paragrafi. Testo semplice, senza markdown (no #, no **, no elenchi puntati con trattini).
3. Alla fine della frase che si basa su un paragrafo della documentazione, inserisci il riferimento nel formato esatto [[id-articolo#numero-paragrafo]], dove id-articolo è l'ID ARTICOLO della documentazione e numero-paragrafo è il numero del paragrafo "## N.". Usa da 1 a 3 riferimenti diversi in tutto.
4. Se la documentazione non consente di rispondere, dillo chiaramente, indica quali informazioni chiedere all'utente oppure suggerisci il rinvio alla sede territoriale competente. Non inventare importi, termini o riferimenti normativi.
5. Se manca un dato del case necessario per rispondere (ad esempio il piano, la matricola, la data dell'evento), segnalalo come informazione da verificare con l'utente.
6. Rispondi in italiano. Non aggiungere saluti o frasi di chiusura.

Documentazione approvata:

""" + KB_TEXT

SYSTEM_PROMPT_DRAFT = """Sei il P&W Advisor dell'Applicazione Contact Center dell'Hub Previdenza e Welfare (DataHubs S.r.l.).
Prepari una BOZZA di risposta che l'operatore rivedrà e invierà all'utente sul canale del case.

Regole:
1. Basati SOLO sulla documentazione approvata fornita sotto e sulla conversazione del case.
2. Tono cortese e professionale, dai del Lei, rivolgiti all'utente per nome se presente. Massimo 110 parole.
3. Testo semplice, senza markdown, senza riferimenti tra parentesi quadre e senza citare l'assistente o la documentazione interna. Puoi citare atti ufficiali (es. "Circolare INPS n. 4/2026") se utili all'utente.
4. Se servono informazioni aggiuntive dall'utente, chiudi con una domanda precisa.
5. Rispondi solo con il testo della bozza.

Documentazione approvata:

""" + KB_TEXT


def is_operator():
    return session.get('user') in OPERATORS


@app.route('/')
def index():
    return render_template(
        'index.html', kb=KB, operator=is_operator(),
        login_error=request.args.get('err') == '1',
    )


@app.route('/accesso', methods=['POST'])
def accesso():
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    if OPERATORS.get(email) == password:
        session['user'] = email
        return redirect(url_for('index'))
    return redirect(url_for('index', err='1'))


@app.route('/esci')
def esci():
    session.clear()
    return redirect(url_for('index'))


@app.route('/cc/ask', methods=['POST'])
def cc_ask():
    if not is_operator():
        return jsonify({'error': 'Accesso riservato agli operatori'}), 401
    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'API key non configurata'}), 500

    data = request.get_json(silent=True) or {}
    messages = [
        {'role': m.get('role'), 'content': str(m.get('content', ''))[:4000]}
        for m in data.get('messages', [])[-16:]
        if m.get('role') in ('user', 'assistant') and m.get('content')
    ]
    mode = data.get('mode', 'risposta')
    case = data.get('case') or {}
    context = ''
    if case:
        context = (
            "CONTESTO DEL CASE (non è una fonte certificata)\n"
            f"Case: {case.get('id', '')} · canale {case.get('canale', '')}\n"
            f"Utente: {case.get('nome', '')} · {case.get('profilo', '')}\n"
            f"Oggetto: {case.get('oggetto', '')}\n"
            f"Conversazione con l'utente:\n{str(case.get('conversazione', ''))[:3000]}\n\n"
        )

    if mode == 'bozza':
        system = SYSTEM_PROMPT_DRAFT
        convo = [{'role': 'user', 'content': context + "Prepara la bozza di risposta all'ultimo messaggio dell'utente."}]
        max_tokens = 400
    else:
        if not messages or messages[-1]['role'] != 'user':
            return jsonify({'error': 'Nessuna domanda'}), 400
        system = SYSTEM_PROMPT
        if context:
            messages[-1] = {'role': 'user', 'content': context + "DOMANDA DELL'OPERATORE: " + messages[-1]['content']}
        convo = messages
        max_tokens = 900

    client = anthropic.Anthropic(api_key=api_key)

    def generate():
        with client.messages.stream(model=MODEL, max_tokens=max_tokens, system=system, messages=convo) as stream:
            for text in stream.text_stream:
                yield f"data: {text}\n\n"
        yield "data: [DONE]\n\n"

    return Response(stream_with_context(generate()), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


if __name__ == '__main__':
    app.run(debug=False)
