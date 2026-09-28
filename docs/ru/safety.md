# Trust & safety

Три опциональных слоя библиотечного уровня для работы с недоверенным вводом и
несколькими арендаторами. Они действуют на **типизированные артефакты и доступ
к ресурсам**, поэтому детерминированы и тестируемы (в отличие от middleware
вокруг сообщений модели). Всё настраивается на `RuntimeResources` и по
умолчанию выключено.

## Guardrails

Guardrail проверяет или переписывает данные артефакта *до коммита*. Каждый
`Create`/`Update`, который порождает агент, проходит проверку:

```python
from reactifact import RuntimeResources
from reactifact.guardrails import GuardrailPolicy, InjectionGuardrail, PIIGuardrail
from reactifact.redaction import RegexRedactor

resources = RuntimeResources(
    guardrails=GuardrailPolicy(
        [PIIGuardrail(redactor=RegexRedactor()), InjectionGuardrail()],
        on_violation="block",          # или "flag"
    ),
)
```

Guardrail — любой объект с `name` и `check(data, context) ->
GuardrailDecision`. Решение — `allow`, `redact` (с заменой `data`), `violation`
(отдать решение политике через `on_violation`) или `block`/`flag` (переопределить
политику для этого guardrail). Встроенные:

| Guardrail | Что проверяет |
| --- | --- |
| `PIIGuardrail(redactor=…)` | редактирует PII в строковых полях через любой `Redactor` |
| `InjectionGuardrail()` | консервативный лексикон prompt-injection |
| `DenyListGuardrail(patterns, …)` | заданный пользователем deny-list |
| `SizeGuardrail(max_chars, field=…)` | ограничение длины ввода |
| `PatternGuardrail(patterns, …)` | общая regex-форма |

`block` бросает `GuardrailViolation` из `arun()` (или роняет генерацию при
`isolate_errors=True`); `flag` пишет метрику
`reactifact_guardrail_triggered_total` и пропускает артефакт. Редактирование
идёт по порядку, поэтому `PIIGuardrail` может очистить текст до того, как его
увидит `InjectionGuardrail`. Guardrails применяются к артефактам агентов; чтобы
проверить засеянный ввод, вызовите `policy.evaluate(data, context)` сами.

## Authorization

`Principal` (пользователь/арендатор/сервис, от имени которого идёт прогон) плюс
политика, гейтящая, что ему можно:

```python
from reactifact.authz import PermissionPolicy, Principal

resources = RuntimeResources(
    principal=Principal(id="alice", capabilities=("analyst",)),
    authorizer=PermissionPolicy(
        {"analyst": {"run:*", "create:Answer"}},
        default_allow=False,           # анонимные principals запрещены
    ),
)
```

`PermissionPolicy.grants` сопоставляет capability шаблонам `"action:resource"`
(`"run:*"`, `"create:*"`, `"*"`). Рантайм энфорсит `run` (имя агента) и
`create`/`update`/`delete` (тип артефакта); цикл инструментов энфорсит
`execute` (имя инструмента); produce может гейтить свои коллабораторы через
`resources.require_authorized("read", id)`. Отказ бросает `AuthorizationError`.

## Quota

`Budget` ограничивает один ход; quota ограничивает principal между ходами:

```python
from reactifact.quota import Quota, QuotaTracker

resources = RuntimeResources(
    principal=Principal(id="acme"),
    quota=QuotaTracker(Quota(max_tokens=1_000_000, window_seconds=86_400)),
)
```

Рантайм проверяет квоту в начале каждого хода; исчерпанный ключ выставляет
`RunOutcome.QUOTA_EXCEEDED` и ничего не запускает. Расход считается за ход через
`QuotaLLM` (попадания в кэш не списываются). Хранилище по умолчанию —
в процессе (как `InMemoryRateLimiter` в LangChain): между воркерами не
координируется; передайте `store=` (крошечный протокол `QuotaStore`) поверх
общего состояния для кластера.

## Связь с остальными примитивами

`Redactor` (редактирование трейсов) и гейт одобрения destructive-инструментов
существовали раньше и работают сами по себе; trust & safety сводит их в
политику и добавляет авторизацию и квоту. `MetricsTracer` пишет срабатывания
guardrails и исходы quota-exceeded, поэтому «почему ход заблокирован» видно из
тех же трейсов и метрик.
