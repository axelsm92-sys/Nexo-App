"""Graficos de financas: sem dependencias alem de Kivy."""
from collections import defaultdict
from datetime import date
from kivy.metrics import dp
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.widget import Widget
from kivy.uix.scrollview import ScrollView

PALETTE=[(.46,.79,.65,1),(.53,.64,.94,1),(.94,.69,.47,1),(.77,.59,.86,1),(.50,.78,.82,1),(.91,.62,.62,1)]

class Bar(Widget):
    def __init__(self, fraction, color, **kwargs):
        super().__init__(size_hint_y=None,height=dp(13),**kwargs)
        self.fraction=max(0,min(1,fraction))
        with self.canvas:
            Color(.19,.22,.27,1)
            self.track=RoundedRectangle(radius=[dp(6)])
            Color(*color)
            self.fill=RoundedRectangle(radius=[dp(6)])
        self.bind(pos=self.redraw,size=self.redraw)
    def redraw(self,*args):
        self.track.pos=self.pos;self.track.size=self.size
        self.fill.pos=self.pos;self.fill.size=(max(0,self.width*self.fraction),self.height)

DEBITO=(.36,.62,.98,1)   # azul: conta (Pix, débito, boleto, dinheiro)
CREDITO=(.70,.45,.95,1)  # roxo: cartão de crédito


def split_by_category(db, month):
    """{categoria: [débito, crédito]} do mês.
    Débito: despesas da conta (inclui custos fixos pagos).
    Crédito: compras e assinaturas da fatura que vence no mês (paga ou não)."""
    from cartao_credito import charges
    data = defaultdict(lambda: [0.0, 0.0])
    for cat, value, pay in db.execute(
            "SELECT categoria,valor,pagamento FROM transacoes WHERE tipo='Despesa' AND substr(data,1,7)=?", (month,)):
        # Lançamento manual marcado como "Credito" conta no lado do crédito.
        data[cat][1 if (pay or '').lower().startswith('cr') else 0] += float(value)
    for item in charges(db, month):
        data[item['categoria']][1] += float(item['valor'])
    return {k: v for k, v in data.items() if abs(v[0]) > 0.004 or abs(v[1]) > 0.004}


class SplitBar(Widget):
    """Barra horizontal com duas partes: débito (azul) + crédito (roxo)."""
    def __init__(self, debit, credit, scale, **kwargs):
        super().__init__(size_hint_y=None, height=dp(14), **kwargs)
        self.a = max(0.0, debit) / scale
        self.b = max(0.0, credit) / scale
        with self.canvas:
            Color(.19, .22, .27, 1)
            self.track = RoundedRectangle(radius=[dp(7)])
            Color(*DEBITO)
            self.fa = RoundedRectangle(radius=[dp(7)])
            Color(*CREDITO)
            self.fb = RoundedRectangle(radius=[dp(7)])
        self.bind(pos=self.redraw, size=self.redraw)

    def redraw(self, *_):
        self.track.pos, self.track.size = self.pos, self.size
        wa, wb = self.width * min(1, self.a), self.width * min(1, self.b)
        self.fa.pos, self.fa.size = self.pos, (wa, self.height)
        self.fb.pos, self.fb.size = (self.x + wa, self.y), (wb, self.height)


class Dot(Widget):
    def __init__(self, color, **kw):
        super().__init__(size_hint=(None, None), size=(dp(12), dp(24)), **kw)
        with self.canvas:
            Color(*color)
            self.r = RoundedRectangle(size=(dp(12), dp(12)), radius=[dp(6)])
        self.bind(pos=self._move)

    def _move(self, *_):
        self.r.pos = (self.x, self.y + dp(6))


def _legend(app, body):
    row = BoxLayout(size_hint_y=None, height=dp(24), spacing=dp(8))
    for name, color in (('Débito / conta', DEBITO), ('Crédito / cartão', CREDITO)):
        row.add_widget(Dot(color))
        row.add_widget(app.label(name, 12, app.MUTED, 24))
    body.add_widget(row)


def split_chart(app, body, data):
    """Uma linha por categoria: nome + total, barra dividida e valores de cada lado."""
    if not data:
        body.add_widget(app.label('Nenhum gasto registrado neste mês.', 13, app.MUTED, 44))
        return
    ranked = sorted(data.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))
    scale = max(sum(v) for _, v in ranked) or 1
    _legend(app, body)
    for cat, (deb, cre) in ranked:
        block = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(66), spacing=dp(3))
        top = BoxLayout(size_hint_y=None, height=dp(24))
        top.add_widget(app.label(cat, 14, app.WHITE, 24, True))
        total = app.label(app.money(deb + cre), 14, app.WHITE, 24, True)
        total.halign = 'right'
        top.add_widget(total)
        block.add_widget(top)
        block.add_widget(SplitBar(deb, cre, scale))
        bottom = BoxLayout(size_hint_y=None, height=dp(22))
        left = app.label('Débito ' + app.money(deb), 11, DEBITO, 22)
        right = app.label('Crédito ' + app.money(cre), 11, CREDITO, 22)
        right.halign = 'right'
        bottom.add_widget(left)
        bottom.add_widget(right)
        block.add_widget(bottom)
        body.add_widget(block)


