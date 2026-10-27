"""Integração do Nexo com a Pluggy (contas Nubank via Meu Pluggy).

Fluxo:
1. Client ID + Client Secret -> POST /auth -> apiKey (vale 2 horas; é gerada a
   cada sincronização, nunca fica salva).
2. Item ID (conexão do Meu Pluggy) -> GET /accounts -> conta corrente e cartão.
3. GET /transactions de cada conta e GET /bills do cartão.
4. As transações são gravadas no banco local:
   - conta corrente -> tabela transacoes (Despesa; Receita é opcional);
   - cartão de crédito -> compras_cartao, uma linha por lançamento, no mês da
     fatura correspondente (a fatura continua sendo marcada como paga no app).

A rede roda numa thread separada e o banco só é alterado na thread principal
(o sqlite3 do app não pode ser usado em outra thread).

As credenciais ficam somente no banco local do aparelho (pasta privada do app),
nunca no código-fonte nem no GitHub.
"""
import json
import ssl
import threading
import urllib.error
import urllib.parse
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timedelta

API = 'https://api.pluggy.ai'
ORIGEM = 'pluggy'
INTERVALO_AUTO_HORAS = 6       # sincronização automática ao abrir o app
MARGEM_DIAS = 15               # reprocessa os últimos dias (pendentes viram postadas)

# Categorias da Pluggy (prefixo do categoryId) -> categorias do Nexo.
MAPA_CATEGORIAS = [
    ('10', 'Mercado'),
    ('08030000', 'Pets'),
    ('08050000', 'Filhos'), ('07020003', 'Filhos'), ('07020004', 'Filhos'),
    ('1905', 'Carro'), ('19030000', 'Carro'), ('02030002', 'Carro'), ('200400000', 'Carro'),
    ('21', 'Lazer'), ('0704', 'Lazer'), ('09010000', 'Lazer'),
]
# Movimentos que não são gasto real: entre contas próprias, aplicações/resgates
# (caixinhas) e pagamento da fatura do cartão (já contado pela fatura).
IGNORAR_CATEGORIAS = ('04', '03', '05100000')
IGNORAR_TEXTOS = ('pagamento de fatura', 'pagamento da fatura', 'pagamento fatura',
                  'aplicação rdb', 'aplicacao rdb', 'resgate rdb', 'dinheiro guardado',
                  'dinheiro resgatado', 'caixinha')


class PluggyError(Exception):
    pass


# ---------------------------------------------------------------- HTTP
def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _request(method, path, api_key=None, body=None, params=None, timeout=40):
    url = API + path
    if params:
        url += '?' + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Accept', 'application/json')
    if data is not None:
        req.add_header('Content-Type', 'application/json')
    if api_key:
        req.add_header('X-API-KEY', api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode('utf-8')).get('message', '')
        except Exception:
            msg = ''
        if e.code in (401, 403):
            raise PluggyError('Acesso negado pela Pluggy (confira Client ID, Client Secret e Item ID). ' + msg)
        if e.code == 404:
            raise PluggyError('Não encontrado na Pluggy (confira o Item ID). ' + msg)
        raise PluggyError(f'Erro {e.code} da Pluggy. {msg}')
    except urllib.error.URLError as e:
        raise PluggyError('Sem conexão com a Pluggy. Verifique a internet. (' + str(e.reason) + ')')


def authenticate(client_id, client_secret):
    res = _request('POST', '/auth', body={'clientId': client_id.strip(), 'clientSecret': client_secret.strip()})
    key = res.get('apiKey')
    if not key:
        raise PluggyError('A Pluggy não devolveu a apiKey.')
    return key


def _paged(path, api_key, params):
    """Percorre páginas numeradas (ex.: /bills)."""
    page, results = 1, []
    while True:
        res = _request('GET', path, api_key, params=dict(params, page=page))
        results.extend(res.get('results', []))
        if page >= int(res.get('totalPages') or 1):
            return results
        page += 1


