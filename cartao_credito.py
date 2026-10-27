"""Modulo isolado de cartao de credito: compras parceladas, assinaturas e faturas.

Regras:
- Compra parcelada: cobra uma parcela por mes, do mes da primeira fatura ate a
  ultima parcela. Centavos que sobram da divisao entram na ultima parcela.
- Assinatura: cobra o mesmo valor todo mes a partir do mes de inicio, sem data
  para acabar, ate o usuario cancelar. Ao cancelar, informa-se o ultimo mes
  cobrado; os meses seguintes deixam de ter a cobranca. Mudanca de preco encerra
  o valor antigo e comeca o novo a partir do mes escolhido, preservando as
  faturas anteriores.
"""
from calendar import monthrange
from datetime import date
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.checkbox import CheckBox

MAX_PARCELAS = 120


# ---------------------------------------------------------------- utilitarios
def add_month(ym, n):
    y, m = map(int, ym.split('-'))
    z = y * 12 + m - 1 + n
    return f'{z//12:04d}-{z%12+1:02d}'


def months_between(start, end):
    """Quantidade de meses de start ate end (end - start)."""
    y1, m1 = map(int, start.split('-'))
    y2, m2 = map(int, end.split('-'))
    return (y2 * 12 + m2) - (y1 * 12 + m1)


def parse_month(text):
    """Valida 'AAAA-MM' e devolve no formato normalizado; ValueError se invalido."""
    y, m = map(int, text.strip().split('-'))
    if not 1 <= m <= 12 or not 2000 <= y <= 2100:
        raise ValueError('mes invalido')
    return f'{y:04d}-{m:02d}'


def installment_value(total, count, index):
    """Valor da parcela 'index' (0 = primeira) de uma compra de 'total' em 'count' vezes."""
    cents = round(total * 100)
    base = cents // count
    extra = cents - base * count if index == count - 1 else 0
    return (base + extra) / 100


