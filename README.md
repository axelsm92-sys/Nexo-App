# Nexo Finanças — Alpha 0.1

Aplicativo pessoal de finanças em Python/Kivy/SQLite, para Android e iOS.

## Estrutura obrigatória do repositório
O GitHub só encontra os workflows se a pasta `.github` estiver na **raiz** do repositório.
Na raiz devem ficar diretamente: `main.py`, `buildozer.spec`, os demais `.py`, as imagens `.png` e a pasta `.github`.
Não envie uma pasta intermediária (ex.: `Nexo-main/Nexo-main/...`), nem `__pycache__` ou `.spyproject`.

## Android — gerar APK no GitHub
1. Abra **Actions** > **Gerar APK Nexo Alpha** > **Run workflow** (também roda a cada push na `main`).
2. Ao terminar com sucesso, baixe o artefato **Nexo-Alpha-0.1-Android** e extraia o `.apk`.
3. No celular, permita "instalar apps de fontes desconhecidas" e instale.

A primeira compilação baixa Android SDK/NDK e leva de 30 a 60 minutos; as seguintes usam cache.
Se falhar, baixe o artefato **log-erro-android** ou copie o final do log da etapa **Compilar APK de testes**.

Observação: o Buildozer não funciona direto no Windows. Para compilar no seu PC, use o WSL2 (Ubuntu) ou o GitHub Actions.

## iOS — gerar app no GitHub
1. Abra **Actions** > **Gerar app iOS Nexo Alpha** > **Run workflow** (roda em macOS; consome mais minutos do GitHub).
2. Baixe o artefato **Nexo-Alpha-0.1-iOS**: ele contém um `.ipa` **sem assinatura**.
3. Para instalar no iPhone, o `.ipa` precisa ser assinado:
   - Sem Mac: use Sideloadly ou AltStore com seu Apple ID gratuito (a assinatura vale 7 dias e precisa ser renovada).
   - Com Mac: rode os mesmos comandos do workflow (`toolchain build python3 kivy` e `toolchain create Nexo pasta_do_app`), abra o `.xcodeproj` no Xcode, escolha seu time em *Signing & Capabilities* e rode no aparelho.
   - Para TestFlight/App Store é necessária a conta Apple Developer (paga).

## Privacidade e dados
O banco `financas.db` é criado na pasta privada do aplicativo no primeiro uso; não há banco de dados incluído neste pacote. Não envie bancos pessoais ao GitHub.

## Avisos
- O identificador `br.com.nexofinancas.nexofinancas` é provisório; defina o identificador definitivo antes de distribuir atualizações de longo prazo.
- Desinstalar o app ou limpar seu armazenamento pode apagar os dados.
- `android.api = 34` gera um APK estável para instalação direta. Para publicar na Google Play será preciso subir para 35/36 e revisar o layout (modo edge-to-edge).
