# POS-система — Руководство по развёртыванию в продакшн

Развёртывание этой системы рассчитано на полностью автоматическую установку через GUI-инсталлятор.
Ручной вариант нужен только как резервный и кратко описан в конце.

---

## Рекомендуемый путь: полностью автоматическая установка (`installer.py`)

Запускайте через скрипт-лаунчер:

```bash
chmod +x start-installer.sh
./start-installer.sh
```

Опционально, для быстрого повторного деплоя:

```bash
./start-installer.sh --skip-setup
```

Что `start-installer.sh` делает перед запуском GUI:
- Проверяет наличие Python 3.10+.
- Проверяет наличие модуля `tkinter`.
- Проверяет, что `installer.py` существует.
- Запускает мастер и передаёт ему аргументы CLI (включая `--skip-setup`).

---

## Предварительные требования

- Linux-сервер с Docker и Docker Compose.
Необходимые входные данные от разработчика/дистрибьютора или администратора Legisell:
- Одноразовый токен провизии (OTPK).
- URL бэкенда Legisell.
- Имя пользователя GHCR и токен (read:packages).
- Теги Docker-образов (`IMAGE_*`).

---

## Справочник полей GUI

### Шаг 1 — Данные лицензии и теги образов

| Поле | Назначение |
|---|---|
| Данные уже получены от Legisell (чекбокс) | Пропускает вызов API провизии. Поля OTPK и URL блокируются. Нужен существующий `.env`; патчатся только изменённые теги/репозиторий/путь. |
| Токен провизии (OTPK) | Одноразовый токен, который `provision.py` использует для получения секретов тенанта из Legisell. |
| URL Legisell Backend | Базовый URL API для запроса провизии. |
| IMAGE_BACKEND | Тег образа backend, записывается в `.env`. |
| IMAGE_FRONTEND | Тег образа frontend, записывается в `.env`. |
| IMAGE_IMAGE_SERVICE | Тег image-service, записывается в `.env`. |
| IMAGE_UPDATER | Тег sidecar-обновлятора, записывается в `.env`. |
| IMAGE_BACKUP | Тег backup-sidecar, записывается в `.env`. |
| DEPLOYMENT_REPO | Репозиторий в формате `org/pos-deployment`; используется для подсказок по релизам/тегам и сохраняется в `.env`. |
| Путь к pos-deployment (`HOST_COMPOSE_PROJECT_DIR`) | Абсолютный путь к каталогу развёртывания на хосте; нужен для self-update обновлятора и корректных bind-mount путей. |
| Часовой пояс (`TZ`) | IANA timezone для контейнеров (логи, расписания, метки времени). |

Примечания:
- Если `.env` уже существует, релевантные поля заполняются автоматически.
- Для `DEPLOYMENT_REPO` автоматически запрашиваются последние теги (информационная подсказка).

### Шаг 2 — Docker Login

| Поле | Назначение |
|---|---|
| GHCR Login уже выполнен (чекбокс) | Пропускает `docker login`, если учётные данные GHCR уже есть в `~/.docker`. |
| Пользователь GHCR | Используется в `docker login ghcr.io`. |
| Токен GHCR / PAT | Используется как пароль реестра (`read:packages`). |
| Sudo-пароль | Нужен для выполнения Docker-команд через `sudo`. |
| Показать токен / пароль (чекбоксы) | Только переключение видимости, значения не меняют. |

Примечания:
- При успешном входе создаётся `~/.docker/pos-auth.json` для GHCR-пулла образов обновлятором.
- `BACKUP_UI_PASSWORD` приходит из Legisell Provisioning (`provision.py`) и автоматически записывается в `.env`.
- `BACKUP_UI_USER` фиксирован и равен `admin`.

### Шаг 3 — Развёртывание

| Поле | Назначение |
|---|---|
| Sudo-пароль (условно) | Появляется только если sudo-пароль не был получен на шаге 2/из состояния. Нужен для финальных Docker-операций. |
| Показать пароль (чекбокс) | Только переключение видимости. |

На этом шаге также показывается сводка только для чтения (API URL, GHCR user, app/port/db/image значения) и live-лог развёртывания.

---

## Что автоматизирует инсталлятор

- Вызывает `provision.py` и создаёт/обновляет `.env`.
- Записывает `BACKUP_UI_PASSWORD` из Legisell Provisioning в `.env` и использует `BACKUP_UI_USER=admin`.
- Патчит ключи деплоя в `.env` (`IMAGE_*`, `DEPLOYMENT_REPO`, `HOST_COMPOSE_PROJECT_DIR`, `TZ`).
- Выполняет GHCR login и сохраняет bridge-файл учётных данных для обновлятора.
- Гарантирует наличие сети `pos-network`.
- Запускает `docker compose pull` и `docker compose up -d` с live-логом.
- Сохраняет лог развёртывания в `logs/deploy-<timestamp>.log`.

---

## Ручная установка (краткий резервный сценарий)

Используйте только если GUI недоступен.

1. Выполните вход в GHCR:

```bash
export GHCR_USER="<ваш-ghcr-пользователь>"
export GHCR_TOKEN="<ваш-ghcr-readonly-токен>"
echo "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin
```

2. Выполните провизию `.env`:

```bash
python3 provision.py --token <ONE_TIME_PROVISIONING_TOKEN> --api-url <LEGISELL_BACKEND_URL>
```

3. Проверьте, что в `.env` корректны минимум эти значения.
`BACKUP_UI_PASSWORD` должен приходить из ответа provisioning, а `BACKUP_UI_USER` должен быть `admin`:

```dotenv
IMAGE_BACKEND=ghcr.io/<org>/pos-backend:<tag>
IMAGE_FRONTEND=ghcr.io/<org>/pos-frontend:<tag>
IMAGE_IMAGE_SERVICE=ghcr.io/<org>/pos-image-service:<tag>
IMAGE_UPDATER=ghcr.io/<org>/pos-updater:<tag>
IMAGE_BACKUP=ghcr.io/<org>/pos-backup:<tag>
DEPLOYMENT_REPO=<org>/pos-deployment
HOST_COMPOSE_PROJECT_DIR=/absolute/path/to/pos-deployment
TZ=Europe/Berlin
BACKUP_UI_USER=admin
BACKUP_UI_PASSWORD=<from-provisioning-secret>
```

4. Запустите сервисы:

```bash
sudo docker network create --driver bridge pos-network || true
sudo docker compose -f docker-compose.prod.yml pull
sudo docker compose -f docker-compose.prod.yml up -d
```

5. Проверьте состояние:

```bash
sudo docker compose -f docker-compose.prod.yml ps
sudo docker compose -f docker-compose.prod.yml logs -f
```
