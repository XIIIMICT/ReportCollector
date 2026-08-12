# Корпоративный Keycloak: первый запуск

Для локальной проверки без доступного Keycloak задайте `KEYCLOAK_ENABLED=false`.
В этом режиме `/api/auth/login` показывает простую браузерную форму для e-mail и пароля.
На продуктивном окружении `KEYCLOAK_ENABLED` должен быть равен `true`; форма предназначена только для тестов.

## Что подготовить вне приложения

1. Назначить машине DNS-имя и направить на неё запись DNS.
2. Настроить reverse proxy на HTTPS. Сертификат должен содержать DNS-имя приложения в SAN, а клиентские машины должны доверять цепочке сертификатов.
3. Передать администраторам Keycloak точный redirect URI: `https://<dns-имя>/api/auth/callback`.
4. Попросить создать confidential client со стандартным Authorization Code Flow, PKCE S256 и scopes `openid profile email`.
5. Получить client secret. Не добавлять его в Git и не передавать через логи.

Рутокен обрабатывается браузером и корпоративным Keycloak. ReportCollector не обращается к устройству напрямую и получает только результат OIDC-аутентификации.

## Переменные окружения

Минимальная конфигурация для корпоративного провайдера:

```dotenv
KEYCLOAK_ENABLED=true
AUTH_APP_ORIGIN=https://<dns-имя>
AUTH_COOKIE_SECURE=true
AUTH_SECRET=<случайная строка не короче 32 символов>

AUTH_KEYCLOAK_PUBLIC_ISSUER=https://portal.ruschem.ru/auth/realms/h2-group
AUTH_KEYCLOAK_INTERNAL_ISSUER=https://portal.ruschem.ru/auth/realms/h2-group
AUTH_KEYCLOAK_DOCKER_INTERNAL_ISSUER=https://portal.ruschem.ru/auth/realms/h2-group
AUTH_KEYCLOAK_TOKEN_ISSUER=<точное значение claim iss в ID token>
AUTH_KEYCLOAK_CLIENT_ID=ReportCollector
AUTH_KEYCLOAK_CLIENT_SECRET=<секрет confidential client>
AUTH_KEYCLOAK_SCOPES=openid profile email
AUTH_KEYCLOAK_SIGNING_ALGORITHMS=RS256
AUTH_KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD=client_secret_basic
```

`AUTH_KEYCLOAK_PUBLIC_ISSUER` используется для перехода браузера. `AUTH_KEYCLOAK_INTERNAL_ISSUER` задаёт адрес, доступный при запуске фронтенда на хосте, а `AUTH_KEYCLOAK_DOCKER_INTERNAL_ISSUER` - из контейнера. Для внешнего Keycloak эти три значения обычно совпадают.

`AUTH_KEYCLOAK_TOKEN_ISSUER` должен буквально совпадать с `iss` подписанного ID token. В предоставленном примере `iss` записан без `https://`, хотя OpenID Configuration содержит полный HTTPS URL; это расхождение нужно подтвердить у администраторов. Переменная позволяет корректно проверить оба варианта без ослабления проверки подписи.

### Пример реальных значений (portal.ruschem.ru)

```dotenv
AUTH_KEYCLOAK_PUBLIC_ISSUER=https://portal.ruschem.ru/auth/realms/h2-group
AUTH_KEYCLOAK_INTERNAL_ISSUER=https://portal.ruschem.ru/auth/realms/h2-group
AUTH_KEYCLOAK_DOCKER_INTERNAL_ISSUER=https://portal.ruschem.ru/auth/realms/h2-group
# Критично: в токене iss = "portal.ruschem.ru/auth/realms/h2-group" (без https://)
AUTH_KEYCLOAK_TOKEN_ISSUER=portal.ruschem.ru/auth/realms/h2-group
AUTH_KEYCLOAK_CLIENT_ID=ReportCollector
AUTH_KEYCLOAK_SIGNING_ALGORITHMS=RS256
AUTH_KEYCLOAK_TOKEN_ENDPOINT_AUTH_METHOD=client_secret_basic
```

Значения `email` и `preferred_username` могут приходить с разных доменов (например, `evtushenko.sn@ruschem.ru` и `evtushenko.sn@h2-group.ru`). ReportCollector использует `email` как основной ключ пользователя при первом входе.

Если корпоративный CA отсутствует в стандартном системном хранилище, PEM-файл нужно установить на хосте в `/etc/ssl/certs` и задать его контейнерный путь:

```dotenv
NODE_EXTRA_CA_CERTS=/etc/ssl/certs/<corporate-ca>.pem
```

Сертификат reverse proxy и доверенный CA решают разные задачи: первый предъявляется браузеру, второй нужен приложению для проверки HTTPS Keycloak.

Для корневого тестового Nginx сертификат и ключ задаются путями внутри контейнера:

```dotenv
NGINX_TLS_CERT_PATH=/host-certs/<certificate>.pem
NGINX_TLS_KEY_PATH=/host-private/<private-key>.pem
```

Пустые значения создают временный самоподписанный сертификат и подходят только для локальной проверки.

## Порядок первого запуска

1. Заполнить `.env` по `.env.example` и проверить доступность PostgreSQL, Loki и Keycloak с машины.
2. Выполнить миграции: `just migrate`.
3. До создания пользователей выполнить сидирование: `just seed`. Сидер полностью пересоздаёт схему приложения.
4. Запустить приложение: `just prod` или `just dev`.
5. Временно включить `AUTH_DEBUG_BOOTSTRAP_ENABLED=true` и задать длинный одноразовый `AUTH_DEBUG_BOOTSTRAP_TOKEN`.
6. Войти через Keycloak. Для неизвестной учётной записи откроется `/auth/bootstrap` с безопасными claims пользователя.
7. Создать администратора либо обычного пользователя и назначить ему формы, поля, режим доступа и фильтры.
8. Установить `AUTH_DEBUG_BOOTSTRAP_ENABLED=false`, удалить одноразовый код из рабочего окружения и перезапустить приложение.

Вместо отладочной страницы первого администратора можно заранее задать `AUTH_SEED_ADMIN_EMAIL`, `AUTH_SEED_ADMIN_NAME` и при наличии `AUTH_SEED_ADMIN_SUB`. После первого входа запись привяжется к issuer и subject.

## Проверка

- `https://<dns-имя>/api/health` должен вернуть успешный ответ.
- После входа известный пользователь открывает приложение, неизвестный при включённом режиме попадает на `/auth/bootstrap`.
- В Grafana Explore источник Loki должен находить `{app="reportcollector"}`.
- Удобные фильтры событий: `|= "keycloak_identity_received"`, `|= "unknown_oidc_user"`, `|= "oidc_authentication_failed"` и `|= "user_created_from_debug_bootstrap"`.
- В логах допустимы имя, e-mail, `preferred_username`, `sub`, issuer и признак подтверждения e-mail. Токенов и client secret там быть не должно.

## Тестирование через Playwright

Для проверки OIDC-потока без браузера используйте режим `KEYCLOAK_ENABLED=false` (ключевой вход), который покрывается e2e-тестами в `front/e2e/auth.spec.ts`.

Для полной проверки корпоративного Keycloak запустите приложение с prod-конфигурацией и выполните ручной тест входа с корпоративными credentials. Проверьте лог-событие `keycloak_identity_received` в Grafana → Loki.
