# Привязка к конституции: оценка (§56)

Состояние структурировано, поэтому оценка **многоуровневая** — не только
`answer == expected`:

> Качество доказательств · Проверка утверждений · Калибровка уверенности ·
> Корректность провенанса · Корректность вычислений · Покрытие ответа ·
> Покрытие источников

`reactifact.eval` — детерминированный, без LLM харнесс, оценивающий итоговое
**состояние** прогона. Каждая метрика — чистая функция над итоговым `Context`
(+ опциональный грёд-трус), поэтому правдивость измеряется там, где она живёт —
в графе артефактов, — а не гладкость текста.

## Запуск сюита

```python
from reactifact.eval import EvalCase, calculation_correctness, core_metrics, run_suite

cases = [
    EvalCase(
        name="calc-question",
        run=_run_knowledge_calc,               # выполняет пайплайн → Context
        expected={"sources": ["costs:", "pricing:", "guide:"]},
    ),
]
report = run_suite(cases, metrics={
    **core_metrics,                          # answer/provenance/evidence/claim
    "calc": calculation_correctness(values=(5480, 3580)),
    "sources": source_coverage(),
})
print(report.render())
```

```
eval · multi-level report (§56)

[calc-question] overall 1.000
    answer_present            1.000
    provenance_grounded       1.000
    evidence_quality          1.000
    claim_verification        1.000
    calc                      1.000
    sources                   1.000

suite overall: 1.000
```

## Метрики

Классы артефактов сопоставляются **по имени** (`Answer`, `Evidence`, …),
поэтому харнессу не нужны доменные импорты — домен остаётся вне фреймворка.

| Метрика | На что отвечает | Встроенная |
| --- | --- | --- |
| `answer_present` | прогон вообще дал ответ? | функция |
| `provenance_grounded` | каждый ли ответ подкреплён существующей `supported_by` (§34)? | функция |
| `evidence_quality(threshold=0.5)` | доля доказательств с оценкой не ниже порога | функция |
| `claim_verification(valid=("verified",))` | доля утверждений, прошедших проверку (§35) | функция |
| `confidence_calibration()` | Brier-скор `Claim.confidence` против фактической правильности (§56) | фабрика (нужен `expected.claim_correctness`) |
| `answer_coverage()` | покрытие ожидаемого текста ответа фактическим | фабрика (нужен `expected.answer`) |
| `calculation_correctness(values=…)` | доля вычислений, совпавших с грёд-трусом (§67) | фабрика |
| `source_coverage()` | доля источников ответа, покрытых маркерами | фабрика (нужен `expected.sources`) |

`core_metrics` объединяет четыре не генеративные. Метрика без грёд-труса
возвращает `None` и попадает в **skipped** (`EvalResult.skipped`), а не в тихий
ноль.

## Оценка по датасету

Кроме оценки одного готового `Context`, `reactifact.eval` прогоняет **датасет**
через **target** и оценивает каждый пример — офлайн-цикл оценки, без hosted-
хранилища:

```python
from reactifact.eval import (
    Dataset, evaluate, from_metric, judge_correctness, summary_pass_rate,
)

dataset = Dataset.from_file("evals/qa.json")       # или from_list / .jsonl
report = evaluate(
    dataset,
    target=lambda inputs: my_pipeline(**inputs),   # Context | ScenarioResult | {outputs} | RunResult
    evaluators={
        "present": from_metric("answer_present", answer_present),
        "coverage": from_metric("coverage", answer_coverage()),
        "correctness": judge_correctness(llm),      # LLM-as-judge
    },
    summary=[summary_pass_rate(0.8)],
)
report.assert_passed({"correctness": 0.7})          # CI-гейт (бросает EvalFailure)
```

