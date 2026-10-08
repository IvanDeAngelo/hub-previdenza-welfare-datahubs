import os
import json
import datetime
import markdown
import anthropic
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response, stream_with_context

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'hub-previdenza-welfare-2026')

# Utenze abilitate all'area riservata (demo)
USERS = {
    'admin@datahubs.it': 'Admin2026!'
}
ADMINS = {'admin@datahubs.it'}

# Limiti assistente versione pubblica
PUBLIC_MAX_QUESTIONS = int(os.environ.get('PUBLIC_MAX_QUESTIONS', '5'))
MODEL_PLUS = os.environ.get('MODEL_PLUS', 'claude-sonnet-4-6')
MODEL_PUBLIC = os.environ.get('MODEL_PUBLIC', MODEL_PLUS)

# Richieste di accesso e quesiti (demo: memoria + file locale, non persistente su Render)
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
REQUESTS = []
QUESITI = []

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

KB_TEXT = "\n\n---\n\n".join([f"TEMA: {k}\n{v}" for k, v in KB_RAW.items()])
KB_TEXT_PUBLIC = "\n\n---\n\n".join(
    [f"ID ARTICOLO: {k.replace('_', '-')}\n{v}" for k, v in KB_RAW.items()]
)

# ── Versione completa (utenti registrati) ──
SYSTEM_PROMPT = """Sei il P&W Advisor, l'assistente virtuale dell'Hub Previdenza e Welfare di DataHubs S.r.l.
Il tuo compito è supportare gli utenti registrati dell'Hub (dirigenti, aziende, sedi territoriali, consulenti e professionisti) su temi previdenziali, di welfare aziendale e di sanità integrativa.

Regole di comportamento:
1. Per domande tecniche su normativa, procedure, scadenze e importi: rispondi SOLO sulla base della documentazione certificata fornita. Indica sempre la fonte (numero circolare/messaggio e data).
2. Per domande di contesto generale — link a siti istituzionali (INPS, Agenzia delle Entrate, Ministero della Salute), definizioni di acronimi comuni, spiegazioni elementari di istituti noti — puoi rispondere con buon senso, senza fingere incertezza su nozioni di pubblico dominio.
3. Se una domanda tecnica non è coperta dalla base documentale, rispondi: "Questa informazione non è presente nella base documentale. Per una risposta accurata, contatta la sede territoriale competente."
4. Rispondi in testo semplice, senza markdown (no #, no **, no tabelle, no trattini decorativi).
   Usa obbligatoriamente una riga vuota tra ogni concetto o punto della risposta — non concatenare mai più frasi di seguito senza andare a capo.
   Ogni nuovo concetto, requisito o informazione deve iniziare su una nuova riga.
5. Adatta la lunghezza: breve per domande di sintesi, dettagliata per approfondimenti tecnici.
6. Mantieni il contesto della conversazione per domande di follow-up.
7. Rispondi sempre in italiano.
8. Concludi sempre la risposta con una riga vuota e poi la frase: "Sono a disposizione per qualsiasi altro approfondimento."

Riferimenti istituzionali sempre validi (non richiedono verifica documentale):
- Sito INPS: https://www.inps.it
- Agenzia delle Entrate: https://www.agenziaentrate.gov.it
- Ministero della Salute: https://www.salute.gov.it
- Portale servizi INPS per aziende e consulenti: https://www.inps.it — dalla homepage seleziona "Aziende, consulenti e professionisti" nella sezione Servizi online

Documentazione disponibile:

""" + KB_TEXT

# ── Versione pubblica (visitatori non registrati): ricerca intelligente ──
SYSTEM_PROMPT_PUBLIC = """Sei la ricerca intelligente pubblica dell'Hub Previdenza e Welfare di DataHubs S.r.l.
Chi ti scrive è un visitatore non registrato. Il tuo compito NON è dare consulenza: è indicare in quale articolo dell'Hub si trova la risposta, con una sintesi brevissima.

Regole:
1. Usa SOLO la documentazione fornita. Non aggiungere informazioni esterne.
2. Rispondi con al massimo 3 frasi brevi che sintetizzano cosa dice la documentazione sul punto chiesto. Se nella documentazione c'è un dato preciso (importo, percentuale, termine) puoi citarlo, con la fonte tra parentesi.
3. Non fornire procedure passo-passo, casistiche, valutazioni del caso concreto, consigli operativi né rimandi a sedi territoriali.
4. Dopo la sintesi, su righe separate, indica dove approfondire con uno o due riferimenti nel formato esatto:
   [[id-articolo#numero-paragrafo]]
   dove id-articolo è l'ID ARTICOLO indicato nella documentazione e numero-paragrafo è il numero del paragrafo "## N." pertinente. Esempio: [[cigo#5]]
5. Se la domanda non è coperta dalla documentazione, rispondi solo: "Non trovo questo argomento negli articoli dell'Hub." senza riferimenti.
6. Testo semplice, senza markdown, in italiano. Non aggiungere saluti o frasi di chiusura.

Documentazione disponibile:

""" + KB_TEXT_PUBLIC


