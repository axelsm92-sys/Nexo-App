"""Movimentações do Nexo: contas conectadas, lista de transações e categorias.

- Junta as transações da conta (tabela transacoes) e as compras do cartão
  (compras_cartao) numa lista única, mais recentes primeiro.
- Tocar numa movimentação abre a escolha de categoria, com opção de criar uma
  categoria nova e de aplicar a mesma categoria a todas as movimentações com a
  mesma descrição (inclusive as que ainda vão chegar pela sincronização).
- A categoria escolhida pelo usuário fica marcada como manual e a
  sincronização com a Pluggy não a substitui.
"""
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from kivy.graphics import Color, Line, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.checkbox import CheckBox
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.widget import Widget

ICONS = Path(__file__).resolve().parent / 'icones'
NOVA = '+ Nova categoria...'


# ---------------------------------------------------------------- banco
def initialize(db):
    for table in ('transacoes', 'compras_cartao'):
        cols = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        if 'categoria_manual' not in cols:
            db.execute(f'ALTER TABLE {table} ADD COLUMN categoria_manual INTEGER DEFAULT 0')
    db.execute('CREATE TABLE IF NOT EXISTS regras_categoria (chave TEXT PRIMARY KEY, categoria TEXT NOT NULL)')
    db.commit()


def normalize(text):
    """Chave de comparação: sem acentos, minúsculas, sem números e sem '(3/10)'."""
    text = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()
    text = re.sub(r'\(\d+/\d+\)', ' ', text)
    text = re.sub(r'[\d*#._\-/]+', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def rule_for(db, description):
    key = normalize(description)
    if not key:
        return None
    try:
        row = db.execute('SELECT categoria FROM regras_categoria WHERE chave=?', (key,)).fetchone()
    except Exception:  # tabela ainda não criada
        return None
    return row[0] if row else None


def categories(db):
    return [r[0] for r in db.execute("SELECT nome FROM categorias WHERE grupo='Variavel' ORDER BY nome")]


def create_category(db, name):
    name = name.strip()
    if not name:
        raise ValueError('nome vazio')
    db.execute("INSERT OR IGNORE INTO categorias(nome,grupo) VALUES (?,'Variavel')", (name,))
    db.commit()
    return name


def movements(db, month=None, limit=None):
    """Lista única: dicts com tipo ('conta'|'cartao'), id, descricao, categoria,
    valor (negativo = saída), data, origem, conta."""
    items = []
    sql = 'SELECT id,descricao,categoria,tipo,valor,data,pagamento,origem,fixo_id FROM transacoes'
    params = []
    if month:
        sql += ' WHERE substr(data,1,7)=?'
        params.append(month)
    for ident, desc, cat, kind, value, day, pay, origin, fixo in db.execute(sql, params):
        items.append({'tipo': 'conta', 'id': ident, 'descricao': desc, 'categoria': cat,
                      'valor': value if kind == 'Receita' else -value, 'data': day,
                      'origem': origin, 'conta': pay or 'Outro', 'fixo': fixo is not None})
    sql = ('SELECT p.id,COALESCE(NULLIF(p.descricao,\'\'),p.categoria),p.categoria,p.valor_total,p.parcelas,'
           'p.data_compra,p.origem,c.nome FROM compras_cartao p JOIN cartoes c ON c.id=p.cartao_id')
    params = []
    if month:
        sql += ' WHERE substr(p.data_compra,1,7)=?'
        params.append(month)
    for ident, desc, cat, total, count, day, origin, card in db.execute(sql, params):
        if origin == 'pluggy' and day and day > datetime.now().date().isoformat():
            continue  # parcelas futuras: aparecem nas faturas, não nas movimentações
        label = desc + (f' ({count}x)' if count and count > 1 else '')
        items.append({'tipo': 'cartao', 'id': ident, 'descricao': label, 'categoria': cat,
                      'valor': -total, 'data': day, 'origem': origin, 'conta': card, 'fixo': False})
    items.sort(key=lambda x: (x['data'] or '', x['id']), reverse=True)
    return items[:limit] if limit else items


def set_category(db, item, category, apply_all=False):
    """Altera a categoria; com apply_all, vale para todas as movimentações com a
    mesma descrição e para as próximas importadas."""
    table = 'transacoes' if item['tipo'] == 'conta' else 'compras_cartao'
    db.execute(f'UPDATE {table} SET categoria=?,categoria_manual=1 WHERE id=?', (category, item['id']))
    changed = 1
    if apply_all:
        key = normalize(item['descricao'])
        if key:
            db.execute('INSERT INTO regras_categoria(chave,categoria) VALUES (?,?) '
                       'ON CONFLICT(chave) DO UPDATE SET categoria=excluded.categoria', (key, category))
            for tbl, col in (('transacoes', 'descricao'), ('compras_cartao', 'descricao')):
                extra = " AND tipo='Despesa' AND fixo_id IS NULL" if tbl == 'transacoes' else ''
                for rid, desc in db.execute(f'SELECT id,{col} FROM {tbl} WHERE 1=1{extra}').fetchall():
                    if normalize(desc) == key and not (tbl == table and rid == item['id']):
                        db.execute(f'UPDATE {tbl} SET categoria=?,categoria_manual=1 WHERE id=?', (category, rid))
                        changed += 1
    db.commit()
    return changed


# ---------------------------------------------------------------- componentes
def _bg(widget, color, radius=14):
    with widget.canvas.before:
        Color(*color)
        rect = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[dp(radius)])
    widget.bind(pos=lambda w, v: setattr(rect, 'pos', v), size=lambda w, v: setattr(rect, 'size', v))


