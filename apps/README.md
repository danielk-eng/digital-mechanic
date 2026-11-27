# apps/

Нативные обёртки стенда «Цифровой механик»: Capacitor (Android APK) и Electron (Windows zip, Linux AppImage).

Как ставить и что лежит в выдаче — в [ПРИЛОЖЕНИЯ.md](ПРИЛОЖЕНИЯ.md).

- `android/` — проект Capacitor, `www/` со сплэшем и офлайн-экраном, правки натива в `native/` (накладывает `scripts/patch-android.py`); production по умолчанию открывает mobile shell `/m`.
- `desktop/` — Electron, адрес сервера в меню «Файл»; production по умолчанию открывает desktop shell `/desktop`.
- `scripts/build-android.sh`, `scripts/build-desktop.sh` — сборка.

Ключ подписи APK и пароль в git не кладём: только переменные `MECHANIC_KEYSTORE` и `MECHANIC_KEYSTORE_PASSFILE`.
