"""Modulo isolado de cartao de credito: compras, parcelas e faturas."""
import sqlite3
from calendar import monthrange
from datetime import date
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.spinner import Spinner
from kivy.uix.checkbox import CheckBox
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView


def add_month(ym, n):
    y, m = map(int, ym.split('-'))
    z = y * 12 + m - 1 + n
    return f'{z//12:04d}-{z%12+1:02d}'


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS cartoes (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, vencimento INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS compras_cartao (id INTEGER PRIMARY KEY, cartao_id INTEGER NOT NULL, categoria TEXT NOT NULL, valor_total REAL NOT NULL, parcelas INTEGER NOT NULL, primeiro_mes TEXT NOT NULL, data_compra TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS faturas_pagas (cartao_id INTEGER NOT NULL, mes TEXT NOT NULL, data_pagamento TEXT NOT NULL, PRIMARY KEY(cartao_id, mes))')
    db.commit()


def invoices(db, month):
    result = []
    for card_id, name, due_day in db.execute('SELECT id,nome,vencimento FROM cartoes ORDER BY id').fetchall():
        total = 0.0
        for val, count, first in db.execute('SELECT valor_total,parcelas,primeiro_mes FROM compras_cartao WHERE cartao_id=?',(card_id,)):
            for i in range(count):
                if add_month(first,i)==month:
                    # Centavos residuais entram na ultima parcela.
                    cents=round(val*100)
                    base=cents//count
                    total+=(base+(cents-base*count if i==count-1 else 0))/100
        paid=bool(db.execute('SELECT 1 FROM faturas_pagas WHERE cartao_id=? AND mes=?',(card_id,month)).fetchone())
        day=min(due_day,monthrange(*map(int,month.split('-')))[1])
        result.append((card_id,name,day,round(total,2),paid))
    return result


def paid_in_period(db, month):
    first=second=0.0
    for card_id,name,day,total,paid in invoices(db,month):
        if paid:
            if day<15:first+=total
            else:second+=total
    return first,second


def show(app):
    from kivy.uix.label import Label
    body=app.page_body('CARTAO DE CREDITO')
    month=app.view_month
    body.add_widget(app.label('Faturas de '+app.month_name(month),15,app.MUTED,35))
    from main_nexo import menu_card
    actions=BoxLayout(orientation='horizontal',spacing=dp(10),size_hint_y=None,height=dp(78))
    actions.add_widget(menu_card('Novo cartão', '',lambda *_:edit_card(app),primary=True,height=78))
    actions.add_widget(menu_card('Nova compra', '',lambda *_:new_purchase(app),height=78))
    body.add_widget(actions)
    cards=db_cards=app.db.execute('SELECT id,nome,vencimento FROM cartoes ORDER BY nome').fetchall()
    for cid,name,due in cards:
        body.add_widget(menu_card('Gerenciar cartão', name+'  •  Vencimento dia '+str(due),lambda _,i=cid,n=name,d=due:edit_card(app,i,n,d),height=72))
    total=sum(x[3] for x in invoices(app.db,month))
    body.add_widget(app.label('Total das faturas: '+app.money(total),19,app.WHITE,50,True))
    bills=invoices(app.db,month)
    if not bills:body.add_widget(app.label('Cadastre um cartao para comecar.',14,app.MUTED,44))
    for cid,name,due,amount,paid in bills:
        content=BoxLayout(orientation='vertical',spacing=dp(5),size_hint_y=None,height=dp(112))
        content.add_widget(app.label(name+' | Dia '+str(due),17,app.WHITE,34,True))
        content.add_widget(app.label(app.money(amount),19,app.GREEN if paid else app.WHITE,34,True))
        line=BoxLayout(size_hint_y=None,height=dp(42))
        check=CheckBox(active=paid,size_hint_x=None,width=dp(45),disabled=amount<=0)
        check.bind(active=lambda _,active,i=cid,m=month:toggle_bill(app,i,m,active))
        line.add_widget(check);line.add_widget(app.label('Fatura paga' if paid else 'Marcar como paga',14,app.MUTED,42))
        content.add_widget(line);body.add_widget(app.panel(content))
    body.add_widget(app.label('Compras e parcelas do mes',18,app.WHITE,45,True))
    found=False
    for ident,cname,cat,val,count,first in app.db.execute('SELECT p.id,c.nome,p.categoria,p.valor_total,p.parcelas,p.primeiro_mes FROM compras_cartao p JOIN cartoes c ON c.id=p.cartao_id ORDER BY p.id DESC').fetchall():
        for i in range(count):
            if add_month(first,i)==month:
                found=True
                cents=round(val*100);base=cents//count
                part=(base+(cents-base*count if i==count-1 else 0))/100
                body.add_widget(app.label(f'{cname} • {cat} • {i+1}/{count}  |  {app.money(part)}',13,app.WHITE,48))
    if not found:body.add_widget(app.label('Nenhuma parcela neste mes.',13,app.MUTED,40))
    body.add_widget(app.label('Compras no credito entram nos gastos apenas quando a fatura for marcada como paga.',12,app.MUTED,70))


def toggle_bill(app,card_id,month,checked):
    amount=next((x[3] for x in invoices(app.db,month) if x[0]==card_id),0)
    if amount<=0:return
    if checked:
        app.db.execute('INSERT OR IGNORE INTO faturas_pagas VALUES (?,?,?)',(card_id,month,date.today().isoformat()))
    else:
        app.db.execute('DELETE FROM faturas_pagas WHERE cartao_id=? AND mes=?',(card_id,month))
    app.db.commit();app.refresh()


def edit_card(app,ident=None,name='',due=10):
    form=app.form()
    namefield=app.input_field('Nome do cartao',name)
    dayfield=app.input_field('Dia de vencimento (1 a 31)',str(due))
    status=app.label('',12,app.RED,26)
    for w in [namefield,dayfield,status]:form.add_widget(w)
    pop=app.popup('Editar cartao' if ident else 'Novo cartao',form,270)
    def save(*_):
        try:day=int(dayfield.text.strip())
        except ValueError:day=0
        if not namefield.text.strip() or not 1<=day<=31:
            status.text='Informe nome e dia entre 1 e 31.';return
        if ident:app.db.execute('UPDATE cartoes SET nome=?,vencimento=? WHERE id=?',(namefield.text.strip(),day,ident))
        else:app.db.execute('INSERT INTO cartoes(nome,vencimento) VALUES (?,?)',(namefield.text.strip(),day))
        app.db.commit();pop.dismiss();app.refresh()
    form.add_widget(app.button('Salvar cartao',save));pop.open()


def new_purchase(app):
    cards=app.db.execute('SELECT id,nome FROM cartoes ORDER BY nome').fetchall()
    if not cards:return app.message('Cartao necessario','Cadastre primeiro um cartao.')
    cats=[x[0] for x in app.db.execute("SELECT nome FROM categorias WHERE grupo='Variavel' ORDER BY nome")]
    form=app.form()
    card=Spinner(text=cards[0][1],values=tuple(x[1] for x in cards),size_hint_y=None,height=dp(44))
    cat=Spinner(text=cats[0] if cats else 'Outros',values=tuple(cats or ['Outros']),size_hint_y=None,height=dp(44))
    amount_box,amount=app.money_field()
    count=app.input_field('Numero de parcelas (1 a 120)','1')
    first=app.input_field('Primeira fatura (AAAA-MM)',app.view_month)
    status=app.label('',12,app.RED,25)
    for w in [app.label('Cartao',12,app.MUTED,22),card,app.label('Categoria',12,app.MUTED,22),cat,app.label('Valor total da compra',12,app.MUTED,22),amount_box,app.label('Parcelas',12,app.MUTED,22),count,app.label('Mes da primeira fatura',12,app.MUTED,22),first,status]:form.add_widget(w)
    pop=app.popup('Nova compra no credito',form,620)
    def save(*_):
        try:
            value=app.parse_amount(amount.text);n=int(count.text);ym=first.text.strip()
            y,m=map(int,ym.split('-'))
            if value<=0 or not 1<=n<=120 or not 1<=m<=12 or not 2000<=y<=2100:raise ValueError()
        except (ValueError,TypeError):status.text='Revise valor, parcelas e mes.';return
        cid=next(i for i,nm in cards if nm==card.text)
        app.db.execute('INSERT INTO compras_cartao(cartao_id,categoria,valor_total,parcelas,primeiro_mes,data_compra) VALUES (?,?,?,?,?,?)',(cid,cat.text,value,n,ym,date.today().isoformat()))
        app.db.commit();pop.dismiss();app.refresh()
    form.add_widget(app.button('Salvar compra',save));pop.open()
