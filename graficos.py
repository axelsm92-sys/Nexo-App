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
    body=app.page_body('GRAFICOS')
    month=app.view_month
    body.add_widget(app.label('Resumo de '+app.month_name(month),14,app.MUTED,35))
    body.add_widget(app.label('Graficos de despesas efetivamente registradas e faturas pagas.',12,app.MUTED,53))
    rows=app.db.execute("SELECT categoria, valor, fixo_id, tipo FROM transacoes WHERE substr(data,1,7)=?",(month,)).fetchall()
    fixed=sum(float(v) for c,v,f,t in rows if t=='Despesa' and f is not None)
    variable=sum(float(v) for c,v,f,t in rows if t=='Despesa' and f is None)
    incomes=sum(float(v) for c,v,f,t in rows if t=='Receita')
    from cartao_credito import invoices
    bills=invoices(app.db,month)
    credit=sum(float(total) for _,_,_,total,paid in bills if paid)
    heading(app,body,'Distribuicao dos gastos')
    values=[('Custos fixos pagos',fixed),('Custos variaveis',variable),('Faturas pagas',credit)]
    total=sum(v for _,v in values)
    if total<=0:body.add_widget(app.label('Ainda nao ha despesas realizadas neste mes.',13,app.MUTED,55))
    vertical_chart(app,body,values)
    body.add_widget(app.label('Total realizado: '+app.money(total),17,app.WHITE,48,True))
    heading(app,body,'Gastos por categoria')
    categories=defaultdict(float)
    for cat,v,f,t in rows:
        if t=='Despesa':categories[cat]+=float(v)
    # Compras no credito so entram na analise depois de pagar a fatura.
    for card_id,_,_,_,paid in bills:
        if not paid:continue
        purchases=app.db.execute('SELECT categoria,valor_total,parcelas,primeiro_mes FROM compras_cartao WHERE cartao_id=?',(card_id,)).fetchall()
        from cartao_credito import add_month
        for cat,value,n,first in purchases:
            for i in range(n):
                if add_month(first,i)==month:
                    cents=round(value*100);base=cents//n
                    categories[cat]+=(base+(cents-base*n if i==n-1 else 0))/100
    ranked=sorted(categories.items(),key=lambda item:-item[1])
    if not ranked:body.add_widget(app.label('Nenhuma categoria com gastos registrados.',13,app.MUTED,48))
    maximum=max((v for _,v in ranked),default=0)
    vertical_chart(app,body,ranked)
    heading(app,body,'Salarios e saldo restante')
    monthly,advance=app.salary_values()
    available=monthly+advance+incomes-total
    vertical_chart(app,body,[('Salarios',monthly+advance),('Outras receitas',incomes),('Despesas realizadas',total),('Disponivel',available)])
    body.add_widget(app.label('As faturas nao pagas nao reduzem o saldo; compras parceladas nao sao contadas duas vezes.',12,app.MUTED,65))
