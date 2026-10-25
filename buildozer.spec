[app]
title = Nexo Finanças
package.name = nexofinancas
package.domain = br.com.nexofinancas
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,atlas
source.exclude_dirs = .git,.github,__pycache__,.kivy,bin,.buildozer,.spyproject
version = 0.1.0

# sqlite3 é obrigatório: o app usa "import sqlite3" e, no Android,
# o módulo só existe no APK se a receita sqlite3 for compilada.
# Versões fixas evitam que o build quebre quando sai uma versão nova.
requirements = python3,kivy==2.3.1,sqlite3

orientation = portrait
fullscreen = 0
icon.filename = nexo_icon_512.png
presplash.filename = nexo_splash.png
android.presplash_color = #0F172A

# ---------------- Android ----------------
android.permissions =
android.archs = arm64-v8a,armeabi-v7a
# API 34: estável com o bootstrap SDL2 (a API 35 força o modo "edge-to-edge"
# do Android 15 e a interface fica por baixo da barra de status).
# Para publicar na Google Play, suba para 35/36 e teste o layout.
android.api = 34
android.minapi = 24
android.ndk = 28c
android.ndk_api = 24
android.accept_sdk_license = True

# python-for-android fixado na versão estável v2026.05.09
# (sem isso o buildozer baixa o "master", que muda a qualquer momento).
p4a.branch = v2026.05.09

# ---------------- iOS ----------------
# O build iOS é feito pelo workflow .github/workflows/ios.yml (kivy-ios),
# pois só funciona em macOS com Xcode.
ios.codesign.allowed = false

[buildozer]
log_level = 2
warn_on_root = 1
