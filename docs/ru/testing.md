# Тестирование агентских пайплайнов

`reactifact.testing` — поведенческий харнесс для агентских пайплайнов:
**посеять артефакты, прогнать агентов, проверить, что произошло**. Он намеренно
отделён от `pytest` — сценарии могут идти вживую, записывать реальную модель или
реплеить запись — но при желании встраивается в `pytest`.

```python
from reactifact.testing import ScenarioLab

lab = ScenarioLab([my_agent], resources=lambda: build_resources())
result = await lab.run(Question(text="what's the refund policy?"))

result.artifacts(Answer).exists()
result.artifacts(Answer).linked("supported_by", Evidence)
result.tools.called("search")
result.path.contains("answer")
result.llm.max_calls(3)
result.errors.none()
```

На каждый `run()` создаётся свежий `Context`/`Runtime`, поэтому поставленный
сбой или рекордер вызовов тулов не утекают между прогонами.

## Sync или async

`run()`/`turn()` — асинхронные; `run_sync()`/`turn_sync()` — то же самое для
обычных (не-async) pytest-тестов:

```python
result = lab.run_sync(Seed(n=21))          # asyncio.run(lab.run(...))
```

## Группы ассертов

Всё, что сценарию нужно проверять, — это свойства `ScenarioResult` (у
`Scenario` — агрегированные варианты):

| Группа | Что проверяет |
| --- | --- |
| `result.artifacts(Type)` | поля последнего/любого артефакта: `exists`, `none`, `count`, `contains`, `matches` (regex), `equals(**fields)`, `field_in` и `linked(relation, Type)` для провенанса |
| `result.relations` | рёбра графа артефактов: `has`/`none`/`count` (любой фильтр — wildcard), `outgoing`/`incoming` |
| `result.tools` | записанные вызовы тулов: `called`, `never_called`, `called_times`, `called_with`, `called_any`, `call_order` |
| `result.path` | путь исполнения агентов: `contains`, `not_contains`, `sequence`, `exact_sequence`, `any_of`, `times` |
| `result.llm` | вызовы/токены: `calls`, `prompt_tokens`, `completion_tokens`, `tokens`, `max_calls`, `max_tokens`, `by_agent` |
| `result.errors` | изолированные ошибки агентов (`isolate_errors=True`, дефолт харнесса): `none`, `count`, `expected(agent)` |
| `result.events` | progress-события `context.announce()`: `contains`/`not_contains` (regex), `messages(kind=…)`, `count`, `min_count` |

Падения бросают `AssertionFailure` с вписанными наблюдаемыми данными — падающий
тест отлаживается прямо из вывода pytest.

### Провенанс — это ассерт, а не соглашение

Типизированный граф артефактов — причина использовать reactifact, поэтому
харнесс проверяет его напрямую:

```python
answer = result.artifacts(Answer).exists()
evidence = result.artifacts(Answer).linked("supported_by", Evidence)
result.relations.has(source=answer.id, relation="supported_by")
result.relations.none(relation="contradicted_by")
```

## Многоходовые сценарии

`lab.scenario()` переиспользует один `Context` между вызовами `.turn()`, поэтому
следующий ход видит всё, что произвёл предыдущий — для потоков, которым нужно
больше одного раунда ввода, чтобы дойти до проверяемого состояния:

```python
convo = lab.scenario()
await convo.turn(UserMsg(text="вот мои пожелания"))
await convo.turn(UserMsg(text="1"))          # выбрать вариант дизайна 1
await convo.turn(UserMsg(text="да, согласен"))  # одобрить

convo.tools.called_times("estimate", 1)      # агрегат по всем ходам
convo.path.contains("repair_flow")
```

## Сбои и заглушки

Всё ниже — **одноразовое**: ставится на лабу, расходуется следующим
`run()`/`.turn()`, не переносится дальше.