def heading(app,body,text):
    body.add_widget(app.label(text,17,app.WHITE,40,True))

def bar_row(app,body,name,value,maximum,color):
    # Compatibilidade com chamadas antigas, mantendo o nome da funcao.
    vertical_chart(app,body,[(name,value)],maximum=maximum,colors=[color])

class VerticalBar(Widget):
    def __init__(self, fraction, color, **kwargs):
        super().__init__(**kwargs)
        self.fraction = max(0.0, min(1.0, float(fraction)))
        with self.canvas:
            Color(.19,.22,.27,1)
            self.track = RoundedRectangle(radius=[dp(5)])
            Color(*color)
            self.fill = RoundedRectangle(radius=[dp(5)])
        self.bind(pos=self.redraw,size=self.redraw)
    def redraw(self,*args):
        w=max(0,self.width-dp(16))
        self.track.pos=(self.x+dp(8),self.y)
        self.track.size=(w,self.height)
        self.fill.pos=(self.x+dp(8),self.y)
        self.fill.size=(w,max(0,self.height*self.fraction))

def vertical_chart(app,body,items,maximum=None,colors=None):
    """Barras verticais sem ScrollView aninhado: rolagem vertical livre."""
    if not items:
        return
    scale = maximum if maximum is not None else max([float(v) for _,v in items if float(v)>0] or [1])
    scale = max(float(scale), 0.000001)
    # No maximo tres barras por linha; todas as linhas pertencem ao scroll da pagina.
    # Evita a captura de gestos verticais por um ScrollView horizontal interno.
    for offset in range(0,len(items),3):
        batch=items[offset:offset+3]
        strip=BoxLayout(orientation='horizontal',size_hint_y=None,height=dp(242),spacing=dp(5))
        for j,(name,value) in enumerate(batch):
            i=offset+j
            col=BoxLayout(orientation='vertical',size_hint_x=1)
            value_label=app.label(app.money(value),11,app.WHITE,32)
            value_label.halign='center'
            col.add_widget(value_label)
            col.add_widget(VerticalBar(max(0,float(value))/scale,(colors[i] if colors else PALETTE[i%len(PALETTE)]),size_hint=(1,None),height=dp(153)))
            category=app.label(name,11,app.MUTED,56)
            category.halign='center'
            col.add_widget(category)
            strip.add_widget(col)
        body.add_widget(strip)

def show(app):
    body=app.page_body('GRÁFICOS')
    month=app.view_month
    body.add_widget(app.label('Resumo de '+app.month_name(month),14,app.MUTED,35))
    body.add_widget(app.label('Despesas registradas na conta e faturas pagas, além da divisão entre débito e crédito.',12,app.MUTED,53))
    rows=app.db.execute("SELECT categoria, valor, fixo_id, tipo FROM transacoes WHERE substr(data,1,7)=?",(month,)).fetchall()
    fixed=sum(float(v) for c,v,f,t in rows if t=='Despesa' and f is not None)
    variable=sum(float(v) for c,v,f,t in rows if t=='Despesa' and f is None)
    incomes=sum(float(v) for c,v,f,t in rows if t=='Receita')
    from cartao_credito import invoices
    bills=invoices(app.db,month)
    credit=sum(float(total) for _,_,_,total,paid in bills if paid)
    heading(app,body,'Distribuição dos gastos realizados')
    values=[('Custos fixos pagos',fixed),('Custos variaveis',variable),('Faturas pagas',credit)]
    total=sum(v for _,v in values)
    if total<=0:body.add_widget(app.label('Ainda não há despesas realizadas neste mês.',13,app.MUTED,55))
    vertical_chart(app,body,values)
    body.add_widget(app.label('Total realizado: '+app.money(total),17,app.WHITE,48,True))
    # Débito x crédito: visão do consumo do mês, inclusive faturas ainda não pagas.
    split = split_by_category(app.db, month)
    debit_total = sum(v[0] for v in split.values())
    credit_total = sum(v[1] for v in split.values())
    heading(app, body, 'Débito x crédito')
    body.add_widget(app.label('Crédito = compras da fatura que vence em ' + app.month_name(month) + ' (paga ou não).', 12, app.MUTED, 40))
    vertical_chart(app, body, [('Débito / conta', debit_total), ('Crédito / cartão', credit_total)], colors=[DEBITO, CREDITO])
    if debit_total + credit_total > 0:
        share = 100 * credit_total / (debit_total + credit_total)
        body.add_widget(app.label(f'{share:.0f}% dos gastos do mês foram no cartão de crédito.', 13, app.WHITE, 36))
    heading(app, body, 'Gastos por categoria')
    split_chart(app, body, split)
    heading(app,body,'Salários e saldo restante')
    monthly,advance,_=app.effective_salary(month)
    available=monthly+advance+incomes-total
    vertical_chart(app,body,[('Salarios',monthly+advance),('Outras receitas',incomes),('Despesas realizadas',total),('Disponivel',available)])
    body.add_widget(app.label('Faturas não pagas não reduzem o saldo; compras parceladas não são contadas duas vezes.',12,app.MUTED,65))
