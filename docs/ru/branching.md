# Ветвление и слияние (§39-§40)

> Живой пример: [`examples/forklab`](https://github.com/bzdvdn/reactifact/tree/master/examples/forklab) —
> детерминированное демо «две стратегии: форк → слияние → оценка», с флагом
> `--conflict`, показывающим явный конфликт и политику его разрешения.

Состояние, а не оркестрация: форк — это **независимая копия контекста** для
исследования альтернативного состояния — гипотезы §39 — это форки, а не пути в
графе исполнения.

## Форк

```python
from reactifact import Context, RuntimeResources

base = Context(resources=RuntimeResources())
base.create(Note(text="v1"), id="note:1")

hypothesis_a = base.branch(name="hypothesis-a")
hypothesis_b = base.branch(name="hypothesis-b")

# с этого момента обе полностью изолированы
hypothesis_a.create(...)
hypothesis_b.create(...)
```

`branch()` глубоко копирует состояние **и запоминает снимок базы**, чтобы
последующее `merge` двух ответвлений могло трёхсторонне обнаружить расхождения.
Ветка делит `resources` с родителем, но расходится по всем артефактам/связям.

## Слияние — явные конфликты, никакого молчаливого выбора (§40)

```python
hypothesis_a.merge(hypothesis_b)
```

Трёхстороннее слияние относительно общей базы форка. Для каждого артефакта,
присутствующего в base/self/other:

| Случай | Результат |
| --- | --- |
| `self == other` | без изменений |
| `self == base` (двигал только другой) | принять `other` |
| `other == base` (двигал только self) | оставить `self` |
| иначе (оба разошлись по-разному) | **`MergeConflict`**, ничего не применяется |

Слияние **атомарно**: один конфликтный артефакт прерывает всё слияние — никакого
частичного состояния. Успешное слияние фиксируется `merge`-коммитом. Удаления тоже
участвуют: одна сторона удаляет артефакт, другая его правит — конфликт; чистое
удаление распространяется.

```python
from reactifact import MergeConflict

try:
    hypothesis_a.merge(hypothesis_b)
except MergeConflict as exc:
    print(exc.conflicts)     # например ["note:1 diverged since the fork (self=changed, other=changed)"]
```

Список конфликтов — вход для верификатора/политики слияния (фреймворк не
выбирает, §40).

## Персистентность: `BranchStore` поверх KV-бэкенда

Ветки переживают перезапуск как именованные ключи на **том же KV-бэкенде** — без
нового хранилища, семантика живёт в операциях `Context`:

```python
from reactifact.branching import BranchStore
from reactifact.checkpoints import SQLiteKVBackend

store = BranchStore(SQLiteKVBackend("sessions.sqlite3"))
await store.save_branch(hypothesis_a, session_id="demo", name="hypothesis-a")
restored = await store.load_branch("demo", "hypothesis-a")
restored.merge(await store.load_branch("demo", "hypothesis-b"))   # база тоже выживает
```

Снимок базы форка сериализуется вместе с контекстом, так что `merge` сохраняет
детекцию конфликтов после перезагрузки.

## CLI

```bash
python -m reactifact branch sessions.sqlite3 demo list
python -m reactifact branch sessions.sqlite3 demo save hypothesis-a
python -m reactifact branch sessions.sqlite3 demo merge --into a --source b --as merged
```

## Что когда использовать

| Ситуация | Подход |
| --- | --- |
| Артефакт проходит фазовые состояния | `StatusMachine` + верификатор-produce |
| Потоку нужно *откатываться* на правках пользователя | гард стадии + сброс вниз по потоку |
| **Альтернативные состояния для исследования и сравнения** | `branch()` + `merge()` (§39-§40) |
| «Который из этих выбрала модель?» | парсинг + гард (как `PickStage`) |