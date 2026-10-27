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
IGNORAR_TEXTOS = ('pagamento de fatura', 'pagamento da fatura', 'pagamento fatura', 'pag fatura',
                  'pagto fatura', 'pagamento cartao', 'pagamento cartão', 'pagto cartao', 'pagamento de cartao',
                  'pagamento de cartão', 'deb autom fatura', 'débito automático fatura',
                  'aplicação rdb', 'aplicacao rdb', 'resgate rdb', 'dinheiro guardado',
                  'dinheiro resgatado', 'caixinha')


BANCOS = ['Automático', 'Nubank', 'Santander', 'Itaú', 'Bradesco', 'Banco do Brasil', 'Caixa', 'Inter', 'C6 Bank', 'Outro banco']
ICONES_BANCO = {'Nubank': 'banco_nubank.png', 'Santander': 'banco_santander.png'}
_DETECTA = [('nubank', 'Nubank'), ('nu pagamentos', 'Nubank'), ('nu financeira', 'Nubank'), ('santander', 'Santander'),
            ('itau', 'Itaú'), ('itaú', 'Itaú'), ('bradesco', 'Bradesco'), ('banco do brasil', 'Banco do Brasil'),
            ('caixa', 'Caixa'), ('inter', 'Inter'), ('c6', 'C6 Bank')]


def detect_bank(accounts, item=None):
    """Descobre o banco pelos nomes das contas e pelos dados da conexão."""
    text = ' '.join(f"{a.get('name') or ''} {a.get('marketingName') or ''} {a.get('owner') or ''} "
                    f"{(a.get('bankData') or {}).get('transferNumber') or ''}" for a in accounts).lower()
    try:
        text += ' ' + json.dumps(item or {}, ensure_ascii=False).lower()
    except Exception:
        pass
    # Código COMPE no número de transferência (ex.: "033/0001/..." = Santander).
    for code, name in (('260/', 'Nubank'), ('033/', 'Santander'), ('341/', 'Itaú'), ('237/', 'Bradesco'),
                       ('001/', 'Banco do Brasil'), ('104/', 'Caixa'), ('077/', 'Inter'), ('336/', 'C6 Bank')):
        if code in text:
            return name
    for key, name in _DETECTA:
        if key in text:
            return name
    return None


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
    """Somente rede (pode rodar fora da thread principal). Um item por banco conectado."""
    key = authenticate(cfg['client_id'], cfg['client_secret'])
    snap = {'items': []}
    for item_id, bank in cfg['itens']:
        item = _request('GET', '/items/' + item_id, key)
        accounts = _request('GET', '/accounts', key, params={'itemId': item_id}).get('results', [])
        entry_item = {'item_id': item_id, 'item': item, 'banco': bank, 'accounts': []}
        if not bank or bank == 'Automático':
            entry_item['banco'] = detect_bank(accounts, item) or 'Banco'
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
            entry_item['accounts'].append(entry)
        snap['items'].append(entry_item)
    return snap