def _text(text, fs, color, bold=False, halign='left', **kw):
    w = Label(text=text, font_size=dp(fs), color=color, bold=bold, halign=halign, valign='middle',
              shorten=True, shorten_from='right', **kw)
    w.bind(size=lambda o, v: setattr(o, 'text_size', v))
    return w


def _icon(name, size=40):
    path = ICONS / name
    if path.is_file():
        return Image(source=str(path), size_hint=(None, None), size=(dp(size), dp(size)), fit_mode='contain')
    return Widget(size_hint=(None, None), size=(dp(size), dp(size)))


class Tap(ButtonBehavior, BoxLayout):
    """Linha inteira clicável."""
    def __init__(self, on_tap=None, **kw):
        super().__init__(**kw)
        self._on_tap = on_tap

    def on_release(self):
        if self._on_tap:
            self._on_tap()


def _card(app, spacing=10):
    box = BoxLayout(orientation='vertical', padding=[dp(16), dp(14), dp(16), dp(14)], spacing=dp(spacing),
                    size_hint_y=None)
    box.bind(minimum_height=box.setter('height'))
    _bg(box, (.11, .15, .21, 1), 18)
    return box


def _divider():
    w = Widget(size_hint_y=None, height=dp(1))
    with w.canvas:
        Color(1, 1, 1, .08)
        line = Line(points=[0, 0, 0, 0], width=1)
    w.bind(pos=lambda o, v: setattr(line, 'points', [o.x, o.y, o.right, o.y]),
           size=lambda o, v: setattr(line, 'points', [o.x, o.y, o.right, o.y]))
    return w


def _when(iso):
    if not iso:
        return 'nunca'
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    delta = datetime.now() - dt
    if delta.days == 0:
        return 'hoje às ' + dt.strftime('%H:%M')
    if delta.days == 1:
        return 'ontem às ' + dt.strftime('%H:%M')
    return dt.strftime('%d/%m às %H:%M')


