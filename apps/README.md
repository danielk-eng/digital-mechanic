# apps/

Нативные обёртки стенда «Цифровой механик»: Capacitor (Android APK) и Electron (Windows zip, Linux AppImage).

Как ставить и что лежит в выдаче — в [ПРИЛОЖЕНИЯ.md](ПРИЛОЖЕНИЯ.md).

- `android/` — проект Capacitor, `www/` со сплэшем и офлайн-экраном, правки натива в `native/` (накладывает `scripts/patch-android.py`).
- `desktop/` — Electron, адрес сервера в меню «Файл».
- `scripts/build-android.sh`, `scripts/build-desktop.sh` — сборка.

Ключ подписи APK и пароль в git не кладём: только переменные `MECHANIC_KEYSTORE` и `MECHANIC_KEYSTORE_PASSFILE`.