# ---------------------------------------------------------------- banco
def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS integracao_pluggy (
        id INTEGER PRIMARY KEY CHECK(id=1), client_id TEXT DEFAULT '', client_secret TEXT DEFAULT '',
        item_id TEXT DEFAULT '', importar_receitas INTEGER DEFAULT 0, inicio TEXT,
        ultima_sync TEXT, ultimo_resultado TEXT, ativo INTEGER DEFAULT 1)''')
    db.execute('INSERT OR IGNORE INTO integracao_pluggy(id) VALUES (1)')
    db.execute('CREATE TABLE IF NOT EXISTS pluggy_contas (account_id TEXT PRIMARY KEY, tipo TEXT, nome TEXT, cartao_id INTEGER, saldo REAL, atualizado TEXT)')
    for col in ('item_id', 'banco'):
        if col not in {r[1] for r in db.execute('PRAGMA table_info(pluggy_contas)')}:
            db.execute(f'ALTER TABLE pluggy_contas ADD COLUMN {col} TEXT')
    # Uma linha por banco conectado no Meu Pluggy (Nubank, Santander...).
    db.execute("CREATE TABLE IF NOT EXISTS pluggy_itens (item_id TEXT PRIMARY KEY, banco TEXT DEFAULT 'Automático', criado TEXT)")
    try:
        cleanup_orphans(db)
    except Exception:
        pass
    old = db.execute('SELECT item_id FROM integracao_pluggy WHERE id=1').fetchone()
    if old and old[0] and not db.execute('SELECT 1 FROM pluggy_itens').fetchone():
        db.execute("INSERT OR IGNORE INTO pluggy_itens(item_id,banco,criado) VALUES (?,'Nubank',?)",
                   (old[0].strip(), datetime.now().isoformat(timespec='seconds')))
    for table in ('transacoes', 'compras_cartao'):
        cols = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        if 'externo_id' not in cols:
            db.execute(f'ALTER TABLE {table} ADD COLUMN externo_id TEXT')
        if 'origem' not in cols:
            db.execute(f'ALTER TABLE {table} ADD COLUMN origem TEXT')
        if 'conta_externa' not in cols:
            db.execute(f'ALTER TABLE {table} ADD COLUMN conta_externa TEXT')
    # Entradas da conta (salário e outras), guardadas mesmo quando não viram "Receita":
    # servem de referência de renda em "Quanto gastei?" quando o salário não foi cadastrado.
    db.execute('CREATE TABLE IF NOT EXISTS rendas_banco (externo_id TEXT PRIMARY KEY, data TEXT NOT NULL, valor REAL NOT NULL, descricao TEXT, banco TEXT, salario INTEGER DEFAULT 0)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_transacoes_externo ON transacoes(externo_id)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_compras_externo ON compras_cartao(externo_id)')
    db.commit()


def get_config(db):
    row = db.execute('SELECT client_id,client_secret,item_id,importar_receitas,inicio,ultima_sync,ultimo_resultado,ativo FROM integracao_pluggy WHERE id=1').fetchone()
    keys = ('client_id', 'client_secret', 'item_id', 'importar_receitas', 'inicio', 'ultima_sync', 'ultimo_resultado', 'ativo')
    cfg = dict(zip(keys, row))
    cfg['itens'] = [tuple(r) for r in db.execute('SELECT item_id,banco FROM pluggy_itens ORDER BY criado')]
    cfg['configurado'] = bool(cfg['client_id'] and cfg['client_secret'] and cfg['itens'])
    return cfg


def add_item(db, item_id, bank='Automático'):
    item_id = item_id.strip()
    if len(item_id) != 36 or item_id.count('-') != 4:
        raise ValueError('Item ID inválido')
    db.execute('INSERT INTO pluggy_itens(item_id,banco,criado) VALUES (?,?,?) ON CONFLICT(item_id) DO UPDATE SET banco=excluded.banco',
               (item_id, bank, datetime.now().isoformat(timespec='seconds')))
    db.commit()


def remove_item(db, item_id):
    """Remove a conexão e tudo o que veio dela: contas, cartões, compras,
    pagamentos e faturas. Lançamentos manuais (feitos no app) não são apagados."""
    accounts = db.execute('SELECT account_id,cartao_id,banco FROM pluggy_contas WHERE item_id=?', (item_id,)).fetchall()
    bank = next((b for _, _, b in accounts if b), None)
    for account_id, card_id, _ in accounts:
        db.execute("DELETE FROM transacoes WHERE origem='pluggy' AND conta_externa=?", (account_id,))
        db.execute("DELETE FROM compras_cartao WHERE origem='pluggy' AND conta_externa=?", (account_id,))
        if card_id:
            _remove_card_if_imported_only(db, card_id)
    if bank and not db.execute('SELECT 1 FROM pluggy_contas WHERE banco=? AND item_id<>?', (bank, item_id)).fetchone():
        db.execute('DELETE FROM rendas_banco WHERE banco=?', (bank,))
    db.execute('DELETE FROM pluggy_itens WHERE item_id=?', (item_id,))
    db.execute('DELETE FROM pluggy_contas WHERE item_id=?', (item_id,))
    cleanup_orphans(db)
    db.commit()


def _remove_card_if_imported_only(db, card_id):
    """Apaga o cartão se não tiver nada lançado à mão (compras manuais ou assinaturas)."""
    manual = db.execute("SELECT 1 FROM compras_cartao WHERE cartao_id=? AND (origem IS NULL OR origem<>'pluggy')", (card_id,)).fetchone()
    subs = db.execute('SELECT 1 FROM assinaturas_cartao WHERE cartao_id=?', (card_id,)).fetchone()
    db.execute("DELETE FROM compras_cartao WHERE cartao_id=? AND origem='pluggy'", (card_id,))
    db.execute("DELETE FROM pagamentos_cartao WHERE cartao_id=? AND origem='pluggy'", (card_id,))
    db.execute('DELETE FROM faturas_banco WHERE cartao_id=?', (card_id,))
    if not manual and not subs:
        db.execute('DELETE FROM faturas_pagas WHERE cartao_id=?', (card_id,))
        db.execute('DELETE FROM cartoes WHERE id=?', (card_id,))


def cleanup_orphans(db):
    """Limpa dados importados de conexões que já foram removidas (versões
    anteriores do app removiam a conexão mas deixavam o cartão)."""
    known = {r[0] for r in db.execute('SELECT account_id FROM pluggy_contas')}
    orphan_cards = set()
    for table in ('transacoes', 'compras_cartao'):
        try:
            rows = db.execute(f"SELECT DISTINCT conta_externa FROM {table} WHERE origem='pluggy'").fetchall()
        except Exception:
            continue
        for (acc,) in rows:
            if acc and acc not in known:
                if table == 'compras_cartao':
                    orphan_cards |= {r[0] for r in db.execute(
                        "SELECT DISTINCT cartao_id FROM compras_cartao WHERE origem='pluggy' AND conta_externa=?", (acc,))}
                db.execute(f"DELETE FROM {table} WHERE origem='pluggy' AND conta_externa=?", (acc,))
    linked = {r[0] for r in db.execute('SELECT cartao_id FROM pluggy_contas WHERE cartao_id IS NOT NULL')}
    for card_id in orphan_cards - linked:
        _remove_card_if_imported_only(db, card_id)


def set_item_bank(db, item_id, bank):
    db.execute('UPDATE pluggy_itens SET banco=? WHERE item_id=?', (bank, item_id))
    db.execute('UPDATE pluggy_contas SET banco=? WHERE item_id=?', (bank, item_id))
    # Renomeia o cartão criado pela integração (ex.: "Banco •1234" -> "Santander •1234").
    for card_id, number in db.execute('SELECT p.cartao_id,c.nome FROM pluggy_contas p JOIN cartoes c ON c.id=p.cartao_id WHERE p.item_id=?', (item_id,)).fetchall():
        suffix = number.split('•', 1)[1] if '•' in number else ''
        db.execute('UPDATE cartoes SET nome=? WHERE id=?', (bank + (' •' + suffix if suffix else ''), card_id))
    db.commit()


def save_config(db, client_id, client_secret, item_id, importar_receitas, inicio, ativo=True):
    """item_id: opcional (compatibilidade); se informado, é adicionado às conexões."""
    inicio = date.fromisoformat(inicio.strip()).isoformat()
    db.execute('UPDATE integracao_pluggy SET client_id=?,client_secret=?,importar_receitas=?,inicio=?,ativo=? WHERE id=1',
               (client_id.strip(), client_secret.strip(), int(bool(importar_receitas)), inicio, int(bool(ativo))))
    db.commit()
    if item_id and item_id.strip():
        add_item(db, item_id)


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


def _card_for_account(db, acc, bank='Nubank'):
    row = db.execute('SELECT cartao_id FROM pluggy_contas WHERE account_id=?', (acc['id'],)).fetchone()
    credit = acc.get('creditData') or {}
    due = credit.get('balanceDueDate')
    due_day = int(due[8:10]) if due else 10
    name = bank + (' •' + str(acc.get('number'))[-4:] if acc.get('number') else '')
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
    if 'items' not in snap:  # formato antigo (um item só)
        snap = {'items': [{'item_id': cfg.get('item_id') or '', 'item': snap.get('item'), 'banco': 'Nubank',
                           'accounts': snap['accounts']}]}
    statuses = []
    for it in snap['items']:
        bank = it.get('banco') or 'Banco'
        if it.get('item_id'):
            db.execute("UPDATE pluggy_itens SET banco=? WHERE item_id=? AND banco='Automático'", (bank, it['item_id']))
        st = (it.get('item') or {}).get('status')
        if st and st != 'UPDATED':
            statuses.append(f'{bank}: {st}')
        _apply_accounts(db, it['accounts'], cfg, summary, bank, it.get('item_id'))
    import movimentacoes
    movimentacoes.apply_fixed_rules(db)  # transações ligadas a custos fixos ("aplicar às próximas")
    return _finish(db, summary, statuses)


def _apply_accounts(db, accounts, cfg, summary, bank, item_id):
    for entry in accounts:
        acc = entry['account']
        kind = acc.get('type')
        db.execute('INSERT INTO pluggy_contas(account_id,tipo,nome,saldo,atualizado,item_id,banco) VALUES (?,?,?,?,?,?,?) '
                   'ON CONFLICT(account_id) DO UPDATE SET tipo=excluded.tipo,nome=excluded.nome,saldo=excluded.saldo,'
                   'atualizado=excluded.atualizado,item_id=excluded.item_id,banco=excluded.banco',
                   (acc['id'], kind, acc.get('name'), acc.get('balance'), datetime.now().isoformat(timespec='seconds'),
                    item_id, bank))
        summary['contas'].append((acc.get('name') or kind, kind, acc.get('balance')))
        if kind == 'BANK':
            items = []
            for tx in entry['transactions']:
                if tx.get('status') == 'PENDING' or should_ignore(tx):
                    summary['ignoradas'] += 1
                    continue
                is_income = tx.get('type') == 'CREDIT'
                if is_income:
                    _store_income(db, tx, bank)
                if is_income and not cfg['importar_receitas']:
                    summary['ignoradas'] += 1
                    continue
                items.append({'externo_id': tx.get('providerId') or tx['id'], 'col_valor': 'valor', 'col_desc': 'descricao',
                              'valores': {'descricao': (tx.get('description') or 'Nubank')[:80],
                                          'categoria': 'Receita' if is_income else category_for(db, tx),
                                          'tipo': 'Receita' if is_income else 'Despesa',
                                          'valor': round(abs(float(tx.get('amount') or 0)), 2),
                                          'data': local_date(tx.get('date')),
                                          'pagamento': bank}})
            res = _reconcile(db, 'transacoes', acc['id'], items, entry['from'], entry['to'], 'data')
            summary['conta'] = [a + b for a, b in zip(summary['conta'], res)]
        elif kind == 'CREDIT':
            card_id = _card_for_account(db, acc, bank)
            db.execute('UPDATE pluggy_contas SET cartao_id=? WHERE account_id=?', (card_id, acc['id']))
            credit = acc.get('creditData') or {}
            close = credit.get('balanceCloseDate')
            due = credit.get('balanceDueDate')
            due_day = int(due[8:10]) if due else 10
            bill_month = {b['id']: b['dueDate'][:7] for b in entry['bills'] if b.get('dueDate')}
            close_day = infer_close_day(entry['transactions'], bill_month, due_day,
                                        int(close[8:10]) if close else None)
            db.execute('UPDATE cartoes SET fechamento=? WHERE id=?', (close_day, card_id))
            # Valor oficial de cada fatura informado pelo banco.
            for b in entry['bills']:
                if b.get('dueDate') and b.get('totalAmount') is not None:
                    db.execute('INSERT INTO faturas_banco(cartao_id,mes,valor) VALUES (?,?,?) '
                               'ON CONFLICT(cartao_id,mes) DO UPDATE SET valor=excluded.valor',
                               (card_id, b['dueDate'][:7], round(float(b['totalAmount']), 2)))
            from cartao_credito import payment_month
            payments = []
            items = []
            for tx in entry['transactions']:
                amount = float(tx.get('amount') or 0)
                text = (tx.get('description') or '').lower()
                is_payment = amount < 0 and (tx.get('operationType') in ('PAGAMENTO', 'PAGAMENTO_FATURA')
                                             or 'pagamento' in text)
                if is_payment:
                    # Pagamento de fatura (inclusive antecipado): abate o valor a pagar.
                    pday = local_date(tx.get('date'))
                    payments.append((tx.get('providerId') or tx['id'], round(-amount, 2), pday,
                                     payment_month(pday, due_day), (tx.get('description') or '')[:60]))
                    continue
                if amount == 0 or should_ignore(tx):
                    summary['ignoradas'] += 1
                    continue
                day = local_date(tx.get('date'))
                meta = tx.get('creditCardMetadata') or {}
                month = bill_month.get(meta.get('billId')) or invoice_month(day, close_day, due_day)
                items.append({'externo_id': tx.get('providerId') or tx['id'], 'col_valor': 'valor_total', 'col_desc': 'descricao',
                              'valores': {'cartao_id': card_id, 'categoria': category_for(db, tx),
                                          'valor_total': round(amount, 2), 'parcelas': 1,
                                          'primeiro_mes': month, 'data_compra': day,
                                          'descricao': _description(tx),
                                          'parcela_num': meta.get('installmentNumber') if (meta.get('totalInstallments') or 1) > 1 else None,
                                          'parcela_total': meta.get('totalInstallments') if (meta.get('totalInstallments') or 1) > 1 else None}})

            def paid(rowid, cid=card_id):
                # Não apaga lançamentos de faturas já marcadas como pagas.
                m = db.execute('SELECT primeiro_mes FROM compras_cartao WHERE id=?', (rowid,)).fetchone()
                return bool(m and db.execute('SELECT 1 FROM faturas_pagas WHERE cartao_id=? AND mes=?', (cid, m[0])).fetchone())
            res = _reconcile(db, 'compras_cartao', acc['id'], items, entry['from'], entry['to'], 'data_compra', protect=paid)
            summary['cartao'] = [a + b for a, b in zip(summary['cartao'], res)]
            # Pagamentos: substitui os importados do período pelos atuais.
            db.execute("DELETE FROM pagamentos_cartao WHERE cartao_id=? AND origem='pluggy' AND data BETWEEN ? AND ?",
                       (card_id, entry['from'], entry['to']))
            for ext, value, pday, pmonth, pdesc in payments:
                db.execute('INSERT OR REPLACE INTO pagamentos_cartao(cartao_id,valor,data,mes,descricao,externo_id,origem) '
                           "VALUES (?,?,?,?,?,?,'pluggy')", (card_id, value, pday, pmonth, pdesc, ext))
            _auto_mark_paid(db, card_id)


SALARIO_TEXTOS = ('salario', 'salário', 'folha', 'proventos', 'pagamento de salario', 'remuneracao', 'remuneração')


def is_salary(tx):
    cid = str(tx.get('categoryId') or '')
    text = (tx.get('description') or '').lower()
    return (cid.startswith('0101') or tx.get('operationType') in ('FOLHA_PAGAMENTO', 'PORTABILIDADE_SALARIO')
            or any(t in text for t in SALARIO_TEXTOS))


def _store_income(db, tx, bank):
    db.execute('INSERT OR REPLACE INTO rendas_banco(externo_id,data,valor,descricao,banco,salario) VALUES (?,?,?,?,?,?)',
               (tx.get('providerId') or tx['id'], local_date(tx.get('date')), round(abs(float(tx.get('amount') or 0)), 2),
                (tx.get('description') or '')[:80], bank, int(is_salary(tx))))


def detected_income(db, month):
    """(salário mensal 1-14, salário 15-fim, outras entradas) vindos do banco no mês."""
    try:
        rows = db.execute('SELECT data,valor,salario FROM rendas_banco WHERE substr(data,1,7)=?', (month,)).fetchall()
    except Exception:
        return 0.0, 0.0, 0.0
    first = sum(v for d, v, sal in rows if sal and int(d[8:10]) <= 14)
    second = sum(v for d, v, sal in rows if sal and int(d[8:10]) >= 15)
    other = sum(v for d, v, sal in rows if not sal)
    return round(first, 2), round(second, 2), round(other, 2)


def _auto_mark_paid(db, card_id):
    """Marca como paga a fatura quitada no banco (nada falta pagar)."""
    from cartao_credito import invoice_summary
    months = {r[0] for r in db.execute('SELECT DISTINCT mes FROM pagamentos_cartao WHERE cartao_id=?', (card_id,))}
    for month in months:
        info = invoice_summary(db, card_id, month)
        if info['compras'] > 0 and info['pago_total'] > 0 and info['restante'] <= 0.01:
            db.execute('INSERT OR IGNORE INTO faturas_pagas VALUES (?,?,?)', (card_id, month, date.today().isoformat()))


def _finish(db, summary, statuses):
    now = datetime.now().isoformat(timespec='seconds')
    text = (f"Conta: {summary['conta'][0]} novas, {summary['conta'][1]} atualizadas, {summary['conta'][2]} removidas. "
            f"Cartão: {summary['cartao'][0]} novas, {summary['cartao'][1]} atualizadas, {summary['cartao'][2]} removidas. "
            f"Ignoradas: {summary['ignoradas']}.")
    if statuses:
        text += ' Status da conexão: ' + ', '.join(statuses) + '.'
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
            on_done(False, 'Preencha Client ID, Client Secret e adicione ao menos uma conexão.')
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
    body = app.page_body('CONTAS CONECTADAS')
    cfg = get_config(app.db)
    body.add_widget(app.label('Importa automaticamente contas e cartões (Nubank, Santander...) conectados no Meu Pluggy.', 13, app.MUTED, 50))

    status = app.label(cfg['ultimo_resultado'] or 'Ainda não sincronizado.', 13,
                       app.RED if (cfg['ultimo_resultado'] or '').startswith('Falha') else app.GREEN, 70)
    last = app.label('Última sincronização: ' + (cfg['ultima_sync'].replace('T', ' ') if cfg['ultima_sync'] else '—'), 12, app.MUTED, 30)

    form = BoxLayout(orientation='vertical', spacing=dp(8), size_hint_y=None)
    form.bind(minimum_height=form.setter('height'))
    cid = app.input_field('Client ID', cfg['client_id'])
    secret = app.input_field('Client Secret', cfg['client_secret'])
    secret.password = True
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
              app.label('Importar a partir de', 12, app.MUTED, 22), start,
              line, line2, err]:
        form.add_widget(w)

    def save(*_):
        try:
            save_config(app.db, cid.text, secret.text, '', income.active, start.text, auto.active)
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

    # ---- conexões (um Item ID por banco)
    from movimentacoes import _card, _icon, _text, _divider
    conns = _card(app, 8)
    conns.add_widget(_text('Bancos conectados', 17, app.WHITE, True, size_hint_y=None, height=dp(28)))
    contas = app.db.execute('SELECT item_id,tipo,nome,saldo FROM pluggy_contas').fetchall()
    if not cfg['itens']:
        conns.add_widget(_text('Nenhum banco adicionado ainda.', 13, app.MUTED, size_hint_y=None, height=dp(30)))
    for n, (item_id, bank) in enumerate(cfg['itens']):
        if n:
            conns.add_widget(_divider())
        mine = [c for c in contas if c[0] == item_id]
        kinds = ' e '.join(k for k, t in (('conta', 'BANK'), ('cartão', 'CREDIT')) if any(c[1] == t for c in mine))
        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(10))
        from movimentacoes import bank_icon
        row.add_widget(bank_icon(bank, 40))
        words = BoxLayout(orientation='vertical')
        words.add_widget(_text(bank if bank != 'Automático' else 'Banco (detectar na sincronização)', 15, app.WHITE, True))
        words.add_widget(_text((kinds.capitalize() if kinds else 'Aguardando sincronização') + '  •  ' + item_id[:8] + '…', 11, app.MUTED))
        row.add_widget(words)
        rm = app.button('Editar', lambda *_, i=item_id, b=bank: _edit_item(app, i, b), 38)
        rm.size_hint_x = None
        rm.width = dp(92)
        row.add_widget(rm)
        conns.add_widget(row)
    conns.add_widget(_divider())
    conns.add_widget(_text('Adicionar banco', 14, app.WHITE, True, size_hint_y=None, height=dp(26)))
    new_item = app.input_field('Item ID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)')
    new_bank = app.spinner('Automático', BANCOS)
    add_err = app.label('', 12, app.RED, 22)

    def add(*_):
        try:
            add_item(app.db, new_item.text, new_bank.text)
        except ValueError:
            add_err.text = 'Item ID inválido: copie o id da conexão no painel da Pluggy.'
            return
        app.refresh()
    for w in (new_item, new_bank, add_err, app.button('Adicionar banco', add, primary=True)):
        conns.add_widget(w)
    conns.add_widget(_text('Cada banco conectado no Meu Pluggy tem o seu Item ID (painel da Pluggy → Demo → Connected Items).',
                           11, app.MUTED, size_hint_y=None, height=dp(36)))
    body.add_widget(conns)
    body.add_widget(panel)
    body.add_widget(last)
    body.add_widget(status)

    body.add_widget(app.label(
        'Como funciona: gastos das contas entram como despesas; compras dos cartões entram na fatura do mês '
        'certo em Cartão de Crédito, e os pagamentos (inclusive antecipados) abatem o valor a pagar. '
        'Transferências entre suas contas (ex.: Nubank → Santander), aplicações e o pagamento da fatura '
        'são ignorados para não contar duas vezes. O Meu Pluggy atualiza os dados 1 vez por dia.',
        12, app.MUTED, 120))
    body.add_widget(app.label('As credenciais ficam salvas só neste aparelho.', 12, app.MUTED, 34))


def _edit_item(app, item_id, bank):
    form = app.form()
    form.add_widget(app.label('Banco desta conexão', 12, app.MUTED, 22))
    chooser = app.spinner(bank if bank in BANCOS else 'Outro banco', BANCOS[1:])
    form.add_widget(chooser)
    pop = app.popup('Conexão ' + bank, form, 330)

    def save(*_):
        set_item_bank(app.db, item_id, chooser.text)
        pop.dismiss()
        app.refresh()

    def remove(*_):
        pop.dismiss()
        _confirm_remove(app, item_id, bank)
    form.add_widget(app.button('Salvar', save, primary=True))
    form.add_widget(app.button('Remover conexão', remove, danger=True))
    form.add_widget(app.button('Cancelar', lambda *_: pop.dismiss()))
    pop.open()


def _confirm_remove(app, item_id, bank):
    form = app.form()
    form.add_widget(app.label(f'Remover {bank}? Saem do app as contas, cartões, compras e faturas importados '
                              'desta conexão. Seus lançamentos manuais continuam.', 14, app.WHITE, 90))
    pop = app.popup('Remover banco', form, 290)

    def ok(*_):
        remove_item(app.db, item_id)
        pop.dismiss()
        app.refresh()
    form.add_widget(app.button('Remover', ok, danger=True))
    form.add_widget(app.button('Voltar', lambda *_: pop.dismiss()))
    pop.open()
