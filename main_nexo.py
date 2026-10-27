import sqlite3
import traceback
import sys
import os
from datetime import date
from pathlib import Path
from kivy.app import App
from kivy.core.window import Window
from kivy.metrics import dp
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner, SpinnerOption
from kivy.uix.popup import Popup
from kivy.uix.checkbox import CheckBox
from kivy.uix.image import Image as KivyImage
from kivy.clock import Clock
from kivy.animation import Animation
from kivy.utils import platform
from calendar import monthrange
import cartao_credito as credito
import graficos
import navegacao
import periodo
import planejamento
import integracao_pluggy
import movimentacoes

BG=(.059,.090,.165,1); CARD=(.11,.15,.21,1); WHITE=(.97,.98,.99,1)
MUTED=(.62,.68,.74,1); GREEN=(.063,.725,.506,1); RED=(.96,.48,.47,1)
# Identidade visual compartilhada por campos, botões e janelas.
FIELD=(.16,.21,.28,1)      # fundo de campos de texto e seletores
SURFACE=(.19,.25,.32,1)    # botões secundários (visíveis sobre BG e sobre CARD)
POPUP_BG=(.07,.11,.17,1)

def money(v):
    return 'R$ '+f'{v:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
def parse_amount(s):
    s=s.strip().replace('R$','').replace(' ','')
    if ',' in s: s=s.replace('.','').replace(',','.')
    return round(float(s),2)
def label(t,size=15,color=WHITE,height=32,bold=False):
    w=Label(text=str(t),font_size=dp(size),color=color,bold=bold,size_hint_y=None,height=dp(height),halign='left',valign='middle')
    w.bind(width=lambda obj,width:setattr(obj,'text_size',(width,None)))
    return w
def _rounded_bg(widget, color, radius=12):
    with widget.canvas.before:
        Color(*color)
        shape = RoundedRectangle(pos=widget.pos, size=widget.size, radius=[dp(radius)])
    widget.bind(pos=lambda obj, v: setattr(shape, 'pos', v),
                size=lambda obj, v: setattr(shape, 'size', v))
    return shape

def button(t,fn,height=46,primary=False,danger=False):
    """Botão padrão do app: arredondado; primary=verde, danger=texto vermelho."""
    w=Button(text=t,size_hint_y=None,height=dp(height),background_normal='',background_down='',
             background_color=(0,0,0,0),color=BG if primary else (RED if danger else WHITE),
             bold=primary,font_size=dp(15 if primary else 14),halign='center',valign='middle')
    w.bind(size=lambda obj,v:setattr(obj,'text_size',(v[0]-dp(16),None)))
    _rounded_bg(w, GREEN if primary else SURFACE, 13)
    w.bind(on_release=fn);return w

