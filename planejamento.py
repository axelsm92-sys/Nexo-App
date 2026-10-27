"""Planejamento financeiro mensal do Nexo. SQLite + Kivy."""
from datetime import date
from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.scrollview import ScrollView
from graficos import bar_row, PALETTE
from cartao_credito import invoices, add_month, paid_category_totals

def initialize(db):
    db.execute("CREATE TABLE IF NOT EXISTS fixos_mensais (fixo_id INTEGER NOT NULL, mes TEXT NOT NULL, valor REAL NOT NULL, nome TEXT NOT NULL, PRIMARY KEY(fixo_id,mes))")
    db.execute("CREATE TABLE IF NOT EXISTS limites_categoria (categoria TEXT NOT NULL, mes TEXT NOT NULL, limite REAL NOT NULL, PRIMARY KEY(categoria,mes))")
    cols={r[1] for r in db.execute('PRAGMA table_info(custos_fixos)')}
    if 'inicio_mes' not in cols:
        db.execute('ALTER TABLE custos_fixos ADD COLUMN inicio_mes TEXT')
    db.commit()

def fixed_rows(db, month):
    result=[]
    for ident,name,value,active,start in db.execute('SELECT id,nome,valor,ativo,inicio_mes FROM custos_fixos ORDER BY nome').fetchall():
        override=db.execute('SELECT nome,valor FROM fixos_mensais WHERE fixo_id=? AND mes=?',(ident,month)).fetchone()
        if start and month<start:continue
        # Historico ja registrado continua visivel mesmo se o custo for desativado.
        recorded=db.execute('SELECT 1 FROM transacoes WHERE fixo_id=? AND substr(data,1,7)=?',(ident,month)).fetchone()
        if not active and not recorded and not override:continue
        n,v=override if override else (name,value)
        result.append((ident,n,float(v),bool(recorded)))
    return result

def update_month(db, ident, month, name, amount):
    db.execute('INSERT INTO fixos_mensais(fixo_id,mes,nome,valor) VALUES(?,?,?,?) ON CONFLICT(fixo_id,mes) DO UPDATE SET nome=excluded.nome,valor=excluded.valor',(ident,month,name,amount))
    # Se ja pago, a transacao deve refletir o valor daquele mes.
    db.execute("UPDATE transacoes SET descricao=?,valor=? WHERE fixo_id=? AND substr(data,1,7)=?",(name,amount,ident,month))
    db.commit()

def pending(db,month):
    fixed=sum(v for _,_,v,paid in fixed_rows(db,month) if not paid)
    bills=sum(total for _,_,_,total,paid in invoices(db,month) if not paid)
    return round(fixed,2),round(bills,2)

def categories(db):
    return [r[0] for r in db.execute("SELECT nome FROM categorias WHERE grupo='Variavel' ORDER BY nome")]

def category_spending(db,month):
    result={}
    for cat,value in db.execute("SELECT categoria,COALESCE(SUM(valor),0) FROM transacoes WHERE tipo='Despesa' AND fixo_id IS NULL AND substr(data,1,7)=? GROUP BY categoria",(month,)):
        result[cat]=result.get(cat,0)+value
    # Parcelas e assinaturas de faturas pagas.
    for cat,value in paid_category_totals(db,month).items():
        result[cat]=result.get(cat,0)+value
    return result

def limit(db,category,month):
    row=db.execute('SELECT limite FROM limites_categoria WHERE categoria=? AND mes=?',(category,month)).fetchone()
    if row:return float(row[0])
    # O ultimo limite anterior funciona como padrao para meses futuros.
    row=db.execute('SELECT limite FROM limites_categoria WHERE categoria=? AND mes<? ORDER BY mes DESC LIMIT 1',(category,month)).fetchone()
    return float(row[0]) if row else 0.0

def set_limit(db,category,month,value):
    db.execute('INSERT INTO limites_categoria VALUES(?,?,?) ON CONFLICT(categoria,mes) DO UPDATE SET limite=excluded.limite',(category,month,value));db.commit()

def history(db, month, salary):
    months=[add_month(month,-i) for i in range(5,-1,-1)]
    data=[]
    for ym in months:
        inc,exp=db.execute("SELECT COALESCE(SUM(CASE WHEN tipo='Receita' THEN valor ELSE 0 END),0),COALESCE(SUM(CASE WHEN tipo='Despesa' THEN valor ELSE 0 END),0) FROM transacoes WHERE substr(data,1,7)=?",(ym,)).fetchone()
        paid=sum(t for _,_,_,t,p in invoices(db,ym) if p)
        data.append((ym,round(exp+paid,2),round(salary+inc-exp-paid,2)))
    return data

def edit_limit(app,cat):
    form=app.form();form.add_widget(app.label('Limite para '+cat,15,app.WHITE,36))
    box,field=app.money_field(str(limit(app.db,cat,app.view_month)).replace('.',','));form.add_widget(box)
    status=app.label('',12,app.RED,25);form.add_widget(status)
    pop=app.popup('Limite mensal',form,235)
    def save(*_):
        try:
            val=app.parse_amount(field.text)
            if val<0:raise ValueError()
        except ValueError:status.text='Informe um valor valido.';return
        set_limit(app.db,cat,app.view_month,val);pop.dismiss();app.refresh()
    form.add_widget(app.button('Salvar limite',save,primary=True));pop.open()

def show(app):
    month=app.view_month
    body=app.page_body('PLANEJAMENTO')
    body.add_widget(app.label('Orcamento de '+app.month_name(month),15,app.MUTED,38))
    fixed,bills=pending(app.db,month)
    rows=app.db.execute('SELECT tipo,valor FROM transacoes WHERE substr(data,1,7)=?',(month,)).fetchall()
    incomes=sum(v for t,v in rows if t=='Receita')
    expenses=sum(v for t,v in rows if t=='Despesa')+sum(t for _,_,_,t,p in invoices(app.db,month) if p)
    m,q=app.salary_values();available=m+q+incomes-expenses
    for title,value,col in [('Saldo disponivel',available,app.GREEN),('Fixos pendentes',fixed,app.WHITE),('Faturas pendentes',bills,app.WHITE),('Saldo previsto',available-fixed-bills,app.GREEN if available>=fixed+bills else app.RED)]:
        body.add_widget(app.label(title+': '+app.money(value),15,col,45,True))
    body.add_widget(app.label('Limites por categoria (toque para editar)',17,app.WHITE,48,True))
    spent=category_spending(app.db,month)
    for i,cat in enumerate(categories(app.db)):
        value=spent.get(cat,0);maximum=limit(app.db,cat,month)
        body.add_widget(app.button(cat+'  |  '+app.money(value)+' / '+(app.money(maximum) if maximum else 'Sem limite'),lambda _,c=cat:edit_limit(app,c),48))
        if maximum:bar_row(app,body,'Utilizado',value,maximum,app.RED if value>maximum else PALETTE[i%len(PALETTE)])
    body.add_widget(app.label('Historico: ultimos 6 meses',17,app.WHITE,50,True))
    data=history(app.db,month,m+q)
    top=max([e for _,e,_ in data]+[1])
    for ym,expense,balance in data:
        body.add_widget(app.label(ym+' | Despesas '+app.money(expense)+' | Saldo '+app.money(balance),12,app.MUTED,42))
        bar_row(app,body,'Gastos',expense,top,PALETTE[1])
    body.add_widget(app.label('Historico usa os salarios atuais como referencia. Custos pendentes nao entram nas despesas realizadas.',12,app.MUTED,72))