def list_transactions(api_key, account_id, date_from, date_to):
    """GET /v2/transactions com paginação por cursor (o /transactions antigo foi desativado)."""
    params = {'accountId': account_id, 'dateFrom': date_from, 'dateTo': date_to}
    results = []
    for _ in range(200):  # limite de segurança
        res = _request('GET', '/v2/transactions', api_key, params=params)
        results.extend(res.get('results', []))
        nxt = res.get('next')
        if not nxt:
            return results
        query = urllib.parse.parse_qs(nxt.lstrip('?'))
        after = (query.get('after') or [None])[0]
        if not after:
            return results
        params = {'accountId': account_id, 'dateFrom': date_from, 'dateTo': date_to, 'after': after}
    return results


def fetch_snapshot(cfg, date_from, date_to):
    """Somente rede (pode rodar fora da thread principal)."""
    key = authenticate(cfg['client_id'], cfg['client_secret'])
    item = _request('GET', '/items/' + cfg['item_id'].strip(), key)
    accounts = _request('GET', '/accounts', key, params={'itemId': cfg['item_id'].strip()}).get('results', [])
    snap = {'item': item, 'accounts': []}
    for acc in accounts:
        entry = {'account': acc, 'bills': []}
        to = date_to
        if acc.get('type') == 'CREDIT':
            # Parcelas futuras do cartão vêm com datas à frente.
            to = (date.fromisoformat(date_to) + timedelta(days=400)).isoformat()
            try:
                entry['bills'] = _paged('/bills', key, {'accountId': acc['id'], 'pageSize': 100})
            except PluggyError:
                entry['bills'] = []
        entry['transactions'] = list_transactions(key, acc['id'], date_from, to)
        entry['from'], entry['to'] = date_from, to
        snap['accounts'].append(entry)
    return snap