def connected_accounts_panel(app):
    """Cartão 'Contas conectadas' com status e botão de sincronizar."""
    import integracao_pluggy as ip
    cfg = ip.get_config(app.db)
    card = _card(app, 12)
    head = BoxLayout(size_hint_y=None, height=dp(30))
    head.add_widget(_text('Contas conectadas', 18, app.WHITE, True))
    failed = (cfg['ultimo_resultado'] or '').startswith('Falha')
    if cfg['configurado']:
        badge = _text('Erro' if failed else 'Ativa', 12, app.RED if failed else app.GREEN, True, 'center',
                      size_hint=(None, None), size=(dp(64), dp(26)))
        _bg(badge, (.30, .12, .14, 1) if failed else (.06, .30, .22, 1), 13)
        head.add_widget(badge)
    card.add_widget(head)

    if not cfg['configurado']:
        row = Tap(on_tap=lambda: app.go('pluggy'), size_hint_y=None, height=dp(56), spacing=dp(12))
        row.add_widget(_icon('banco_nubank.png', 44))
        words = BoxLayout(orientation='vertical')
        words.add_widget(_text('Conectar Nubank', 16, app.WHITE, True))
        words.add_widget(_text('Importe conta e cartão automaticamente', 12, app.MUTED))
        row.add_widget(words)
        row.add_widget(_text('>', 18, app.WHITE, size_hint_x=None, width=dp(16)))
        card.add_widget(row)
        return card

    contas = app.db.execute('SELECT tipo,nome,saldo FROM pluggy_contas').fetchall()
    kinds = {t for t, _, _ in contas}
    subtitle = ' e '.join(x for x, k in (('Conta', 'BANK'), ('cartão', 'CREDIT')) if k in kinds) or 'Aguardando 1ª sincronização'
    subtitle = subtitle[0].upper() + subtitle[1:]
    bank_balance = next((s for t, _, s in contas if t == 'BANK'), None)
    if bank_balance is not None:
        subtitle += '  •  Saldo ' + app.money(bank_balance)
    row = Tap(on_tap=lambda: app.go('pluggy'), size_hint_y=None, height=dp(56), spacing=dp(12))
    row.add_widget(_icon('banco_nubank.png', 44))
    words = BoxLayout(orientation='vertical')
    words.add_widget(_text('Nubank', 17, app.WHITE, True))
    words.add_widget(_text(subtitle, 12, app.MUTED))
    row.add_widget(words)
    row.add_widget(_icon('alerta.png' if failed else 'check.png', 24))
    card.add_widget(row)
    card.add_widget(_divider())

    # Botão "Sincronizar movimentações"
    sync = Tap(orientation='vertical', size_hint_y=None, height=dp(70), padding=[dp(10), dp(8)])
    _bg(sync, (.17, .24, .32, 1), 14)
    top = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
    top.add_widget(Widget())
    top.add_widget(_icon('sincronizar.png', 22))
    title = _text('Sincronizar movimentações', 16, app.WHITE, True, size_hint_x=None, width=dp(230))
    top.add_widget(title)
    top.add_widget(Widget())
    sync.add_widget(top)
    info = _text('Última atualização: ' + _when(cfg['ultima_sync']), 12, app.MUTED, False, 'center')
    sync.add_widget(info)

    def done(ok, msg):
        sync.disabled = False
        if ok:
            app.refresh()
        else:
            title.text = 'Sincronizar movimentações'
            info.text = 'Falha: ' + msg[:60]
            info.color = app.RED

    def start():
        if sync.disabled:
            return
        sync.disabled = True
        title.text = 'Sincronizando...'
        info.text = 'Buscando dados no Nubank via Pluggy'
        if not ip.sync(app, on_done=done):
            sync.disabled = False
            title.text = 'Sincronizar movimentações'
    sync._on_tap = start
    card.add_widget(sync)
    return card


def movement_row(app, item, on_change=None):
    out = item['valor'] < 0
    row = Tap(on_tap=lambda: category_picker(app, item, on_change), size_hint_y=None, height=dp(52), spacing=dp(10))
    words = BoxLayout(orientation='vertical')
    words.add_widget(_text(item['descricao'], 16, app.WHITE))
    via = 'Cartão ' + item['conta'] if item['tipo'] == 'cartao' else item['conta']
    day = item['data'][8:10] + '/' + item['data'][5:7] if item['data'] and len(item['data']) >= 10 else ''
    words.add_widget(_text(f"{item['categoria']}  •  {day}  •  {via}", 11, app.MUTED))
    row.add_widget(words)
    value = _text(('− ' if out else '+ ') + app.money(abs(item['valor'])), 16,
                  (.96, .55, .55, 1) if out else app.GREEN, False, 'right', size_hint_x=None, width=dp(118))
    row.add_widget(value)
    return row


