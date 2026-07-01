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
- Проверяет, что `installer.py` существует в том же каталоге.
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

> Часовой пояс и учётная запись администратора (ID `0001`, 6-значный PIN, необязательный e-mail) настраиваются в приложении через мастер первичной настройки и хранятся в базе данных — не в `.env`.

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
- Инсталлятор записывает `POS_DOCKER_AUTH_FILE` в `.env` с абсолютным Linux-путём к этому файлу.
- GUI не показывает это поле для ручного ввода; это техническое значение управляется инсталлятором.
- Compose больше не создаёт этот путь автоматически; если файл отсутствует или является папкой, повторите Docker Login в инсталляторе.
- Служба резервного копирования не имеет отдельного логина и опубликованного порта — управляется из админ-панели POS (Настройки ▸ Резервные копии), с правом `system.backup`.

### Шаг 3 — Развёртывание

| Поле | Назначение |
|---|---|
| Sudo-пароль (условно) | Появляется только если sudo-пароль не был получен на шаге 2/из состояния. Нужен для финальных Docker-операций. |
| Показать пароль (чекбокс) | Только переключение видимости. |

На этом шаге также показывается сводка только для чтения (API URL, GHCR user, app/port/db/image значения) и live-лог развёртывания.

---

## Что автоматизирует инсталлятор

- Вызывает `provision.py` и создаёт/обновляет `.env`.
- Патчит ключи деплоя в `.env` (`IMAGE_*`, `DEPLOYMENT_REPO`, `HOST_COMPOSE_PROJECT_DIR`).
- Выполняет GHCR login и сохраняет bridge-файл учётных данных для обновлятора.
- Сеть `pos-network` создаётся автоматически через Docker Compose на основе `docker-compose.prod.yml` — отдельный шаг создания не требуется.
- Запускает `docker compose pull` с индикатором прогресса (вывод буферизуется внутри, построчный лог не отображается) и `docker compose up -d` с live-логом.
- Сохраняет лог развёртывания в `logs/deploy-<timestamp>.log`.

---

## Ручная установка (краткий резервный сценарий)

Используйте только если GUI недоступен.

1. Выполните вход в GHCR:

```bash
export GHCR_USER="<ваш-ghcr-пользователь>"
export GHCR_TOKEN="<ваш-ghcr-readonly-токен>"
echo "$GHCR_TOKEN" | sudo docker login ghcr.io -u "$GHCR_USER" --password-stdin
python3 -c 'import base64,json,os,pathlib; p=pathlib.Path.home()/".docker"/"pos-auth.json"; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps({"auths":{"ghcr.io":{"auth":base64.b64encode((os.environ["GHCR_USER"]+":"+os.environ["GHCR_TOKEN"]).encode()).decode()}}}, indent=2)+"\n"); p.chmod(0o600)'
```

Файл `pos-auth.json` — это bridge-файл для обновлятора в WSL/Docker Desktop. Он должен быть обычным файлом, а не папкой.

2. Выполните провизию `.env`:

```bash
python3 provision.py --token <ONE_TIME_PROVISIONING_TOKEN> --api-url <LEGISELL_BACKEND_URL>
```

3. Проверьте, что в `.env` корректны минимум эти значения:

```dotenv
IMAGE_BACKEND=ghcr.io/<org>/pos-backend:<tag>
IMAGE_FRONTEND=ghcr.io/<org>/pos-frontend:<tag>
IMAGE_IMAGE_SERVICE=ghcr.io/<org>/pos-image-service:<tag>
IMAGE_UPDATER=ghcr.io/<org>/pos-updater:<tag>
IMAGE_BACKUP=ghcr.io/<org>/pos-backup:<tag>
DEPLOYMENT_REPO=<org>/pos-deployment
HOST_COMPOSE_PROJECT_DIR=/absolute/path/to/pos-deployment
POS_DOCKER_AUTH_FILE=/home/<user>/.docker/pos-auth.json
```

Часовой пояс и учётная запись администратора задаются позже в браузере через мастер первичной настройки.

Для `POS_DOCKER_AUTH_FILE` используйте абсолютный Linux-путь; не используйте `~` в `.env`. При установке через GUI это значение записывает сам инсталлятор.

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