# ---------------------------------------------------------------- banco
def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS integracao_pluggy (
        id INTEGER PRIMARY KEY CHECK(id=1), client_id TEXT DEFAULT '', client_secret TEXT DEFAULT '',
        item_id TEXT DEFAULT '', importar_receitas INTEGER DEFAULT 0, inicio TEXT,
        ultima_sync TEXT, ultimo_resultado TEXT, ativo INTEGER DEFAULT 1)''')
    db.execute('INSERT OR IGNORE INTO integracao_pluggy(id) VALUES (1)')
    db.execute('CREATE TABLE IF NOT EXISTS pluggy_contas (account_id TEXT PRIMARY KEY, tipo TEXT, nome TEXT, cartao_id INTEGER, saldo REAL, atualizado TEXT)')
    for table in ('transacoes', 'compras_cartao'):
        cols = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        if 'externo_id' not in cols:
            db.execute(f'ALTER TABLE {table} ADD COLUMN externo_id TEXT')
        if 'origem' not in cols:
            db.execute(f'ALTER TABLE {table} ADD COLUMN origem TEXT')
        if 'conta_externa' not in cols:
            db.execute(f'ALTER TABLE {table} ADD COLUMN conta_externa TEXT')
    db.execute('CREATE INDEX IF NOT EXISTS idx_transacoes_externo ON transacoes(externo_id)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_compras_externo ON compras_cartao(externo_id)')
    db.commit()


def get_config(db):
    row = db.execute('SELECT client_id,client_secret,item_id,importar_receitas,inicio,ultima_sync,ultimo_resultado,ativo FROM integracao_pluggy WHERE id=1').fetchone()
    keys = ('client_id', 'client_secret', 'item_id', 'importar_receitas', 'inicio', 'ultima_sync', 'ultimo_resultado', 'ativo')
    cfg = dict(zip(keys, row))
    cfg['configurado'] = bool(cfg['client_id'] and cfg['client_secret'] and cfg['item_id'])
    return cfg


def save_config(db, client_id, client_secret, item_id, importar_receitas, inicio, ativo=True):
    inicio = date.fromisoformat(inicio.strip()).isoformat()
    db.execute('UPDATE integracao_pluggy SET client_id=?,client_secret=?,item_id=?,importar_receitas=?,inicio=?,ativo=? WHERE id=1',
               (client_id.strip(), client_secret.strip(), item_id.strip(), int(bool(importar_receitas)), inicio, int(bool(ativo))))
    db.commit()


def default_start():
    t = date.today()
    y, m = t.year, t.month - 2
    if m <= 0:
        y, m = y - 1, m + 12
    return date(y, m, 1).isoformat()


# ---------------------------------------------------------------- regras
def local_date(iso):
    """Data no fuso do Brasil. Datas 'cheias' (00:00Z) são mantidas como estão."""
    if not iso:
        return date.today().isoformat()
    if 'T' not in iso or iso[11:19] == '00:00:00':
        return iso[:10]
    dt = datetime.fromisoformat(iso.replace('Z', '+00:00')) - timedelta(hours=3)
    return dt.date().isoformat()


def should_ignore(tx):
    cid = str(tx.get('categoryId') or '')
    if cid and any(cid.startswith(p) for p in IGNORAR_CATEGORIAS):
        return True
    text = (tx.get('description') or '').lower()
    if any(t in text for t in IGNORAR_TEXTOS):
        return True
    return tx.get('operationType') in ('PAGAMENTO_FATURA',)


def category_for(db, tx):
    # 1º: regra criada pelo usuário ("aplicar a todas com esta descrição").
    import movimentacoes
    rule = movimentacoes.rule_for(db, tx.get('description') or '')
    if rule:
        return rule
    cid = str(tx.get('categoryId') or '')
    for prefix, name in MAPA_CATEGORIAS:
        if cid.startswith(prefix):
            break
    else:
        name = None
    if name is None:
        top = cid[:2] + '000000' if len(cid) >= 2 else ''
        name = TOP_CATEGORIAS.get(top, 'Outros')
    db.execute("INSERT OR IGNORE INTO categorias(nome,grupo) VALUES (?,'Variavel')", (name,))
    return name


TOP_CATEGORIAS = {
    '01000000': 'Renda', '02000000': 'Empréstimos', '05000000': 'Transferências',
    '06000000': 'Obrigações legais', '07000000': 'Serviços', '08000000': 'Compras',
    '09000000': 'Serviços digitais', '10000000': 'Mercado', '11000000': 'Alimentação',
    '12000000': 'Viagens', '13000000': 'Doações', '14000000': 'Apostas', '15000000': 'Impostos',
    '16000000': 'Taxas bancárias', '17000000': 'Moradia', '18000000': 'Saúde',
    '19000000': 'Transporte', '20000000': 'Seguros', '21000000': 'Lazer', '99999999': 'Outros',
}


def _add_month(ym, n):
    y, m = map(int, ym.split('-'))
    z = y * 12 + m - 1 + n
    return f'{z//12:04d}-{z%12+1:02d}'


def invoice_month(tx_date, close_day, due_day):
    """Mês de vencimento da fatura de uma compra feita em tx_date (AAAA-MM-DD)."""
    ym = tx_date[:7]
    day = int(tx_date[8:10])
    # Compra no dia do fechamento ou depois cai na fatura seguinte.
    closing_month = ym if day < close_day else _add_month(ym, 1)
    return closing_month if due_day > close_day else _add_month(closing_month, 1)


def infer_close_day(transactions, bill_month, due_day, informed=None):
    """Dia de fechamento do cartão. A Pluggy nem sempre informa (no Nubank vem
    vazio); então é deduzido das compras já faturadas: escolhe o dia que coloca
    o maior número delas na fatura em que o banco realmente as cobrou."""
    pairs = []
    for t in transactions:
        bill = (t.get('creditCardMetadata') or {}).get('billId')
        if bill in bill_month and float(t.get('amount') or 0) > 0:
            pairs.append((local_date(t.get('date')), bill_month[bill]))
    if len(pairs) >= 5:
        scores = {c: sum(invoice_month(d, c, due_day) == m for d, m in pairs) for c in range(1, 29)}
        best = max(scores.values())
        # Empate: prefere o dia mais próximo de 7 dias antes do vencimento.
        target = (due_day - 7 - 1) % 28 + 1
        return min((c for c, v in scores.items() if v == best), key=lambda c: abs(c - target))
    if informed:
        return informed
    return (due_day - 7 - 1) % 28 + 1


def _card_for_account(db, acc):
    row = db.execute('SELECT cartao_id FROM pluggy_contas WHERE account_id=?', (acc['id'],)).fetchone()
    credit = acc.get('creditData') or {}
    due = credit.get('balanceDueDate')
    due_day = int(due[8:10]) if due else 10
    name = 'Nubank' + (' •' + str(acc.get('number'))[-4:] if acc.get('number') else '')
    if row and row[0] and db.execute('SELECT 1 FROM cartoes WHERE id=?', (row[0],)).fetchone():
        db.execute('UPDATE cartoes SET vencimento=? WHERE id=?', (due_day, row[0]))
        return row[0]
    cur = db.execute('INSERT INTO cartoes(nome,vencimento) VALUES (?,?)', (name, due_day))
    return cur.lastrowid


def _description(tx):
    text = (tx.get('description') or 'Sem descrição').strip()
    meta = tx.get('creditCardMetadata') or {}
    n, total = meta.get('installmentNumber'), meta.get('totalInstallments')
    if n and total and int(total) > 1:
        text += f' ({n}/{total})'
    return text[:80]


def _reconcile(db, table, account_id, items, date_from, date_to, date_col, protect=None):
    """Grava/atualiza 'items' (lista de dicts com 'externo_id' e 'valores') e
    remove importações antigas da mesma conta que sumiram da Pluggy."""
    existing = {r[0]: r[1] for r in db.execute(
        f'SELECT externo_id,id FROM {table} WHERE origem=? AND conta_externa=? AND {date_col} BETWEEN ? AND ?',
        (ORIGEM, account_id, date_from, date_to))}
    seen, created, updated = set(), 0, 0
    for it in items:
        ext = it['externo_id']
        seen.add(ext)
        vals = it['valores']
        cols = list(vals.keys())
        found = db.execute(f'SELECT COALESCE(categoria_manual,0) FROM {table} WHERE externo_id=?', (ext,)).fetchone()
        if ext in existing or found:
            # Categoria escolhida pelo usuário nunca é sobrescrita pela sincronização.
            upd = [c for c in cols if not (c == 'categoria' and found and found[0])]
            db.execute(f'UPDATE {table} SET ' + ','.join(c + '=?' for c in upd) + ' WHERE externo_id=?',
                       [vals[c] for c in upd] + [ext])
            updated += 1
            continue
        # A Pluggy pode recriar a transação com outro id: casa por data+valor+descrição.
        match = db.execute(
            f'SELECT id,externo_id FROM {table} WHERE origem=? AND conta_externa=? AND {date_col}=? AND ABS({it["col_valor"]}-?)<0.005 AND {it["col_desc"]}=?',
            (ORIGEM, account_id, vals[date_col], vals[it['col_valor']], vals[it['col_desc']])).fetchall()
        match = [m for m in match if m[1] not in seen and not any(x['externo_id'] == m[1] for x in items)]
        if match:
            db.execute(f'UPDATE {table} SET externo_id=? WHERE id=?', (ext, match[0][0]))
            existing.pop(match[0][1], None)
            updated += 1
            continue
        allcols = cols + ['externo_id', 'origem', 'conta_externa']
        db.execute(f'INSERT INTO {table}(' + ','.join(allcols) + ') VALUES (' + ','.join('?' * len(allcols)) + ')',
                   [vals[c] for c in cols] + [ext, ORIGEM, account_id])
        created += 1
    removed = 0
    for ext, rowid in existing.items():
        if ext in seen:
            continue
        if protect and protect(rowid):
            continue
        db.execute(f'DELETE FROM {table} WHERE id=?', (rowid,))
        removed += 1
    return created, updated, removed


def apply_snapshot(db, snap, cfg):
    """Grava o resultado da sincronização. Roda na thread principal."""
    summary = {'conta': [0, 0, 0], 'cartao': [0, 0, 0], 'ignoradas': 0, 'contas': []}
    for entry in snap['accounts']:
        acc = entry['account']
        kind = acc.get('type')
        db.execute('INSERT INTO pluggy_contas(account_id,tipo,nome,saldo,atualizado) VALUES (?,?,?,?,?) '
                   'ON CONFLICT(account_id) DO UPDATE SET tipo=excluded.tipo,nome=excluded.nome,saldo=excluded.saldo,atualizado=excluded.atualizado',
                   (acc['id'], kind, acc.get('name'), acc.get('balance'), datetime.now().isoformat(timespec='seconds')))
        summary['contas'].append((acc.get('name') or kind, kind, acc.get('balance')))
        if kind == 'BANK':
            items = []
            for tx in entry['transactions']:
                if tx.get('status') == 'PENDING' or should_ignore(tx):
                    summary['ignoradas'] += 1
                    continue
                is_income = tx.get('type') == 'CREDIT'
                if is_income and not cfg['importar_receitas']:
                    summary['ignoradas'] += 1
                    continue
                items.append({'externo_id': tx.get('providerId') or tx['id'], 'col_valor': 'valor', 'col_desc': 'descricao',
                              'valores': {'descricao': (tx.get('description') or 'Nubank')[:80],
                                          'categoria': 'Receita' if is_income else category_for(db, tx),
                                          'tipo': 'Receita' if is_income else 'Despesa',
                                          'valor': round(abs(float(tx.get('amount') or 0)), 2),
                                          'data': local_date(tx.get('date')),
                                          'pagamento': 'Nubank'}})
            res = _reconcile(db, 'transacoes', acc['id'], items, entry['from'], entry['to'], 'data')
            summary['conta'] = [a + b for a, b in zip(summary['conta'], res)]
        elif kind == 'CREDIT':
            card_id = _card_for_account(db, acc)
            db.execute('UPDATE pluggy_contas SET cartao_id=? WHERE account_id=?', (card_id, acc['id']))
            credit = acc.get('creditData') or {}
            close = credit.get('balanceCloseDate')
            due = credit.get('balanceDueDate')
            due_day = int(due[8:10]) if due else 10
            bill_month = {b['id']: b['dueDate'][:7] for b in entry['bills'] if b.get('dueDate')}
            close_day = infer_close_day(entry['transactions'], bill_month, due_day,
                                        int(close[8:10]) if close else None)
            items = []
            for tx in entry['transactions']:
                amount = float(tx.get('amount') or 0)
                text = (tx.get('description') or '').lower()
                is_payment = amount < 0 and (tx.get('operationType') in ('PAGAMENTO', 'PAGAMENTO_FATURA')
                                             or 'pagamento' in text)
                if amount == 0 or is_payment or should_ignore(tx):
                    summary['ignoradas'] += 1
                    continue
                day = local_date(tx.get('date'))
                meta = tx.get('creditCardMetadata') or {}
                month = bill_month.get(meta.get('billId')) or invoice_month(day, close_day, due_day)
                items.append({'externo_id': tx.get('providerId') or tx['id'], 'col_valor': 'valor_total', 'col_desc': 'descricao',
                              'valores': {'cartao_id': card_id, 'categoria': category_for(db, tx),
                                          'valor_total': round(amount, 2), 'parcelas': 1,
                                          'primeiro_mes': month, 'data_compra': day,
                                          'descricao': _description(tx)}})

            def paid(rowid, cid=card_id):
                # Não apaga lançamentos de faturas já marcadas como pagas.
                m = db.execute('SELECT primeiro_mes FROM compras_cartao WHERE id=?', (rowid,)).fetchone()
                return bool(m and db.execute('SELECT 1 FROM faturas_pagas WHERE cartao_id=? AND mes=?', (cid, m[0])).fetchone())
            res = _reconcile(db, 'compras_cartao', acc['id'], items, entry['from'], entry['to'], 'data_compra', protect=paid)
            summary['cartao'] = [a + b for a, b in zip(summary['cartao'], res)]
    now = datetime.now().isoformat(timespec='seconds')
    text = (f"Conta: {summary['conta'][0]} novas, {summary['conta'][1]} atualizadas, {summary['conta'][2]} removidas. "
            f"Cartão: {summary['cartao'][0]} novas, {summary['cartao'][1]} atualizadas, {summary['cartao'][2]} removidas. "
            f"Ignoradas: {summary['ignoradas']}.")
    status = (snap.get('item') or {}).get('status')
    if status and status != 'UPDATED':
        text += f' Status da conexão na Pluggy: {status}.'
    db.execute('UPDATE integracao_pluggy SET ultima_sync=?,ultimo_resultado=? WHERE id=1', (now, text))
    db.commit()
    summary['texto'] = text
    return summary


# ---------------------------------------------------------------- sincronização
_running = threading.Lock()


def sync_window(cfg):
    start = cfg.get('inicio') or default_start()
    if cfg.get('ultima_sync'):
        last = date.fromisoformat(cfg['ultima_sync'][:10]) - timedelta(days=MARGEM_DIAS)
        start = max(start, last.isoformat())
    return start, date.today().isoformat()


def sync(app, on_done=None, full=False):
    """Busca na Pluggy em segundo plano e grava no banco na thread principal."""
    from kivy.clock import Clock
    cfg = get_config(app.db)
    if not cfg['configurado']:
        if on_done:
            on_done(False, 'Preencha Client ID, Client Secret e Item ID.')
        return False
    if not _running.acquire(blocking=False):
        if on_done:
            on_done(False, 'Já existe uma sincronização em andamento.')
        return False
    date_from, date_to = (cfg.get('inicio') or default_start(), date.today().isoformat()) if full else sync_window(cfg)

    def worker():
        try:
            snap = fetch_snapshot(cfg, date_from, date_to)
            error = None
        except PluggyError as e:
            snap, error = None, str(e)
        except Exception as e:  # erro inesperado: não derruba o app
            snap, error = None, 'Erro inesperado: ' + repr(e)

        def finish(dt):
            try:
                if error:
                    app.db.execute('UPDATE integracao_pluggy SET ultimo_resultado=? WHERE id=1', ('Falha: ' + error,))
                    app.db.commit()
                    ok, msg = False, error
                else:
                    ok, msg = True, apply_snapshot(app.db, snap, cfg)['texto']
            except Exception as e:
                ok, msg = False, 'Erro ao gravar: ' + repr(e)
            finally:
                _running.release()
            if on_done:
                on_done(ok, msg)
        Clock.schedule_once(finish, 0)
    threading.Thread(target=worker, daemon=True).start()
    return True


def auto_sync(app):
    """Chamada ao abrir o app: sincroniza se configurado e passou o intervalo."""
    cfg = get_config(app.db)
    if not cfg['configurado'] or not cfg['ativo']:
        return
    if cfg['ultima_sync']:
        last = datetime.fromisoformat(cfg['ultima_sync'])
        if datetime.now() - last < timedelta(hours=INTERVALO_AUTO_HORAS):
            return
    sync(app, on_done=lambda ok, msg: app.refresh() if ok else None)


# ---------------------------------------------------------------- tela
def show(app):
    from kivy.metrics import dp
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.checkbox import CheckBox
    from main_nexo import modern_button
    body = app.page_body('INTEGRAÇÃO NUBANK')
    cfg = get_config(app.db)
    body.add_widget(app.label('Importa automaticamente a conta e o cartão Nubank conectados no Meu Pluggy.', 13, app.MUTED, 50))

    status = app.label(cfg['ultimo_resultado'] or 'Ainda não sincronizado.', 13,
                       app.RED if (cfg['ultimo_resultado'] or '').startswith('Falha') else app.GREEN, 70)
    last = app.label('Última sincronização: ' + (cfg['ultima_sync'].replace('T', ' ') if cfg['ultima_sync'] else '—'), 12, app.MUTED, 30)

    form = BoxLayout(orientation='vertical', spacing=dp(8), size_hint_y=None)
    form.bind(minimum_height=form.setter('height'))
    cid = app.input_field('Client ID', cfg['client_id'])
    secret = app.input_field('Client Secret', cfg['client_secret'])
    secret.password = True
    item = app.input_field('Item ID (conexão MeuPluggy)', cfg['item_id'])
    start = app.input_field('AAAA-MM-DD', cfg['inicio'] or default_start())
    line = BoxLayout(size_hint_y=None, height=dp(44))
    income = CheckBox(active=bool(cfg['importar_receitas']), size_hint_x=None, width=dp(44))
    line.add_widget(income)
    line.add_widget(app.label('Importar entradas como receita (desligue se o salário já está em Ajustes > Salários)', 12, app.WHITE, 44))
    line2 = BoxLayout(size_hint_y=None, height=dp(40))
    auto = CheckBox(active=bool(cfg['ativo']), size_hint_x=None, width=dp(44))
    line2.add_widget(auto)
    line2.add_widget(app.label(f'Sincronizar sozinho ao abrir o app (a cada {INTERVALO_AUTO_HORAS}h)', 12, app.WHITE, 40))
    err = app.label('', 12, app.RED, 26)
    for w in [app.label('Client ID', 12, app.MUTED, 22), cid, app.label('Client Secret', 12, app.MUTED, 22), secret,
              app.label('Item ID', 12, app.MUTED, 22), item, app.label('Importar a partir de', 12, app.MUTED, 22), start,
              line, line2, err]:
        form.add_widget(w)

    def save(*_):
        try:
            save_config(app.db, cid.text, secret.text, item.text, income.active, start.text, auto.active)
        except ValueError:
            err.text = 'Data inválida. Use AAAA-MM-DD.'
            return False
        err.text = ''
        return True

    def done(ok, msg):
        status.text = msg
        status.color = app.GREEN if ok else app.RED
        new = get_config(app.db)
        last.text = 'Última sincronização: ' + (new['ultima_sync'].replace('T', ' ') if new['ultima_sync'] else '—')
        sync_btn.disabled = False
        sync_btn.text = 'Sincronizar agora'

    def do_sync(*_):
        if not save():
            return
        sync_btn.disabled = True
        sync_btn.text = 'Sincronizando...'
        status.text = 'Buscando dados na Pluggy...'
        status.color = app.MUTED
        if not sync(app, on_done=done, full=True):
            sync_btn.disabled = False
            sync_btn.text = 'Sincronizar agora'

    def only_save(*_):
        if save():
            status.text = 'Configuração salva.'
            status.color = app.GREEN

    form.add_widget(app.button('Salvar configuração', only_save))
    sync_btn = modern_button('Sincronizar agora', do_sync, primary=True)
    form.add_widget(sync_btn)
    panel = app.panel(form)
    form.bind(height=lambda w, v: setattr(panel, 'height', v + dp(26)))
    body.add_widget(panel)
    body.add_widget(last)
    body.add_widget(status)

    contas = app.db.execute('SELECT nome,tipo,saldo,atualizado FROM pluggy_contas ORDER BY tipo').fetchall()
    if contas:
        body.add_widget(app.label('Contas conectadas', 17, app.WHITE, 40, True))
        for nome, tipo, saldo, at in contas:
            kind = 'Conta corrente' if tipo == 'BANK' else 'Cartão de crédito'
            extra = ('Saldo: ' if tipo == 'BANK' else 'Fatura aberta: ') + app.money(saldo or 0)
            body.add_widget(app.label(f'{nome}  •  {kind}  •  {extra}', 13, app.MUTED, 40))
    body.add_widget(app.label(
        'Como funciona: gastos da conta entram como despesas; compras do cartão entram na fatura do mês '
        'certo em Cartão de Crédito. Transferências entre suas contas, caixinhas e o pagamento da fatura '
        'são ignorados para não contar duas vezes. O Meu Pluggy atualiza os dados do banco 1 vez por dia.',
        12, app.MUTED, 120))
    body.add_widget(app.label('As credenciais ficam salvas só neste aparelho.', 12, app.MUTED, 34))