def latest_panel(app, month, limit=8):
    card = _card(app, 6)
    head = BoxLayout(size_hint_y=None, height=dp(32))
    head.add_widget(_text('Últimas movimentações', 18, app.WHITE, True))
    card.add_widget(head)
    items = movements(app.db, month, limit)
    if not items:
        card.add_widget(_text('Nenhuma movimentação neste mês.', 13, app.MUTED, size_hint_y=None, height=dp(36)))
    for i, item in enumerate(items):
        if i:
            card.add_widget(_divider())
        card.add_widget(movement_row(app, item, app.refresh))
    card.add_widget(_text('Toque numa movimentação para escolher a categoria.', 11, app.MUTED, False, 'center',
                          size_hint_y=None, height=dp(26)))
    more = app.button('Ver todas as movimentações do mês', lambda *_: app.go('movimentacoes'), 44)
    card.add_widget(more)
    return card


def show(app):
    """Tela com todas as movimentações do mês, agrupadas por dia."""
    body = app.page_body('MOVIMENTAÇÕES')
    items = movements(app.db, app.view_month)
    out = sum(-i['valor'] for i in items if i['valor'] < 0)
    inc = sum(i['valor'] for i in items if i['valor'] > 0)
    body.add_widget(app.label(f'Saídas {app.money(out)}  •  Entradas {app.money(inc)}', 14, app.MUTED, 34))
    if not items:
        body.add_widget(app.label('Nenhuma movimentação neste mês.', 14, app.MUTED, 40))
        return
    current, card = None, None
    for item in items:
        if item['data'] != current:
            current = item['data']
            body.add_widget(app.label(f'{current[8:10]}/{current[5:7]}' if current else '—', 13, app.MUTED, 26, True))
            card = _card(app, 6)
            body.add_widget(card)
        elif card.children:
            card.add_widget(_divider())
        card.add_widget(movement_row(app, item, app.refresh))


# ---------------------------------------------------------------- escolher categoria
def category_picker(app, item, on_change=None):
    form = app.form()
    sign = '−' if item['valor'] < 0 else '+'
    form.add_widget(app.label(item['descricao'], 16, app.WHITE, 30, True))
    form.add_widget(app.label(f"{sign} {app.money(abs(item['valor']))}  •  {item['data']}  •  "
                              + ('Cartão ' if item['tipo'] == 'cartao' else '') + item['conta'], 12, app.MUTED, 26))
    if item.get('fixo'):
        form.add_widget(app.label('Este lançamento é um custo fixo (categoria "Fixo").', 12, app.MUTED, 26))
    cats = categories(app.db)
    if item['categoria'] not in cats and item['categoria'] not in ('Fixo', 'Receita'):
        cats.append(item['categoria'])
    form.add_widget(app.label('Categoria', 12, app.MUTED, 22))
    current = item['categoria'] if item['categoria'] in cats else (cats[0] if cats else NOVA)
    chooser = app.spinner(current, sorted(cats) + [NOVA])
    form.add_widget(chooser)
    new_name = app.input_field('Nome da nova categoria')
    new_label = app.label('Nova categoria', 12, app.MUTED, 22)
    holder = BoxLayout(orientation='vertical', size_hint_y=None, height=0, spacing=dp(6))
    form.add_widget(holder)

    def toggle_new(_, text):
        holder.clear_widgets()
        if text == NOVA:
            holder.add_widget(new_label)
            holder.add_widget(new_name)
            holder.height = dp(76)
        else:
            holder.height = 0
    chooser.bind(text=toggle_new)
    toggle_new(chooser, chooser.text)

    line = BoxLayout(size_hint_y=None, height=dp(46))
    same = CheckBox(active=False, size_hint_x=None, width=dp(44))
    line.add_widget(same)
    line.add_widget(app.label('Aplicar a todas com esta descrição (inclusive as próximas)', 12, app.WHITE, 46))
    form.add_widget(line)
    status = app.label('', 12, app.RED, 24)
    form.add_widget(status)
    pop = app.popup('Categoria da movimentação', form, 470)

    def save(*_):
        try:
            cat = create_category(app.db, new_name.text) if chooser.text == NOVA else chooser.text
        except ValueError:
            status.text = 'Digite o nome da nova categoria.'
            return
        n = set_category(app.db, item, cat, same.active)
        pop.dismiss()
        if on_change:
            on_change()
        if n > 1:
            app.message('Categoria aplicada', f'{n} movimentações agora estão em "{cat}".')
    form.add_widget(app.button('Salvar categoria', save, primary=True))
    form.add_widget(app.button('Cancelar', lambda *_: pop.dismiss()))
    pop.open()