```python
# заставить тул падать (times=N: только первые N, затем делегирование)
lab.fail("search", ConnectionError("unreachable"), times=1)
lab.fail("kubectl", RuntimeError("boom"), when=lambda args: args["resource"] == "pods")
lab.fail("slow_tool", TimeoutError(), delay=5.0)   # сначала задержка, потом падение

# заставить тул вернуть заранее заданный вывод вместо выполнения
lab.stub_tool("search", text="canned result")

# то же для любого нетул-ресурса — "llm", "embedder", id источника, имя,
# заданное через resources.set(...), или типизированный Type/ResourceKey
lab.fail_resource("llm", ConnectionError("model down"))
lab.fail_resource("embedder", RuntimeError("no vectors"), method="embed")
lab.stub_resource("catalog", returns={"sku": "A1"})
lab.stub_resource("catalog", side_effect=[{"sku": "A1"}, KeyError("missing")])
```

`stub_resource(side_effect=[…])` следует семантике `unittest.mock`: элемент,
который является `Exception`, бросается, остальное возвращается. `when(args)` /
`when(args, kwargs)` сужают сбой до совпадающих вызовов; `delay` спит сначала
(async-ресурсы уступают; sync — блокируются).

Одна оговорка про тулы: `ToolUse` ловит исключение тула и отдаёт модели строку
`"Tool 'x' failed: …"`, поэтому инъектированный сбой обычно **не** прерывает
прогон — агент видит его как реальный временный сбой и может повторить.
`result.tools.called("x")[0].error` покажет упавший вызов, а `result.errors.none()`
может при этом пройти.

## Record / replay

`mode=` управляет LLM за `resources.llm`:

- `"live"` (дефолт) — реальный провайдер;
- `"record"` — оборачивает его, дописывая каждый вызов в `recording_path`;
- `"replay"` — без сети; вызов, разошедшийся с записью, бросает `ReplayMiss`,
  а не отвечает догадкой (§59).

```python
lab = ScenarioLab(agents, resources=resources,
                  mode=mode_from_env(),              # live/record/replay
                  recording_path="scenarios/data/calls.jsonl")
```

`mode_from_env()` читает `$REACTIFACT_SCENARIO_MODE`, который выставляет
`reactifact scenario --mode …` — так один и тот же сценарий идёт вживую,
пишется или реплеится в зависимости только от способа запуска.

## Golden-снапшоты

Замораживает отпечаток прогона (`context_hash` + хеши промптов из трейса) и
падает при дрейфе. Отсутствующий файл — или `update=True` /
`$REACTIFACT_GOLDEN_UPDATE=1` — **записывает** снапшот вместо падения, так что
первый прогон его заводит:

```python
result.assert_golden("scenarios/data/gpu.golden.json")
# позже: REACTIFACT_GOLDEN_UPDATE=1 pytest   # перезаписать после намеренного изменения
```

Сочетайте record/replay с golden для полностью офлайнового регресса на реальном
прогоне.

## Отладка прогона

`result.explain()` (и `scenario.explain()`) выводит всё, что произвёл прогон —
артефакты по типам, отношения, путь, вызовы тулов/LLM, ошибки — для вывода
падающего теста или быстрого `print()`:

```python
print(result.explain())
```

## Запуск тестов

Под pytest подключите плагин, чтобы `async def`-сценарии шли без
`pytest-asyncio`, а `ScenarioSkip` становился skip:

```python
# tests/conftest.py
pytest_plugins = ["reactifact.testing.pytest_plugin"]
```

Он же даёт фикстуру-фабрику `scenario_lab`, которая по умолчанию берёт `mode=`
из `$REACTIFACT_SCENARIO_MODE`:

```python
def test_refund_policy(scenario_lab):
    lab = scenario_lab([my_agent], resources=build_resources)
    result = lab.run_sync(Question(text="refund policy?"))
    result.artifacts(Answer).contains("14 days")
```

Вне pytest регистрируйте сценарии через `@scenario` и запускайте CLI — удобно
для live/record прогонов, которых обычная сессия `pytest` не должна касаться:

```bash
reactifact scenario examples.repair.scenarios
reactifact scenario examples.knowledge.scenarios --mode replay
```

Для *оценки* вывода пайплайна (качество evidence, provenance grounding,
корректность вычислений), а не проверки поведения, см. [Evaluation](eval.md).