class NexoSpinnerOption(SpinnerOption):
    """Opções da lista suspensa no tema escuro."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.background_normal=''
        self.background_down=''
        self.background_color=CARD
        self.color=WHITE
        self.height=dp(46)

def spinner(text, values, height=48):
    w=Spinner(text=text,values=tuple(values),size_hint_y=None,height=dp(height),
              background_normal='',background_down='',background_color=(0,0,0,0),
              color=WHITE,font_size=dp(15),option_cls=NexoSpinnerOption)
    _rounded_bg(w, FIELD, 12)
    return w

def style_input(field):
    """Aplica o visual escuro (igual ao de Nova transação) a um TextInput."""
    field.background_normal='';field.background_active=''
    field.background_color=FIELD
    field.foreground_color=WHITE;field.cursor_color=GREEN
    field.hint_text_color=(.45,.51,.58,1)
    field.font_size=dp(15);field.padding=[dp(12),dp(13),dp(12),dp(10)]
    return field

def popup(title, content, height, scroll=True):
    """Janela padrão: fundo escuro, linha verde e rolagem interna para o teclado."""
    if scroll:
        sc=ScrollView(do_scroll_x=False,bar_width=dp(3))
        sc.add_widget(content)
        content=sc
    return Popup(title=title,content=content,size_hint=(.94,None),
                 height=min(dp(height),Window.height*.83),
                 separator_color=GREEN,background_color=POPUP_BG,
                 title_color=WHITE,title_size=dp(16))

def modern_button(t, fn, primary=False, height=54):
    bg = GREEN if primary else CARD
    fg = BG if primary else WHITE
    w = Button(text=t, size_hint_y=None, height=dp(height),
               background_normal='', background_down='', background_color=(0,0,0,0),
               color=fg, bold=primary, font_size=dp(15))
    with w.canvas.before:
        Color(*bg)
        shape = RoundedRectangle(pos=w.pos, size=w.size, radius=[dp(15)])
    w.bind(pos=lambda obj, v: setattr(shape, 'pos', v),
           size=lambda obj, v: setattr(shape, 'size', v))
    w.bind(on_release=fn)
    return w

def icon_button(title, icon_name, fn, primary=False, height=60):
    """Botão com ícone e texto centralizados, compatível com Kivy/Pydroid."""
    bg = GREEN if primary else CARD
    fg = BG if primary else WHITE
    root = FloatLayout(size_hint_y=None, height=dp(height))
    with root.canvas.before:
        Color(*bg)
        shape = RoundedRectangle(pos=root.pos, size=root.size, radius=[dp(15)])
    root.bind(pos=lambda obj, v: setattr(shape, 'pos', v),
              size=lambda obj, v: setattr(shape, 'size', v))

    content = BoxLayout(orientation='horizontal', spacing=dp(8),
                        padding=[dp(10), dp(8), dp(10), dp(8)],
                        size_hint=(1, 1), pos_hint={'x': 0, 'y': 0})
    path = Path(__file__).resolve().parent / 'icones' / icon_name
    if path.is_file():
        content.add_widget(KivyImage(source=str(path), size_hint_x=None,
                                     width=dp(25), fit_mode='contain'))
    caption = Label(text=title, font_size=dp(13 if not primary else 16),
                    bold=primary, color=fg, halign='center', valign='middle',
                    shorten=True, shorten_from='right')
    caption.bind(size=lambda w, v: setattr(w, 'text_size', v))
    content.add_widget(caption)
    root.add_widget(content)

    # Camada de toque sobre todo o botão, sem deslocar o conteúdo visual.
    touch = Button(text='', size_hint=(1, 1), pos_hint={'x': 0, 'y': 0},
                   background_normal='', background_down='',
                   background_color=(0, 0, 0, 0))
    touch.bind(on_release=fn)
    root.add_widget(touch)
    return root

def menu_card(title, subtitle, fn, icon_name=None, primary=False, height=76):
    """Cartão clicável com conteúdo ancorado no próprio contêiner."""
    root=FloatLayout(size_hint_y=None, height=dp(height))
    with root.canvas.before:
        Color(*(GREEN if primary else CARD))
        shape=RoundedRectangle(pos=root.pos,size=root.size,radius=[dp(16)])
    root.bind(pos=lambda w,v:setattr(shape,'pos',v),size=lambda w,v:setattr(shape,'size',v))
    line=BoxLayout(orientation='horizontal',padding=[dp(15),dp(9),dp(14),dp(9)],
                   spacing=dp(12),size_hint=(1,1),pos_hint={'x':0,'y':0})
    icon_path=Path(__file__).resolve().parent/'icones'/(icon_name or '')
    if icon_name and icon_path.is_file():
        line.add_widget(KivyImage(source=str(icon_path),size_hint_x=None,width=dp(27),fit_mode='contain'))
    words=BoxLayout(orientation='vertical',spacing=dp(1))
    foreground=BG if primary else WHITE
    words.add_widget(Label(text=title,color=foreground,bold=True,font_size=dp(15),
                           halign='left',valign='middle',shorten=True,
                           text_size=(None,None)))
    if subtitle:
        words.add_widget(Label(text=subtitle,color=BG if primary else MUTED,
                               font_size=dp(11),halign='left',valign='middle',shorten=True))
    line.add_widget(words)
    arrow=Label(text='>',color=foreground,font_size=dp(20),size_hint_x=None,width=dp(15))
    line.add_widget(arrow)
    root.add_widget(line)
    def sync_text(*_):
        for child in words.children:
            child.text_size=(max(0,words.width),None)
    words.bind(width=sync_text)
    touch=Button(text='',background_normal='',background_down='',background_color=(0,0,0,0),
                 size_hint=(1,1),pos_hint={'x':0,'y':0})
    touch.bind(on_release=fn)
    root.add_widget(touch)
    return root

def input_field(hint,value=''):
    return style_input(TextInput(text=str(value),hint_text=hint,multiline=False,size_hint_y=None,height=dp(48)))
def money_field(value=''):
    # Prefixo fixo R$ fora da area digitavel, para evitar apagar o prefixo.
    box=BoxLayout(size_hint_y=None,height=dp(48),spacing=dp(5))
    prefix=Label(text='R$',size_hint_x=None,width=dp(42),color=MUTED,font_size=dp(15))
    field=style_input(TextInput(text=str(value),hint_text='0,00',multiline=False,input_filter=None,input_type='number'))
    box.add_widget(prefix);box.add_widget(field)
    return box,field
def panel(content):
    outer=BoxLayout(orientation='vertical',padding=dp(13),size_hint_y=None,height=content.height+dp(26))
    with outer.canvas.before:
        Color(*CARD);r=RoundedRectangle(pos=outer.pos,size=outer.size,radius=[dp(16)])
    outer.bind(pos=lambda x,v:setattr(r,'pos',v),size=lambda x,v:setattr(r,'size',v))
    outer.add_widget(content);return outer

class FinanceApp(App):
    def build(self):
        Window.softinput_mode = "resize"
        self.title='Nexo Finanças'
        icon_path = Path(__file__).parent / 'nexo_icon_512.png'
        if icon_path.is_file():
            self.icon = str(icon_path)
        data_dir = Path(self.user_data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        self.db=sqlite3.connect(str(data_dir/'financas.db'))
        self.db.execute('CREATE TABLE IF NOT EXISTS transacoes (id INTEGER PRIMARY KEY, descricao TEXT NOT NULL, categoria TEXT NOT NULL, tipo TEXT NOT NULL, valor REAL NOT NULL, data TEXT NOT NULL)')
        cols=[r[1] for r in self.db.execute('PRAGMA table_info(transacoes)')]
        if 'pagamento' not in cols:self.db.execute("ALTER TABLE transacoes ADD COLUMN pagamento TEXT DEFAULT 'Outro'")
        if 'fixo_id' not in cols:self.db.execute('ALTER TABLE transacoes ADD COLUMN fixo_id INTEGER')
        self.db.execute('CREATE TABLE IF NOT EXISTS categorias (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, grupo TEXT NOT NULL, UNIQUE(nome,grupo))')
        self.db.execute('CREATE TABLE IF NOT EXISTS custos_fixos (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, valor REAL NOT NULL, ativo INTEGER DEFAULT 1)')
        self.db.execute('CREATE TABLE IF NOT EXISTS configuracao_salario (id INTEGER PRIMARY KEY CHECK(id=1), mensal REAL NOT NULL DEFAULT 0, quinzenal REAL NOT NULL DEFAULT 0)')
        self.db.execute('INSERT OR IGNORE INTO configuracao_salario(id,mensal,quinzenal) VALUES (1,0,0)')
        for category in ('Mercado','Lazer','Filhos','Carro','Pets'):
            self.db.execute("INSERT OR IGNORE INTO categorias(nome,grupo) VALUES (?,'Variavel')",(category,))
        self.db.commit()
        credito.initialize(self.db)
        planejamento.initialize(self.db)
        integracao_pluggy.initialize(self.db)
        movimentacoes.initialize(self.db)
        self.view_month=date.today().strftime('%Y-%m')
        self.money=money;self.parse_amount=parse_amount;self.label=label;self.button=button;self.input_field=input_field;self.money_field=money_field;self.panel=panel;self.spinner=spinner
        self.WHITE=WHITE;self.MUTED=MUTED;self.GREEN=GREEN;self.RED=RED
        self.root_box=BoxLayout(orientation='vertical')
        if platform=='ios':
            # Margem para o notch/Dynamic Island e a barra de gestos do iPhone.
            self.root_box.padding=[0,dp(44),0,dp(22)]
        # Botão "voltar" do Android (tecla 27): volta ao início em vez de fechar o app.
        Window.bind(on_keyboard=self._on_back_key)
        with self.root_box.canvas.before:
            Color(*BG);r=RoundedRectangle(pos=self.root_box.pos,size=self.root_box.size)
        self.root_box.bind(pos=lambda x,v:setattr(r,'pos',v),size=lambda x,v:setattr(r,'size',v))
        self.page='inicio'
        self.show_splash()
        Clock.schedule_once(self._safe_initial_refresh, 1.4)
        return self.root_box

    def _on_back_key(self, window, key, *args):
        if key==27 and getattr(self,'page','inicio')!='inicio':
            self.go('inicio')
            return True
        return False

    def _safe_initial_refresh(self, dt):
        try:
            self.refresh()
        except Exception:
            save_error('Falha ao carregar tela inicial')
            self.root_box.clear_widgets()
            self.root_box.add_widget(Label(text='Falha ao abrir Nexo.\nConsulte erro_nexo.txt', color=WHITE))
        # Sincronização automática com a Pluggy (em segundo plano, não trava a tela).
        try:
            integracao_pluggy.auto_sync(self)
        except Exception:
            save_error('Falha ao iniciar sincronização Pluggy')

    def show_splash(self):
        self.root_box.clear_widgets()
        splash = Path(__file__).parent / 'nexo_splash.png'
        if splash.is_file():
            self.root_box.add_widget(KivyImage(source=str(splash),fit_mode="contain"))
        else:
            self.root_box.add_widget(Label(text='NEXO\nSuas contas fazendo sentido.', font_size=dp(27), color=GREEN))

    def page_body(self,title):
        self.root_box.clear_widgets()
        sc=ScrollView();body=BoxLayout(orientation='vertical',padding=dp(18),spacing=dp(12),size_hint_y=None)
        body.bind(minimum_height=body.setter('height'))
        body.add_widget(label(title,23,WHITE,48,True))
        if self.page in ('inicio','custos','fixos','categorias','cartao','graficos','planejamento','movimentacoes'):
            body.add_widget(periodo.create(self))
        sc.add_widget(body);self.root_box.add_widget(sc)
        self.root_box.add_widget(navegacao.create(self))
        return body

    def show_costs_menu(self):
        body=self.page_body('CUSTOS  •  '+self.month_name(self.view_month))
        body.add_widget(label('Organize seus pagamentos e categorias.',14,MUTED,54))
        body.add_widget(menu_card('Custos fixos', 'Pagamentos e despesas mensais',
                                  lambda *_:self.go('fixos'),'custos_ativo.png'))
        body.add_widget(menu_card('Categorias', 'Organize seus gastos variáveis',
                                  lambda *_:self.go('categorias'),'graficos_ativo.png'))
        body.add_widget(menu_card('Planejamento', 'Limites e histórico de gastos',
                                  lambda *_:self.go('planejamento'),'inicio_ativo.png'))

    def show_settings(self):
        body=self.page_body('AJUSTES')
        body.add_widget(label('Personalize seu Nexo.',14,MUTED,54))
        body.add_widget(menu_card('Salários', 'Mensal e adiantamento quinzenal',
                                  lambda *_:self.go('salario'), 'custos_ativo.png'))
        cfg=integracao_pluggy.get_config(self.db)
        body.add_widget(menu_card('Integração Nubank', 'Conectado via Pluggy' if cfg['configurado'] else 'Importar conta e cartão automaticamente',
                                  lambda *_:self.go('pluggy'), 'cartao_ativo.png'))
        body.add_widget(label('Seus dados permanecem armazenados no aplicativo.',12,MUTED,60))

    def go(self,page):self.page=page;self.refresh()
    def refresh(self):
        if self.page=='custos':return self.show_costs_menu()
        if self.page=='ajustes':return self.show_settings()
        if self.page=='cartao':return credito.show(self)
        if self.page=='graficos':return graficos.show(self)
        if self.page=='planejamento':return planejamento.show(self)
        if self.page=='salario':return self.show_salary()
        if self.page=='pluggy':return integracao_pluggy.show(self)
        if self.page=='movimentacoes':return movimentacoes.show(self)
        if self.page=='fixos':return self.show_fixed()
        if self.page=='categorias':return self.show_categories()
        self.show_home()

    def shift_month(self, delta):
        self.view_month=periodo.add_month(self.view_month,delta)
        self.refresh()

    def selected_day(self):
        year,month=map(int,self.view_month.split('-'))
        return date(year,month,min(date.today().day,monthrange(year,month)[1])).isoformat()

    def salary_values(self):
        return self.db.execute('SELECT mensal,quinzenal FROM configuracao_salario WHERE id=1').fetchone()

    def show_salary(self):
        body=self.page_body('SALÁRIOS')
        body.add_widget(label('Configure os valores recebidos em cada período.',13,MUTED,48))
        mensal,quinzenal=self.salary_values()
        form=BoxLayout(orientation='vertical',spacing=dp(12),size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))
        form.add_widget(label('Salário mensal  •  dias 1 a 14',14,WHITE,34))
        box_m,field_m=money_field(str(mensal).replace('.',','))
        form.add_widget(box_m)
        form.add_widget(label('Adiantamento quinzenal  •  dia 15 em diante',14,WHITE,40))
        box_q,field_q=money_field(str(quinzenal).replace('.',','))
        form.add_widget(box_q)
        status=label('',12,RED,26);form.add_widget(status)
        def save(*_):
            try:
                m=parse_amount(field_m.text);q=parse_amount(field_q.text)
                if m<0 or q<0:raise ValueError()
            except (ValueError,OverflowError):
                status.text='Informe valores válidos (ex.: 2500,00).';return
            self.db.execute('UPDATE configuracao_salario SET mensal=?,quinzenal=? WHERE id=1',(m,q))
            self.db.commit();self.message('Salário atualizado','Valores salvos com sucesso.')
            self.refresh()
        form.add_widget(modern_button('Salvar salários',save,primary=True,height=54))
        # O painel precisa acompanhar a altura real do formulário.
        # Sem isso, os campos ultrapassam o cartão e sobrepõem o título.
        salary_panel = panel(form)
        form.bind(height=lambda widget, value: setattr(salary_panel, 'height', value + dp(26)))
        salary_panel.height = form.height + dp(26)
        body.add_widget(salary_panel)
        body.add_widget(label('Os salários não geram transações automaticamente.',12,MUTED,44))
        body.add_widget(label('Regra: 1–14 = mensal; 15–fim = quinzenal.',12,MUTED,44))

    def show_home(self):
        body=self.page_body('NEXO  •  FINANÇAS')
        brand=BoxLayout(size_hint_y=None,height=dp(60),spacing=dp(8))
        logo = Path(__file__).parent / 'nexo_logo_transparente.png'
        if logo.is_file():
            brand.add_widget(KivyImage(source=str(logo),size_hint_x=None,width=dp(58),fit_mode='contain'))
        brand.add_widget(label('Suas contas fazendo sentido.',14,MUTED,60))
        body.add_widget(brand)
        today=date.today();month=self.view_month
        body.add_widget(label('Periodo selecionado: '+self.month_name(month),13,MUTED,29))
        records=self.db.execute('SELECT tipo,valor,data FROM transacoes WHERE substr(data,1,7)=?',(month,)).fetchall()
        income=sum(v for t,v,d in records if t=='Receita')
        expense=sum(v for t,v,d in records if t=='Despesa')
        first=sum(v for t,v,d in records if t=='Despesa' and int(d[-2:])<=14)
        second=sum(v for t,v,d in records if t=='Despesa' and int(d[-2:])>=15)
        credit_first,credit_second=credito.paid_in_period(self.db,month)
        expense+=credit_first+credit_second
        first+=credit_first;second+=credit_second
        monthly,advance=self.salary_values()
        total_salary=monthly+advance
        summary=BoxLayout(orientation='vertical',size_hint_y=None,height=dp(164))
        for x in [label('Disponivel (salarios + outras receitas - gastos)',12,MUTED,36),label(money(total_salary+income-expense),26,WHITE,48,True),label('Salarios cadastrados: '+money(total_salary),13,GREEN),label('Outras receitas: '+money(income),13,GREEN),label('Despesas pagas/lancadas: '+money(expense),13,RED)]:summary.add_widget(x)
        body.add_widget(panel(summary))
        body.add_widget(movimentacoes.connected_accounts_panel(self))
        periods=BoxLayout(orientation='vertical',size_hint_y=None,height=dp(155))
        for x in [label('1 a 14: mensal - gastos do periodo',13,MUTED),label(money(monthly-first),19,WHITE,39,True),label('15 ao fim: quinzenal - gastos do periodo',13,MUTED),label(money(advance-second),19,WHITE,39,True)]:periods.add_widget(x)
        body.add_widget(panel(periods))
        body.add_widget(label('Saldo combinado dos dois salarios: '+money(total_salary-expense),14,WHITE,54,True))
        body.add_widget(label('Faturas pagas neste mes: '+money(credit_first+credit_second),13,MUTED,35))
        pend_fix,pend_bill=planejamento.pending(self.db,month)
        body.add_widget(label('Saldo previsto apos pendencias: '+money(total_salary+income-expense-pend_fix-pend_bill),16,GREEN if total_salary+income-expense>=pend_fix+pend_bill else RED,48,True))
        body.add_widget(modern_button('Planejamento e histórico  ›',lambda *_:self.go('planejamento')))
        fixed=sum(v for _,_,v,_ in planejamento.fixed_rows(self.db,month))
        paid=self.db.execute("""SELECT COALESCE(SUM(t.valor),0) FROM transacoes t JOIN custos_fixos f ON f.id=t.fixo_id
            WHERE f.ativo=1 AND t.tipo='Despesa' AND substr(t.data,1,7)=?""",(month,)).fetchone()[0]
        body.add_widget(label('Fixos previstos: '+money(fixed),14,WHITE,30))
        body.add_widget(label('Fixos pagos: '+money(paid)+'  |  Pendentes: '+money(pend_fix),13,MUTED,45))
        body.add_widget(icon_button('Quanto gastei?', 'resumo_gastos.png',
                                    self.show_variable_spending, primary=True, height=68))
        actions = BoxLayout(orientation='horizontal', spacing=dp(10),
                            size_hint_y=None, height=dp(64))
        actions.add_widget(icon_button('Nova transação', 'nova_transacao.png',
                                       self.new_transaction, height=64))
        actions.add_widget(icon_button('Ver gráficos', 'ver_graficos.png',
                                       lambda *_: self.go('graficos'), height=64))
        body.add_widget(actions)
        body.add_widget(movimentacoes.latest_panel(self,month))

    def show_variable_spending(self,*_):
        """Análise dos gastos variáveis, excluindo fixos e faturas."""
        body=self.page_body('QUANTO GASTEI?')
        month=self.view_month
        previous=periodo.add_month(month,-1)
        monthly,advance=self.salary_values()
        salary=float(monthly or 0)+float(advance or 0)
        current_month=date.today().strftime('%Y-%m')
        # No mês corrente, comparar o mesmo número de dias do mês anterior.
        cutoff=date.today().day if month==current_month else None
        def by_category(m):
            sql="""SELECT categoria,COALESCE(SUM(valor),0) FROM transacoes
                   WHERE substr(data,1,7)=? AND tipo='Despesa' AND fixo_id IS NULL"""
            params=[m]
            if cutoff is not None:
                year,mon=map(int,m.split('-'))
                last_day=monthrange(year,mon)[1]
                sql+=' AND CAST(substr(data,9,2) AS INTEGER)<=?'
                params.append(min(cutoff,last_day))
            sql+=' GROUP BY categoria ORDER BY SUM(valor) DESC'
            return {str(cat):float(amount) for cat,amount in self.db.execute(sql,params).fetchall()}
        current=by_category(month)
        old=by_category(previous)
        total=sum(current.values());old_total=sum(old.values())
        animated=[]
        def add(widget):
            widget.opacity=0
            body.add_widget(widget)
            animated.append(widget)
        add(label('Gastos variáveis • '+self.month_name(month),14,MUTED,42))
        add(label(money(total),25,WHITE,52,True))
        if salary>0:
            add(label(f'{100*total/salary:.1f}% do seu salário destinado a gastos variáveis'.replace('.',','),13,GREEN,50))
        add(label('COMPARAÇÃO COM '+self.month_name(previous).upper(),13,MUTED,42,True))
        if old_total>0:
            diff=total-old_total
            variation=100*diff/old_total
            verb='a mais' if diff>0 else 'a menos' if diff<0 else 'de diferença'
            color=RED if diff>0 else GREEN
            add(label(f'{money(abs(diff))} {verb} ({variation:+.1f}%)'.replace('.',','),17,color,54,True))
            add(label('Anterior: '+money(old_total)+'   |   Atual: '+money(total),13,MUTED,42))
        elif total>0:
            add(label('Sem gastos registrados no período anterior para comparar.',13,MUTED,60))
        else:
            add(label('Ainda não há gastos para comparar.',13,MUTED,48))
        if cutoff is not None:
            add(label('Comparação do dia 1 ao dia '+str(cutoff)+' em cada mês.',12,MUTED,42))
        if not current:
            add(label('Nenhum gasto variável registrado neste mês.',13,MUTED,60))
        else:
            add(label('CATEGORIAS DO MÊS',13,MUTED,42,True))
        for category,amount in sorted(current.items(),key=lambda item:-item[1]):
            content=BoxLayout(orientation='vertical',size_hint_y=None,height=dp(118),spacing=dp(3))
            content.add_widget(label(category+'  •  '+money(amount),16,WHITE,38,True))
            if salary>0:
                content.add_widget(label(f'Equivale a {100*amount/salary:.1f}% do seu salário'.replace('.',','),13,MUTED,32))
            else:
                content.add_widget(label('Cadastre o salário para ver a proporção.',12,MUTED,32))
            before=old.get(category,0.0)
            change=amount-before
            if before>0:
                trend=f'{money(abs(change))} '+('a mais' if change>0 else 'a menos' if change<0 else 'sem mudança')+' que no mês anterior'
            else:
                trend='Sem gasto registrado nessa categoria no mês anterior'
            content.add_widget(label(trend,12,RED if change>0 and before>0 else GREEN if change<0 else MUTED,44))
            add(panel(content))
        if current and old:
            increase=max(((cat, val-old.get(cat,0)) for cat,val in current.items()),key=lambda x:x[1])
            if increase[1]>0:
                add(label('Insight: '+increase[0]+' teve o maior aumento: '+money(increase[1])+'.',13,GREEN,65))
        add(label('Inclui somente despesas variáveis lançadas. Custos fixos e faturas de cartão ficam fora.',12,MUTED,70))
        add(modern_button('‹  Voltar ao início',lambda *_:self.go('inicio')))
        # Animação leve em sequência, sem bloquear a rolagem.
        for index, widget in enumerate(animated):
            Clock.schedule_once(lambda dt,w=widget: Animation(opacity=1,d=.65,t='out_quad').start(w), .07+index*.09)

    def show_fixed(self):
        body=self.page_body('CUSTOS FIXOS')
        body.add_widget(label('Cada mes tem pagamentos independentes. Edicoes de valor afetam apenas o mes selecionado.',13,MUTED,70))
        body.add_widget(modern_button('+  Cadastrar custo fixo',lambda *_:self.edit_fixed(),primary=True))
        items=planejamento.fixed_rows(self.db,self.view_month)
        month=self.view_month
        paid_ids={i for i,_,_,paid in items if paid}
        total=sum(v for _,_,v,_ in items)
        paid=sum(v for ident,_,v,_ in items if ident in paid_ids)
        body.add_widget(label('Previsto: '+money(total),16,WHITE,35,True))
        body.add_widget(label('Pago: '+money(paid)+' | Restante: '+money(total-paid),14,GREEN,40))
        for ident,name,value,_ in items:
            block=BoxLayout(orientation='vertical',size_hint_y=None,height=dp(96),spacing=dp(4))
            line=BoxLayout(size_hint_y=None,height=dp(42))
            check=CheckBox(active=ident in paid_ids,size_hint_x=None,width=dp(45))
            check.bind(active=lambda _,active,i=ident,n=name,v=value:self.toggle_fixed(i,n,v,active))
            line.add_widget(check);line.add_widget(label(name+'  -  '+money(value),15,WHITE,42,True))
            block.add_widget(line)
            block.add_widget(button('Editar custo',lambda _,i=ident,n=name,v=value:self.edit_fixed(i,n,v),40))
            body.add_widget(panel(block))

    def toggle_fixed(self,ident,name,value,checked):
        month=self.view_month
        found=self.db.execute('SELECT id FROM transacoes WHERE fixo_id=? AND substr(data,1,7)=?',(ident,month)).fetchone()
        if checked and not found:
            self.db.execute('INSERT INTO transacoes(descricao,categoria,tipo,valor,data,pagamento,fixo_id) VALUES (?,?,?,?,?,?,?)',(name,'Fixo','Despesa',value,self.selected_day(),'Pix/Debito',ident))
        elif not checked and found:
            self.db.execute('DELETE FROM transacoes WHERE id=?',(found[0],))
        else:return
        self.db.commit();self.refresh()

    def edit_fixed(self,ident=None,name='',value=0):
        form=BoxLayout(orientation='vertical',padding=dp(12),spacing=dp(8),size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))
        name_field=input_field('Nome: aluguel, agua, luz...',name)
        val_box,val_field=money_field(str(value).replace('.',',') if ident else '')
        status=label('',12,RED,25)
        for w in [name_field,val_box,status]:form.add_widget(w)
        pop=popup('Editar custo deste mês' if ident else 'Novo custo fixo',form,330 if ident else 280)
        def save(*_):
            try:v=parse_amount(val_field.text)
            except ValueError:status.text='Informe um valor valido.';return
            n=name_field.text.strip()
            if not n or v<=0:status.text='Nome e valor positivo obrigatorios.';return
            if ident:planejamento.update_month(self.db,ident,self.view_month,n,v)
            else:self.db.execute('INSERT INTO custos_fixos(nome,valor,inicio_mes) VALUES (?,?,?)',(n,v,self.view_month))
            self.db.commit();pop.dismiss();self.refresh()
        form.add_widget(button('Salvar',save,primary=True))
        if ident:
            def remove(*_):
                self.db.execute('UPDATE custos_fixos SET ativo=0 WHERE id=?',(ident,));self.db.commit();pop.dismiss();self.refresh()
            form.add_widget(button('Desativar custo',remove,danger=True))
        pop.open()

    def pay_fixed(self,ident,name,value):
        month=self.view_month
        already=self.db.execute("SELECT 1 FROM transacoes WHERE fixo_id=? AND substr(data,1,7)=? LIMIT 1",(ident,month)).fetchone()
        if already:return self.message('Ja registrado','Este custo fixo ja foi lancado neste mes.')
        form=BoxLayout(orientation='vertical',padding=dp(12),spacing=dp(8),size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))
        form.add_widget(label(name+' - '+money(value),16,WHITE,50))
        pay=spinner('Pix/Debito',('Pix/Debito','Dinheiro','Credito','Outro'))
        form.add_widget(pay)
        pop=popup('Registrar pagamento',form,250)
        def confirm(*_):
            self.db.execute('INSERT INTO transacoes(descricao,categoria,tipo,valor,data,pagamento,fixo_id) VALUES (?,?,?,?,?,?,?)',(name,'Fixo','Despesa',value,self.selected_day(),pay.text,ident))
            self.db.commit();pop.dismiss();self.refresh()
        form.add_widget(button('Confirmar pagamento',confirm,primary=True));pop.open()

    def show_categories(self):
        body=self.page_body('CATEGORIAS')
        body.add_widget(label('Crie e edite suas categorias de custos variaveis.',13,MUTED,50))
        body.add_widget(modern_button('+  Nova categoria',lambda *_:self.edit_category(),primary=True))
        cats=self.db.execute("SELECT id,nome FROM categorias WHERE grupo='Variavel' ORDER BY nome").fetchall()
        if not cats:body.add_widget(label('Nenhuma categoria criada.',14,MUTED,40))
        for ident,name in cats:
            block=BoxLayout(orientation='horizontal',size_hint_y=None,height=dp(48))
            block.add_widget(label(name,16,WHITE,48))
            edit=button('Editar',lambda _,i=ident,n=name:self.edit_category(i,n),44);edit.size_hint_x=None;edit.width=dp(96)
            block.add_widget(edit)
            body.add_widget(panel(block))

    def edit_category(self,ident=None,name=''):
        form=BoxLayout(orientation='vertical',padding=dp(12),spacing=dp(8),size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))
        field=input_field('Ex.: Mercado, Lazer, Cartao...',name)
        status=label('',12,RED,25)
        form.add_widget(field);form.add_widget(status)
        pop=popup('Categoria variável',form,240)
        def save(*_):
            n=field.text.strip()
            if not n:status.text='Informe um nome.';return
            try:
                if ident:
                    old=self.db.execute('SELECT nome FROM categorias WHERE id=?',(ident,)).fetchone()[0]
                    self.db.execute('UPDATE categorias SET nome=? WHERE id=?',(n,ident))
                    self.db.execute("UPDATE transacoes SET categoria=? WHERE categoria=? AND tipo='Despesa' AND fixo_id IS NULL",(n,old))
                else:self.db.execute("INSERT INTO categorias(nome,grupo) VALUES (?,'Variavel')",(n,))
                self.db.commit()
            except sqlite3.IntegrityError:status.text='Categoria ja existe.';return
            pop.dismiss();self.refresh()
        form.add_widget(button('Salvar',save,primary=True));pop.open()

    def new_transaction(self,*_):
        # Modal escuro com botão Salvar fixo, fora da área rolável.
        outer=BoxLayout(orientation='vertical',spacing=dp(10),padding=[dp(15),dp(12),dp(15),dp(12)])
        with outer.canvas.before:
            Color(*BG)
            back=RoundedRectangle(pos=outer.pos,size=outer.size,radius=[dp(14)])
        outer.bind(pos=lambda w,v:setattr(back,'pos',v),size=lambda w,v:setattr(back,'size',v))

        form=BoxLayout(orientation='vertical',spacing=dp(8),size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))
        form.add_widget(label('Despesa  •  '+self.month_name(self.view_month),12,MUTED,28))
        form.add_widget(label('Valor',13,MUTED,26))
        amount_box,amount=money_field()
        amount.write_tab=False
        form.add_widget(amount_box)
        form.add_widget(label('Categoria',13,MUTED,26))
        categories=[x[0] for x in self.db.execute(
            "SELECT nome FROM categorias WHERE grupo='Variavel' ORDER BY nome")]
        cat=spinner(categories[0] if categories else 'Sem categoria',
                    categories or ['Sem categoria'])
        form.add_widget(cat)
        status=label('',12,RED,28)
        form.add_widget(status)
        scroll=ScrollView(do_scroll_x=False,bar_width=dp(3))
        scroll.add_widget(form)
        outer.add_widget(scroll)

        footer=BoxLayout(size_hint_y=None,height=dp(52),spacing=dp(9))
        pop=Popup(title='Nova transação',content=outer,
                  size_hint=(.94,None),height=min(dp(390),Window.height*.77),
                  separator_color=GREEN,background_color=POPUP_BG,
                  title_color=WHITE,title_size=dp(16))
        footer.add_widget(modern_button('Cancelar',lambda *_:pop.dismiss(),height=52))
        def save(*_):
            try:
                v=parse_amount(amount.text)
            except (ValueError,OverflowError):
                status.text='Informe um valor válido.';return
            if not (0<v<1e12):
                status.text='Informe um valor positivo válido.';return
            category=cat.text
            try:
                self.db.execute(
                    'INSERT INTO transacoes(descricao,categoria,tipo,valor,data,pagamento) VALUES (?,?,?,?,?,?)',
                    (category,category,'Despesa',v,self.selected_day(),'Outro'))
                self.db.commit()
            except Exception:
                save_error('Falha ao salvar transacao')
                status.text='Erro ao salvar. Consulte erro_nexo.txt.';return
            pop.dismiss();self.refresh()
        footer.add_widget(modern_button('Salvar',save,primary=True,height=52))
        outer.add_widget(footer)
        pop.open()

    def month_name(self,month):
        names=['Janeiro','Fevereiro','Março','Abril','Maio','Junho','Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
        y,m=map(int,month.split('-'))
        return f'{names[m-1]} de {y}'

    def form(self):
        form=BoxLayout(orientation='vertical',padding=dp(12),spacing=dp(7),size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))
        return form

    def popup(self,title,form,height):
        # Scroll interno para campos nao ficarem inacessiveis com teclado Android.
        return popup(title,form,height)

    def message(self,title,msg):
        form=self.form();form.add_widget(label(msg,14,WHITE,70))
        pop=popup(title,form,230)
        form.add_widget(button('OK',lambda *_:pop.dismiss(),primary=True));pop.open()
    def on_stop(self):
        if hasattr(self,'db'):self.db.close()

def save_error(contexto):
    """Salva erros em arquivo acessível ao lado do main_nexo.py."""
    msg = contexto + "\n" + traceback.format_exc()
    try:
        app = App.get_running_app()
        path = (Path(app.user_data_dir) if app else Path(__file__).resolve().parent) / 'erro_nexo.txt'
        with path.open('a', encoding='utf-8') as f:
            f.write('\n--- NEXO ---\n' + msg + '\n')
    except Exception:
        pass
    print(msg, file=sys.stderr, flush=True)

if __name__ == '__main__':
    try:
        FinanceApp().run()
    except Exception:
        save_error('Falha na inicialização/execução')
        raise