`Example` несёт `inputs` (уходят в target), опциональный `reference_outputs`
(используется только эвалуаторами) и `metadata`; `id` выводится из содержимого,
поэтому `Dataset.version` привязывает прогон к ревизии датасета. Target может
вернуть `Context`, `ScenarioResult` из `reactifact.testing` (читается через
`.context`/`.trace`), обычный mapping выходов или `RunResult`. `evaluate` идёт
по примерам последовательно в порядке датасета (`max_concurrency=N` —
параллелит I/O-bound target'ы).

## Эвалуаторы

`Evaluator` — любой callable `EvalInput -> Feedback | bool | float |
{key: score} | list | None`; `None` **пропускает** метрику (нет грёд-труса /
неприменимо — а не тихий ноль). `EvalInput` даёт пример, извлечённые `outputs`
и — для прогонов reactifact — итоговые `context` и `trace`. `from_metric`
заворачивает любую метрику скоринга в эвалуатор:

```python
evaluators = {
    "grounded": from_metric("provenance_grounded", provenance_grounded),
    "calc": from_metric("calc", calculation_correctness(values=(5480, 3580))),
}
```

`trajectory_match(mode, steps=…)` сравнивает **путь** прогона с
`example.reference_outputs["trajectory"]` — типизированный аналог сопоставления
траекторий сообщений. `steps` — `"agents"` (путь агентов), `"events"`,
`"reads"`/`"writes"` (`"create:Answer"`, …) или свой `EvalInput -> list[str]`:

| Режим | Смысл |
| --- | --- |
| `strict` | те же шаги в том же порядке |
| `unordered` | тот же мультимножество шагов |
| `subset` | actual ⊆ expected (без лишних шагов) |
| `superset` | expected ⊆ actual (минимум обязательных шагов) |

## LLM-as-judge

Для субъективного качества детерминированной метрики нет — LLM оценивает
вывод по рубрике. `llm_judge` превращает любой `LLMProvider` в `Evaluator`,
т.е. судья — просто ещё один вызов модели: он сочетается с
`CachingLLM`/бюджетом/метриками и подменяется в тестах через `FakeLLM`:

```python
from reactifact.eval import judge_correctness, judge_faithfulness, judge_relevance

judge_correctness(llm, continuous=True, choices=[0.0, 0.5, 1.0])   # по эталону
judge_relevance(llm)        # без эталона: отвечает ли на вопрос?
judge_faithfulness(llm)     # без эталона: обосновано ли контекстом?
```

Судью просят о строгом JSON (`{"score": …, "comment": …}`); парсинг терпимый
(ищет первый JSON-объект, затем число/булево). `continuous` даёт float 0..1;
`choices` притягивает к фиксированной шкале; `include_reference` форматирует
эталонные выходы в промпт.

## Гейт и summary

`EvalReport.aggregate()` — среднее по ключу метрики; `passed(…)` /
`assert_passed(…)` (или модульный `assert_eval`) — CI-гейт: порог для ключа,
который не измерил ни один кейс, **не** выполняется. Summary-эвалуаторы
(`summary_pass_rate(threshold)`, `summary_mean(key)`) агрегируют по всему
сюиту в `EvalReport.summary`.

## Online-оценка

Те же эвалуаторы могут оценивать **живые** прогоны вместо подготовленного
датасета: `reactifact.eval.online` сэмплирует завершённые прогоны из
`TraceStore`, скорит их и записывает вердикты обратно тегами (канал
ревьюерских аннотаций, который дашборд уже читает) плюс метриками.

```python
from reactifact.eval import (
    OnlineEvalConfig, OnlineEvaluator, judge_relevance, output_present,
)
from reactifact.tracing import TraceStore

store = TraceStore("traces.db")
evaluator = OnlineEvaluator(
    store,
    OnlineEvalConfig(
        evaluators={"present": output_present(), "relevance": judge_relevance(llm)},
        sample_rate=0.1,            # оценить 10% подходящих прогонов
        tag="eval", failure_tag="eval:failed",
    ),
    metrics=metrics,                # любой reactifact.metrics.Metrics
)
await evaluator.run_once()          # один батч
# или в lifespan-задаче FastAPI:
await evaluator.run_forever(interval_seconds=300)
```

Трейс — это **сводка**: данные артефактов и сообщения LLM усекаются при
сохранении, поэтому источник решает, что вообще можно оценить:

- `trace_source()` (по умолчанию): оценивает сам `RunTrace` — путь прогона
  (`trajectory_match`) и `output_present`/`no_errors` по выводам и спанам.
  Метрика, которой нужны данные, которых в трейсе уже нет, помечается
  **skipped**.
- `context_source(run_fn)`: приложение восстанавливает полный `Context` по id
  прогона, поэтому `core_metrics`, `from_metric(...)` и судьи видят полные
  данные.
- `OnlineEvalConfig.reference_fn(run)` даёт грёд-трус на прогон (напр.
  ожидаемую траекторию) для метрик, которым нужен эталон.

`sample_rate`/`strategy`/`seed` выбирают прогоны (воспроизводимо при заданном
`seed`); `on_report` наблюдает каждый батч. FastAPI-приложение может
смонтировать `create_online_eval_router(evaluator)` — `POST /api/evals/run`,
`GET /api/evals/report`, `GET /api/evals/summary` — а CLI оценивает стор одной
командой:

```bash
reactifact eval traces.db --sample 1.0
reactifact eval traces.db --evaluators judge --provider openai:gpt-4o-mini
```

## Типы

- `Metric` — один измеренный 0..1 скор с весами `weight`.
- `EvalCase` — имя + `run() -> Context` + опциональный `expected`.
- `EvalResult` (на кейс) / `EvalReport` (сюит) — `overall()` (взвешенное
  среднее), `aggregate()`, `passed()`, `to_dict()`, `render()`.
- `Example` / `Dataset` — версионируемые примеры + загрузка JSON/JSONL.
- `EvalInput` / `Feedback` / `Evaluator` — входы и вердикт эвалуатора.
- `RunResult` — нормализованный результат target'а (`outputs` + context/trace).
- `EvalFailure` — бросается гейтом (наследник `AssertionError`).

## Куда смотреть

`tests/test_eval.py` оценивает калькуляционный вопрос demo `knowledge` офлайн
(без LLM); `tests/test_eval_dataset.py`, `test_eval_trajectory.py` и
`test_eval_judge.py` покрывают цикл по датасету, сопоставление траекторий и
судью (`FakeLLM`, возвращающий JSON).