# ---------------------------------------------------------------- banco
def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS cartoes (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, vencimento INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS compras_cartao (id INTEGER PRIMARY KEY, cartao_id INTEGER NOT NULL, categoria TEXT NOT NULL, valor_total REAL NOT NULL, parcelas INTEGER NOT NULL, primeiro_mes TEXT NOT NULL, data_compra TEXT NOT NULL)')
    cols = {r[1] for r in db.execute('PRAGMA table_info(compras_cartao)')}
    if 'descricao' not in cols:
        db.execute("ALTER TABLE compras_cartao ADD COLUMN descricao TEXT DEFAULT ''")
    db.execute('CREATE TABLE IF NOT EXISTS faturas_pagas (cartao_id INTEGER NOT NULL, mes TEXT NOT NULL, data_pagamento TEXT NOT NULL, PRIMARY KEY(cartao_id, mes))')
    # fim_mes NULL = assinatura ativa (cobra ate ser cancelada).
    db.execute('CREATE TABLE IF NOT EXISTS assinaturas_cartao (id INTEGER PRIMARY KEY, cartao_id INTEGER NOT NULL, nome TEXT NOT NULL, categoria TEXT NOT NULL, valor REAL NOT NULL, inicio_mes TEXT NOT NULL, fim_mes TEXT, data_cadastro TEXT NOT NULL)')
    db.commit()


# ---------------------------------------------------------------- parcelas
def add_installment_purchase(db, card_id, category, total, count, first_month, description=''):
    """Registra uma compra; count=1 e compra a vista no credito."""
    if total <= 0 or not 1 <= count <= MAX_PARCELAS:
        raise ValueError('valor ou parcelas invalidos')
    first_month = parse_month(first_month)
    cur = db.execute('INSERT INTO compras_cartao(cartao_id,categoria,valor_total,parcelas,primeiro_mes,data_compra,descricao) VALUES (?,?,?,?,?,?,?)',
                     (card_id, category, total, count, first_month, date.today().isoformat(), description.strip()))
    db.commit()
    return cur.lastrowid


def delete_purchase(db, purchase_id):
    db.execute('DELETE FROM compras_cartao WHERE id=?', (purchase_id,))
    db.commit()


def installment_status(total, count, first_month, month):
    """Situacao do parcelamento no 'month': parcela atual, restantes e saldo a pagar."""
    index = months_between(first_month, month)
    last_month = add_month(first_month, count - 1)
    if index < 0:
        paid_count, current = 0, None
    elif index >= count:
        paid_count, current = count, None
    else:
        paid_count, current = index, index + 1
    # Saldo inclui a parcela do proprio mes.
    remaining = sum(installment_value(total, count, i) for i in range(paid_count, count))
    return {'parcela_atual': current, 'restantes': count - paid_count,
            'saldo': round(remaining, 2), 'ultimo_mes': last_month}


def installments_in_month(db, month, card_id=None):
    """Parcelas cobradas no mes. Cada item: dict com dados da compra e da parcela."""
    sql = ('SELECT p.id,p.cartao_id,c.nome,p.categoria,p.descricao,p.valor_total,p.parcelas,p.primeiro_mes '
           'FROM compras_cartao p JOIN cartoes c ON c.id=p.cartao_id')
    params = ()
    if card_id is not None:
        sql += ' WHERE p.cartao_id=?'
        params = (card_id,)
    result = []
    for ident, cid, cname, cat, desc, total, count, first in db.execute(sql + ' ORDER BY p.id DESC', params).fetchall():
        index = months_between(first, month)
        if 0 <= index < count:
            result.append({'tipo': 'parcela', 'id': ident, 'cartao_id': cid, 'cartao': cname,
                           'categoria': cat, 'descricao': desc or cat, 'valor': installment_value(total, count, index),
                           'parcela': index + 1, 'parcelas': count, 'valor_total': total, 'primeiro_mes': first})
    return result


# ---------------------------------------------------------------- assinaturas
def add_subscription(db, card_id, name, category, value, start_month):
    if value <= 0 or not name.strip():
        raise ValueError('nome ou valor invalidos')
    cur = db.execute('INSERT INTO assinaturas_cartao(cartao_id,nome,categoria,valor,inicio_mes,fim_mes,data_cadastro) VALUES (?,?,?,?,?,NULL,?)',
                     (card_id, name.strip(), category, value, parse_month(start_month), date.today().isoformat()))
    db.commit()
    return cur.lastrowid


def cancel_subscription(db, subscription_id, last_month):
    """Cancela: 'last_month' e o ultimo mes cobrado; nada e cobrado depois dele."""
    last_month = parse_month(last_month)
    row = db.execute('SELECT inicio_mes FROM assinaturas_cartao WHERE id=?', (subscription_id,)).fetchone()
    if not row:
        raise ValueError('assinatura nao encontrada')
    if last_month < row[0]:
        raise ValueError('o ultimo mes cobrado nao pode ser anterior ao inicio')
    db.execute('UPDATE assinaturas_cartao SET fim_mes=? WHERE id=?', (last_month, subscription_id))
    db.commit()


def reactivate_subscription(db, subscription_id):
    """Desfaz o cancelamento: volta a cobrar todo mes, sem fim."""
    db.execute('UPDATE assinaturas_cartao SET fim_mes=NULL WHERE id=?', (subscription_id,))
    db.commit()


def change_subscription_value(db, subscription_id, new_value, from_month):
    """Reajuste de preco a partir de 'from_month' sem alterar faturas anteriores."""
    from_month = parse_month(from_month)
    if new_value <= 0:
        raise ValueError('valor invalido')
    row = db.execute('SELECT cartao_id,nome,categoria,inicio_mes,fim_mes FROM assinaturas_cartao WHERE id=?', (subscription_id,)).fetchone()
    if not row:
        raise ValueError('assinatura nao encontrada')
    card_id, name, cat, start, end = row
    if end is not None and from_month > end:
        raise ValueError('assinatura ja cancelada antes desse mes')
    if from_month <= start:
        # Reajuste desde o inicio: basta trocar o valor.
        db.execute('UPDATE assinaturas_cartao SET valor=? WHERE id=?', (new_value, subscription_id))
        db.commit()
        return subscription_id
    db.execute('UPDATE assinaturas_cartao SET fim_mes=? WHERE id=?', (add_month(from_month, -1), subscription_id))
    cur = db.execute('INSERT INTO assinaturas_cartao(cartao_id,nome,categoria,valor,inicio_mes,fim_mes,data_cadastro) VALUES (?,?,?,?,?,?,?)',
                     (card_id, name, cat, new_value, from_month, end, date.today().isoformat()))
    db.commit()
    return cur.lastrowid


def delete_subscription(db, subscription_id):
    """Apaga a assinatura inclusive das faturas passadas (use para lancamentos errados)."""
    db.execute('DELETE FROM assinaturas_cartao WHERE id=?', (subscription_id,))
    db.commit()


def subscriptions_in_month(db, month, card_id=None):
    """Assinaturas cobradas no mes (inicio <= mes e, se cancelada, mes <= fim)."""
    sql = ('SELECT s.id,s.cartao_id,c.nome,s.nome,s.categoria,s.valor,s.inicio_mes,s.fim_mes '
           'FROM assinaturas_cartao s JOIN cartoes c ON c.id=s.cartao_id '
           'WHERE s.inicio_mes<=? AND (s.fim_mes IS NULL OR s.fim_mes>=?)')
    params = [month, month]
    if card_id is not None:
        sql += ' AND s.cartao_id=?'
        params.append(card_id)
    result = []
    for ident, cid, cname, name, cat, value, start, end in db.execute(sql + ' ORDER BY s.nome', params).fetchall():
        result.append({'tipo': 'assinatura', 'id': ident, 'cartao_id': cid, 'cartao': cname,
                       'categoria': cat, 'descricao': name, 'valor': round(value, 2),
                       'inicio_mes': start, 'fim_mes': end})
    return result


def subscriptions(db, include_cancelled=False):
    sql = ('SELECT s.id,c.nome,s.nome,s.categoria,s.valor,s.inicio_mes,s.fim_mes '
           'FROM assinaturas_cartao s JOIN cartoes c ON c.id=s.cartao_id')
    if not include_cancelled:
        sql += ' WHERE s.fim_mes IS NULL'
    return db.execute(sql + ' ORDER BY s.fim_mes IS NOT NULL, s.nome').fetchall()


# ---------------------------------------------------------------- faturas
def charges(db, month, card_id=None):
    """Todos os lancamentos do mes: parcelas + assinaturas."""
    return installments_in_month(db, month, card_id) + subscriptions_in_month(db, month, card_id)


def invoices(db, month):
    """Lista (cartao_id, nome, dia_vencimento, total, paga) para o mes."""
    result = []
    for card_id, name, due_day in db.execute('SELECT id,nome,vencimento FROM cartoes ORDER BY id').fetchall():
        total = sum(c['valor'] for c in charges(db, month, card_id))
        paid = bool(db.execute('SELECT 1 FROM faturas_pagas WHERE cartao_id=? AND mes=?', (card_id, month)).fetchone())
        day = min(due_day, monthrange(*map(int, month.split('-')))[1])
        result.append((card_id, name, day, round(total, 2), paid))
    return result


def paid_category_totals(db, month):
    """Gastos por categoria vindos de faturas PAGAS do mes (parcelas e assinaturas)."""
    result = {}
    paid_cards = {cid for cid, _, _, _, paid in invoices(db, month) if paid}
    for item in charges(db, month):
        if item['cartao_id'] in paid_cards:
            result[item['categoria']] = result.get(item['categoria'], 0) + item['valor']
    return {k: round(v, 2) for k, v in result.items()}


def paid_in_period(db, month):
    first = second = 0.0
    for card_id, name, day, total, paid in invoices(db, month):
        if paid:
            if day < 15:
                first += total
            else:
                second += total
    return first, second


# ---------------------------------------------------------------- telas
def _menu_card():
    from main_nexo import menu_card
    return menu_card


def _categories(app):
    cats = [x[0] for x in app.db.execute("SELECT nome FROM categorias WHERE grupo='Variavel' ORDER BY nome")]
    return cats or ['Outros']


def _confirm(app, title, text, action, label_ok='Confirmar'):
    form = app.form()
    form.add_widget(app.label(text, 14, app.WHITE, 80))
    pop = app.popup(title, form, 260)

    def ok(*_):
        action()
        pop.dismiss()
        app.refresh()
    form.add_widget(app.button(label_ok, ok, danger=True))
    form.add_widget(app.button('Voltar', lambda *_: pop.dismiss()))
    pop.open()


def show(app):
    menu_card = _menu_card()
    body = app.page_body('CARTÃO DE CRÉDITO')
    month = app.view_month
    body.add_widget(app.label('Faturas de ' + app.month_name(month), 15, app.MUTED, 35))

    actions = BoxLayout(orientation='horizontal', spacing=dp(10), size_hint_y=None, height=dp(78))
    actions.add_widget(menu_card('Novo cartão', '', lambda *_: edit_card(app), primary=True, height=78))
    actions.add_widget(menu_card('Nova compra', 'À vista ou parcelada', lambda *_: new_purchase(app), height=78))
    body.add_widget(actions)
    body.add_widget(menu_card('Nova assinatura', 'Cobrança mensal até cancelar', lambda *_: new_subscription(app), height=70))

    for cid, name, due in app.db.execute('SELECT id,nome,vencimento FROM cartoes ORDER BY nome').fetchall():
        body.add_widget(menu_card('Gerenciar cartão', name + '  •  Vencimento dia ' + str(due),
                                  lambda _, i=cid, n=name, d=due: edit_card(app, i, n, d), height=72))

    bills = invoices(app.db, month)
    body.add_widget(app.label('Total das faturas: ' + app.money(sum(x[3] for x in bills)), 19, app.WHITE, 50, True))
    if not bills:
        body.add_widget(app.label('Cadastre um cartão para começar.', 14, app.MUTED, 44))
    for cid, name, due, amount, paid in bills:
        content = BoxLayout(orientation='vertical', spacing=dp(5), size_hint_y=None, height=dp(112))
        content.add_widget(app.label(name + ' | Dia ' + str(due), 17, app.WHITE, 34, True))
        content.add_widget(app.label(app.money(amount), 19, app.GREEN if paid else app.WHITE, 34, True))
        line = BoxLayout(size_hint_y=None, height=dp(42))
        check = CheckBox(active=paid, size_hint_x=None, width=dp(45), disabled=amount <= 0)
        check.bind(active=lambda _, active, i=cid, m=month: toggle_bill(app, i, m, active))
        line.add_widget(check)
        line.add_widget(app.label('Fatura paga' if paid else 'Marcar como paga', 14, app.MUTED, 42))
        content.add_widget(line)
        body.add_widget(app.panel(content))

    # Parcelas do mes
    body.add_widget(app.label('Parcelas do mês (toque para detalhes)', 18, app.WHITE, 45, True))
    parts = installments_in_month(app.db, month)
    for p in parts:
        title = f"{p['descricao']}  •  {p['parcela']}/{p['parcelas']}"
        sub = f"{p['cartao']} • {p['categoria']} • {app.money(p['valor'])}"
        body.add_widget(menu_card(title, sub, lambda _, x=p: purchase_details(app, x), height=66))
    if not parts:
        body.add_widget(app.label('Nenhuma parcela neste mês.', 13, app.MUTED, 40))

    # Assinaturas do mes
    subs = subscriptions_in_month(app.db, month)
    body.add_widget(app.label('Assinaturas do mês: ' + app.money(sum(s['valor'] for s in subs)), 18, app.WHITE, 45, True))
    for s in subs:
        state = 'ativa' if s['fim_mes'] is None else 'último mês ' + s['fim_mes']
        sub = f"{s['cartao']} • {s['categoria']} • {app.money(s['valor'])} • {state}"
        body.add_widget(menu_card(s['descricao'], sub, lambda _, x=s: subscription_details(app, x), height=66))
    if not subs:
        body.add_widget(app.label('Nenhuma assinatura cobrada neste mês.', 13, app.MUTED, 40))
    body.add_widget(app.button('Ver todas as assinaturas (inclui canceladas)', lambda *_: list_subscriptions(app), 44))

    body.add_widget(app.label('Compras no crédito entram nos gastos apenas quando a fatura for marcada como paga.', 12, app.MUTED, 70))


def toggle_bill(app, card_id, month, checked):
    amount = next((x[3] for x in invoices(app.db, month) if x[0] == card_id), 0)
    if amount <= 0:
        return
    if checked:
        app.db.execute('INSERT OR IGNORE INTO faturas_pagas VALUES (?,?,?)', (card_id, month, date.today().isoformat()))
    else:
        app.db.execute('DELETE FROM faturas_pagas WHERE cartao_id=? AND mes=?', (card_id, month))
    app.db.commit()
    app.refresh()


def edit_card(app, ident=None, name='', due=10):
    form = app.form()
    namefield = app.input_field('Nome do cartão', name)
    dayfield = app.input_field('Dia de vencimento (1 a 31)', str(due))
    status = app.label('', 12, app.RED, 26)
    for w in [app.label('Nome do cartão', 12, app.MUTED, 22), namefield,
              app.label('Dia de vencimento', 12, app.MUTED, 22), dayfield, status]:
        form.add_widget(w)
    pop = app.popup('Editar cartão' if ident else 'Novo cartão', form, 340)

    def save(*_):
        try:
            day = int(dayfield.text.strip())
        except ValueError:
            day = 0
        if not namefield.text.strip() or not 1 <= day <= 31:
            status.text = 'Informe nome e dia entre 1 e 31.'
            return
        if ident:
            app.db.execute('UPDATE cartoes SET nome=?,vencimento=? WHERE id=?', (namefield.text.strip(), day, ident))
        else:
            app.db.execute('INSERT INTO cartoes(nome,vencimento) VALUES (?,?)', (namefield.text.strip(), day))
        app.db.commit()
        pop.dismiss()
        app.refresh()
    form.add_widget(app.button('Salvar cartão', save, primary=True))
    pop.open()


def _card_spinner(app, cards):
    return app.spinner(cards[0][1], [x[1] for x in cards])


def new_purchase(app):
    cards = app.db.execute('SELECT id,nome FROM cartoes ORDER BY nome').fetchall()
    if not cards:
        return app.message('Cartão necessário', 'Cadastre primeiro um cartão.')
    cats = _categories(app)
    form = app.form()
    card = _card_spinner(app, cards)
    cat = app.spinner(cats[0], cats)
    desc = app.input_field('Descrição (opcional)')
    amount_box, amount = app.money_field()
    count = app.input_field(f'Numero de parcelas (1 a {MAX_PARCELAS})', '1')
    first = app.input_field('AAAA-MM', app.view_month)
    preview = app.label('', 13, app.GREEN, 40)
    status = app.label('', 12, app.RED, 25)

    def update_preview(*_):
        try:
            value = app.parse_amount(amount.text)
            n = int(count.text)
            ym = parse_month(first.text)
            if value <= 0 or not 1 <= n <= MAX_PARCELAS:
                raise ValueError()
        except (ValueError, TypeError):
            preview.text = ''
            return
        if n == 1:
            preview.text = 'À vista: ' + app.money(value) + ' na fatura de ' + ym
        else:
            preview.text = f'{n}x de {app.money(installment_value(value, n, 0))} • última em {add_month(ym, n-1)}'
    for field in (amount, count, first):
        field.bind(text=update_preview)

    for w in [app.label('Cartão', 12, app.MUTED, 22), card, app.label('Categoria', 12, app.MUTED, 22), cat,
              app.label('Descrição', 12, app.MUTED, 22), desc,
              app.label('Valor total da compra', 12, app.MUTED, 22), amount_box,
              app.label('Parcelas', 12, app.MUTED, 22), count,
              app.label('Mês da primeira fatura', 12, app.MUTED, 22), first, preview, status]:
        form.add_widget(w)
    pop = app.popup('Nova compra no crédito', form, 760)

    def save(*_):
        try:
            value = app.parse_amount(amount.text)
            cid = next(i for i, nm in cards if nm == card.text)
            add_installment_purchase(app.db, cid, cat.text, value, int(count.text), first.text, desc.text)
        except (ValueError, TypeError):
            status.text = 'Revise valor, parcelas e mês.'
            return
        pop.dismiss()
        app.refresh()
    form.add_widget(app.button('Salvar compra', save, primary=True))
    pop.open()


def purchase_details(app, p):
    st = installment_status(p['valor_total'], p['parcelas'], p['primeiro_mes'], app.view_month)
    form = app.form()
    lines = [
        (p['descricao'], 17, app.WHITE, 34, True),
        (f"{p['cartao']} • {p['categoria']}", 13, app.MUTED, 28, False),
        (f"Valor total: {app.money(p['valor_total'])} em {p['parcelas']}x", 14, app.WHITE, 30, False),
        (f"Parcela deste mês: {p['parcela']}/{p['parcelas']} • {app.money(p['valor'])}", 14, app.WHITE, 30, False),
        (f"Faltam {st['restantes']} parcela(s), incluindo esta: {app.money(st['saldo'])}", 14, app.GREEN, 30, False),
        (f"Primeira fatura: {p['primeiro_mes']} • Última: {st['ultimo_mes']}", 13, app.MUTED, 30, False),
    ]
    for text, size, color, height, bold in lines:
        form.add_widget(app.label(text, size, color, height, bold))
    pop = app.popup('Compra parcelada', form, 420)

    def remove(*_):
        pop.dismiss()
        _confirm(app, 'Excluir compra',
                 'Remove todas as parcelas desta compra, inclusive de faturas já pagas.',
                 lambda: delete_purchase(app.db, p['id']), 'Excluir compra')
    form.add_widget(app.button('Excluir compra', remove, danger=True))
    form.add_widget(app.button('Fechar', lambda *_: pop.dismiss()))
    pop.open()


def new_subscription(app):
    cards = app.db.execute('SELECT id,nome FROM cartoes ORDER BY nome').fetchall()
    if not cards:
        return app.message('Cartão necessário', 'Cadastre primeiro um cartão.')
    cats = _categories(app)
    form = app.form()
    card = _card_spinner(app, cards)
    cat = app.spinner(cats[0], cats)
    name = app.input_field('Nome (ex.: Netflix, Spotify)')
    amount_box, amount = app.money_field()
    start = app.input_field('AAAA-MM', app.view_month)
    status = app.label('', 12, app.RED, 25)
    for w in [app.label('Cartão', 12, app.MUTED, 22), card, app.label('Nome da assinatura', 12, app.MUTED, 22), name,
              app.label('Categoria', 12, app.MUTED, 22), cat,
              app.label('Valor mensal', 12, app.MUTED, 22), amount_box,
              app.label('Mês da primeira fatura', 12, app.MUTED, 22), start,
              app.label('A cobrança se repete todo mês até você cancelar.', 12, app.MUTED, 36), status]:
        form.add_widget(w)
    pop = app.popup('Nova assinatura', form, 640)

    def save(*_):
        try:
            cid = next(i for i, nm in cards if nm == card.text)
            add_subscription(app.db, cid, name.text, cat.text, app.parse_amount(amount.text), start.text)
        except (ValueError, TypeError):
            status.text = 'Revise nome, valor e mês.'
            return
        pop.dismiss()
        app.refresh()
    form.add_widget(app.button('Salvar assinatura', save, primary=True))
    pop.open()


def subscription_details(app, s):
    form = app.form()
    active = s['fim_mes'] is None
    form.add_widget(app.label(s['descricao'], 17, app.WHITE, 34, True))
    form.add_widget(app.label(f"{s['cartao']} • {s['categoria']} • {app.money(s['valor'])}/mês", 13, app.MUTED, 28))
    form.add_widget(app.label(f"Cobrada desde {s['inicio_mes']}", 13, app.MUTED, 26))
    form.add_widget(app.label('Ativa: cobra todo mês até cancelar' if active else f"Cancelada • último mês cobrado {s['fim_mes']}",
                              14, app.GREEN if active else app.RED, 30))
    status = app.label('', 12, app.RED, 25)
    pop = app.popup('Assinatura', form, 560)

    month_field = app.input_field('AAAA-MM', app.view_month)
    form.add_widget(app.label('Mês (cancelamento ou novo preço)', 12, app.MUTED, 22))
    form.add_widget(month_field)

    def done():
        pop.dismiss()
        app.refresh()

    def cancel(*_):
        try:
            cancel_subscription(app.db, s['id'], month_field.text)
        except (ValueError, TypeError) as e:
            status.text = 'Mês inválido: ' + str(e)
            return
        done()

    def reactivate(*_):
        reactivate_subscription(app.db, s['id'])
        done()

    price_box, price = app.money_field()

    def change_price(*_):
        try:
            change_subscription_value(app.db, s['id'], app.parse_amount(price.text), month_field.text)
        except (ValueError, TypeError) as e:
            status.text = 'Revise valor e mês: ' + str(e)
            return
        done()

    def remove(*_):
        pop.dismiss()
        _confirm(app, 'Excluir assinatura',
                 'Apaga a assinatura de TODAS as faturas, inclusive passadas. Para parar de cobrar, use "Cancelar".',
                 lambda: delete_subscription(app.db, s['id']), 'Excluir')

    if active:
        form.add_widget(app.button('Cancelar assinatura (mês acima = último cobrado)', cancel, danger=True))
    else:
        form.add_widget(app.button('Reativar assinatura', reactivate, primary=True))
    form.add_widget(app.label('Novo valor mensal a partir do mês acima', 12, app.MUTED, 22))
    form.add_widget(price_box)
    form.add_widget(app.button('Alterar valor', change_price))
    form.add_widget(status)
    form.add_widget(app.button('Excluir assinatura', remove, danger=True))
    form.add_widget(app.button('Fechar', lambda *_: pop.dismiss()))
    pop.open()


def list_subscriptions(app):
    menu_card = _menu_card()
    form = app.form()
    rows = subscriptions(app.db, include_cancelled=True)
    active_total = sum(r[4] for r in rows if r[6] is None)
    form.add_widget(app.label('Ativas: ' + app.money(active_total) + '/mês', 15, app.GREEN, 34, True))
    if not rows:
        form.add_widget(app.label('Nenhuma assinatura cadastrada.', 13, app.MUTED, 40))
    pop = app.popup('Assinaturas', form, 600)
    for ident, cname, name, cat, value, start, end in rows:
        item = {'tipo': 'assinatura', 'id': ident, 'cartao': cname, 'categoria': cat, 'descricao': name,
                'valor': value, 'inicio_mes': start, 'fim_mes': end}
        state = 'ativa' if end is None else f'cancelada (até {end})'
        form.add_widget(menu_card(name, f'{cname} • {app.money(value)} • desde {start} • {state}',
                                  lambda _, x=item: (pop.dismiss(), subscription_details(app, x)), height=64))
    form.add_widget(app.button('Fechar', lambda *_: pop.dismiss()))
    pop.open()
