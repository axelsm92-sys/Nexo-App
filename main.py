"""Ponto de entrada do Nexo Finanças no Android."""
from main_nexo import FinanceApp, save_error

if __name__ == '__main__':
    try:
        FinanceApp().run()
    except Exception:
        save_error('Falha na inicialização/execução')
        raise