def current_user():
    return session.get('user')


def save_record(kind, record):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, f'{kind}.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except OSError:
        pass
    print(f'[{kind}]', json.dumps(record, ensure_ascii=False), flush=True)


@app.route('/')
def index():
    user = current_user()
    remaining = None if user else max(0, PUBLIC_MAX_QUESTIONS - session.get('public_q', 0))
    return render_template(
        'index.html', kb=KB, user=user,
        is_admin=user in ADMINS,
        public_remaining=remaining,
        public_max=PUBLIC_MAX_QUESTIONS,
        login_error=request.args.get('err') == '1',
        open_login=request.args.get('accedi') == '1',
    )


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        if email in USERS and USERS[email] == password:
            session['user'] = email
            return redirect(url_for('index'))
        return redirect(url_for('index', accedi='1', err='1'))
    return redirect(url_for('index', accedi='1'))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/registrati', methods=['POST'])
def registrati():
    data = request.get_json(silent=True) or request.form
    record = {k: (data.get(k) or '').strip()[:300] for k in ('nome', 'cognome', 'ente', 'ruolo', 'email', 'note')}
    if not record['nome'] or not record['cognome'] or '@' not in record['email']:
        return jsonify({'ok': False, 'error': 'Compila nome, cognome ed email.'}), 400
    record['data'] = datetime.datetime.now().strftime('%d/%m/%Y %H:%M')
    REQUESTS.append(record)
    save_record('richieste_accesso', record)
    return jsonify({'ok': True})


@app.route('/quesito', methods=['POST'])
def quesito():
    if not current_user():
        return jsonify({'ok': False, 'error': 'Accesso richiesto.'}), 401
    data = request.get_json(silent=True) or request.form
    testo = (data.get('testo') or '').strip()[:3000]
    if len(testo) < 10:
        return jsonify({'ok': False, 'error': 'Descrivi il quesito in qualche riga.'}), 400
    record = {
        'utente': current_user(),
        'tema': (data.get('tema') or '').strip()[:100],
        'testo': testo,
        'data': datetime.datetime.now().strftime('%d/%m/%Y %H:%M'),
    }
    QUESITI.append(record)
    save_record('quesiti', record)
    return jsonify({'ok': True})


@app.route('/admin/richieste')
def admin_richieste():
    if current_user() not in ADMINS:
        return redirect(url_for('index', accedi='1'))
    return jsonify({'richieste_accesso': REQUESTS, 'quesiti': QUESITI})


@app.route('/ask', methods=['POST'])
def ask():
    user = current_user()
    data = request.get_json(silent=True) or {}
    messages = data.get('messages', [])

    if not messages:
        return jsonify({'error': 'Nessun messaggio'}), 400

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        return jsonify({'error': 'API key non configurata'}), 500

    if user:
        system, model, max_tokens = SYSTEM_PROMPT, MODEL_PLUS, 1024
        convo = messages[-20:]
    else:
        used = session.get('public_q', 0)
        if used >= PUBLIC_MAX_QUESTIONS:
            return jsonify({'error': 'limite', 'remaining': 0}), 429
        session['public_q'] = used + 1
        system, model, max_tokens = SYSTEM_PROMPT_PUBLIC, MODEL_PUBLIC, 350
        # Versione pubblica: nessuna memoria di conversazione, solo l'ultima domanda
        last = next((m for m in reversed(messages) if m.get('role') == 'user'), None)
        if not last:
            return jsonify({'error': 'Nessun messaggio'}), 400
        convo = [{'role': 'user', 'content': str(last.get('content', ''))[:1000]}]

    client = anthropic.Anthropic(api_key=api_key)

    def generate():
        with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=convo
        ) as stream:
            for text in stream.text_stream:
                yield f"data: {text}\n\n"
        yield "data: [DONE]\n\n"

    headers = {'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'X-Tier': 'plus' if user else 'public'}
    if not user:
        headers['X-Remaining'] = str(max(0, PUBLIC_MAX_QUESTIONS - session['public_q']))
    return Response(stream_with_context(generate()), mimetype='text/event-stream', headers=headers)


if __name__ == '__main__':
    app.run(debug=False)
