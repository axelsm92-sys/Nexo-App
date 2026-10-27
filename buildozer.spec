[app]
title = Nexo Finanças
package.name = nexofinancas
package.domain = br.com.nexofinancas
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas
source.exclude_dirs = .git,.github,__pycache__,.kivy,bin,.buildozer,.spyproject,.venv-pc
version = 0.1.0

# openssl/certifi: conexões HTTPS (Pluggy).
# sqlite3 é obrigatório: o app usa "import sqlite3" e, no Android,
# o módulo só existe no APK se a receita sqlite3 for compilada.
# Versões fixas evitam que o build quebre quando sai uma versão nova.
requirements = python3,kivy==2.3.1,sqlite3,openssl,certifi

orientation = portrait
fullscreen = 0
icon.filename = nexo_icon_512.png
presplash.filename = nexo_splash.png
android.presplash_color = #0F172A

# ---------------- Android ----------------
# INTERNET: necessária para a integração com a Pluggy (Nubank).
android.permissions = INTERNET
android.archs = arm64-v8a,armeabi-v7a
# API 34: estável com o bootstrap SDL2 (a API 35 força o modo "edge-to-edge"
# do Android 15 e a interface fica por baixo da barra de status).
# Para publicar na Google Play, suba para 35/36 e teste o layout.
android.api = 34
android.minapi = 24
android.ndk = 28c
android.ndk_api = 24
android.accept_sdk_license = True

# python-for-android do branch develop (commit fixo definido em P4A_COMMIT
# no workflow .github/workflows/android.yml). A tag v2026.05.09 falha com
# "charset_normalizer ... android_24_arm64_v8a.whl is not a supported wheel".
# Para compilar fora do GitHub, rode antes os mesmos comandos da etapa
# "Baixar python-for-android" do workflow.
p4a.source_dir = ~/p4a-nexo

# ---------------- iOS ----------------
# O build iOS é feito pelo workflow .github/workflows/ios.yml (kivy-ios),
# pois só funciona em macOS com Xcode.
ios.codesign.allowed = false

[buildozer]
log_level = 2
warn_on_root = 1
