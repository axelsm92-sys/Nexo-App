"""Nexo Finanças - barra inferior V7.2, com ícones PNG locais.

Interface pública preservada: create(app) retorna um widget Kivy.
"""
from pathlib import Path

from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.image import Image
from kivy.uix.label import Label

ITEMS = [
    ("Início", "inicio"),
    ("Custos", "custos"),
    ("Cartão", "cartao"),
    ("Gráficos", "graficos"),
    ("Ajustes", "ajustes"),
]
ICONS = Path(__file__).resolve().parent / "icones"
BG = (0.075, 0.105, 0.155, 1)
ACTIVE_BG = (0.12, 0.22, 0.25, 1)
ACTIVE_TEXT = (0.10, 0.83, 0.61, 1)
NORMAL_TEXT = (0.65, 0.71, 0.77, 1)


class NavigationItem(ButtonBehavior, BoxLayout):
    """Botão inteiro clicável, sem sobreposição de FloatLayouts."""

    def __init__(self, title, page, selected, app, **kwargs):
        super().__init__(
            orientation="vertical",
            size_hint=(1, 1),
            spacing=dp(2),
            padding=[dp(2), dp(5), dp(2), dp(5)],
            **kwargs,
        )
        self.page = page
        self.app_ref = app

        with self.canvas.before:
            Color(*(ACTIVE_BG if selected else BG))
            self.background_rect = RoundedRectangle(
                pos=self.pos, size=self.size, radius=[dp(9)]
            )
        self.bind(pos=self._update_background, size=self._update_background)

        suffix = "_ativo" if selected else "_normal"
        icon_path = ICONS / f"{page}{suffix}.png"
        if icon_path.is_file():
            self.add_widget(
                Image(
                    source=str(icon_path),
                    size_hint=(1, None),
                    height=dp(32),
                    fit_mode="contain",
                )
            )
        else:
            self.add_widget(
                Label(
                    text=title[:1],
                    font_size=dp(22),
                    size_hint=(1, None),
                    height=dp(32),
                    color=ACTIVE_TEXT if selected else NORMAL_TEXT,
                )
            )

        self.add_widget(
            Label(
                text=title,
                font_size=dp(10),
                color=ACTIVE_TEXT if selected else NORMAL_TEXT,
                size_hint=(1, 1),
                halign="center",
                valign="middle",
                shorten=True,
            )
        )

    def _update_background(self, *_):
        self.background_rect.pos = self.pos
        self.background_rect.size = self.size

    def on_release(self):
        self.app_ref.go(self.page)


def create(app):
    """Cria a barra inferior mantendo a API da versão anterior."""
    bar = BoxLayout(
        orientation="horizontal",
        size_hint_y=None,
        height=dp(76),
        spacing=dp(3),
        padding=[dp(4), dp(4), dp(4), dp(4)],
    )
    with bar.canvas.before:
        Color(*BG)
        background = RoundedRectangle(pos=bar.pos, size=bar.size)

    def update_bar_background(*_):
        background.pos = bar.pos
        background.size = bar.size

    bar.bind(pos=update_bar_background, size=update_bar_background)

    active = {
        "fixos": "custos",
        "categorias": "custos",
        "salario": "ajustes",
    }.get(app.page, app.page)

    for title, page in ITEMS:
        bar.add_widget(
            NavigationItem(title, page, page == active, app)
        )

    return bar
