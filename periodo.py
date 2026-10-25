"""Seletor de mês do Nexo, compartilhado entre as telas."""
from kivy.metrics import dp
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label


def add_month(ym, delta):
    year, month = map(int, ym.split('-'))
    index = year * 12 + month - 1 + delta
    new_year, zero_month = divmod(index, 12)
    return f'{new_year:04d}-{zero_month + 1:02d}'


def create(app):
    bar=BoxLayout(size_hint_y=None,height=dp(64),spacing=dp(8),padding=dp(7))
    with bar.canvas.before:
        Color(.11,.15,.21,1)
        bg=RoundedRectangle(pos=bar.pos,size=bar.size,radius=[dp(16)])
    bar.bind(pos=lambda w,v:setattr(bg,'pos',v),size=lambda w,v:setattr(bg,'size',v))
    def arrow(symbol,delta):
        btn=Button(text=symbol,size_hint_x=None,width=dp(46),
                   background_normal='',background_down='',background_color=(0,0,0,0),
                   color=(.97,.98,.99,1),font_size=dp(22))
        with btn.canvas.before:
            Color(.19,.25,.32,1)
            rect=RoundedRectangle(pos=btn.pos,size=btn.size,radius=[dp(11)])
        btn.bind(pos=lambda w,v:setattr(rect,'pos',v),size=lambda w,v:setattr(rect,'size',v))
        btn.bind(on_release=lambda *_:app.shift_month(delta))
        return btn
    bar.add_widget(arrow('<',-1))
    center=BoxLayout(orientation='vertical')
    center.add_widget(Label(text='PERÍODO SELECIONADO',font_size=dp(9),color=(.62,.68,.74,1)))
    center.add_widget(Label(text=app.month_name(app.view_month),font_size=dp(16),
                            bold=True,color=(.97,.98,.99,1),shorten=True))
    bar.add_widget(center)
    bar.add_widget(arrow('>',1))
    return bar
