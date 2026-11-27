# Провайдеры

Провайдеры — это *способности*: LLM, эмбеддеры, модели изображений/речи/видео.
Они живут в `reactifact.providers`, не в ядре. Приложение подключает нужные в
`RuntimeResources`; ядро общается с ними только через узкие контракты
(`LLMProvider`, `EmbeddingProvider`, …).

## Контракты

Ядро знает четыре контракта:

| Контракт | Роль |
| --- | --- |
| `LLMProvider` | генерация текста (`generate`, `stream`) |
| `EmbeddingProvider` | векторные эмбеддинги |
| `ImageProvider` | генерация изображений |
| `SpeechProvider` / `TranscriberProvider` | TTS / STT |
| `VideoProvider` | генерация видео из текста |

`LLMRequest` / `LLMResponse` / `LLMResponseChunk` и `Message` — проводные типы.
`FakeLLM` и `FakeEmbedder` живут в `providers.fake` — детерминированные заглушки,
чтобы демо и тесты работали без ключей API.

## Переключатели через .env

Самая простая схема подключения — фабрики `*_from_env`, читающие `.env`:

```python
from reactifact.providers import (
    embedder_from_env,
    image_from_env,
    llm_from_env,
    openrouter_llm,
)

resources = RuntimeResources(llm=llm_from_env() or openrouter_llm())
emb = embedder_from_env()
if emb is not None:
    resources.embedder = emb
img = image_from_env()
if img is not None:
    resources.set("images", img)
```

Паттерн: **`llm_from_env() or <вендор по умолчанию>`**. Когда ключа нет, фабрика
возвращает `None`, и демо переходит на детерминированное поведение (поэтому каждый
пример работает вообще без ключей API).

`from_env(**overrides)` собирает тот же двухветочный паттерн, который каждый
пример вручную повторяет в своём `build_llm()` (сначала `OPENROUTER_API_KEY`,
иначе `OPENAI_BASE_URL`, иначе `None`) — для приложений, которым нужен общий
дефолт без копирования этого блока:

```python
from reactifact.providers import from_env

llm = from_env(max_tokens=2048)  # openrouter_llm(...) или llm_from_env(...) или None
```

Примеры в репозитории предпочитают **явные фабрики** (`openai_llm(...)`,
`openrouter_llm(...)`) с явным `max_tokens`: каждое демо определяет маленький
`build_llm()`, который читает `.env` и выбирает провайдера, — всегда видно,
какой провайдер работает, и при отсутствии ключа оно остаётся офлайн-совместимым.

### Декларативные спецификации провайдера

`LLMSpec` — альтернатива ручной ветке `build_llm()`. Он один раз описывает
провайдера, креды, сетевые настройки и политику fallback; `build()` возвращает
провайдера или `None` и сообщает причину:

```python
from reactifact.providers import LLMSpec

llm = LLMSpec(
    provider="openrouter",              # или "openai", "openai-compatible"
    model="deepseek/deepseek-v4-flash",
    api_key_env="OPENROUTER_API_KEY",
    proxy="http://proxy:3128",
    temperature=0.0,
    max_tokens=2048,
).build(
    on_error=lambda reason, exc: print("llm недоступен:", reason),
)
```

- `provider` покрывает все chat-фабрики: `openai`, `openrouter`, `anthropic`,
  `gemini`, `azure`, `ollama`, `cerebras`, `deepseek`, `fireworks`,
  `github-models`, `groq`, `mistral`, `nvidia`, `perplexity`, `qwen`, `together`,
  `xai`, `zai`, а также `openai-compatible`/`compatible`/`local`/`vllm`/
  `lmstudio`. Алиасы: `claude`, `google`, `github`, `nvidia-nim`.
- Нативные дефолты остаются в фабрике: OpenRouter fast mode (`reasoning`
  выключен), Anthropic `x-api-key`/raw-key и 4096 токенов, Gemini
  `x-goog-api-key`, env-настройки вендорных OpenAI-compatible провайдеров,
  `ollama` без ключа. `auth_header`/`auth_scheme` задавайте только для
  override; `raw_key=True` отправляет ключ без схемы.
- `azure`: `base_url` — endpoint ресурса, `model` — deployment; `ollama`
  собирается без ключа; `openai-compatible` требует `base_url`.
- `api_key` не попадает в `repr`; `api_key_env` задаёт имя переменной. Явные
  `api_key`/`api_key_env` не откатываются молча на стандартную env-переменную
  провайдера.
- `allow_anonymous=True` — для локального endpoint без auth (Ollama, vLLM,
  LM Studio). Это явный флаг: отсутствие ключа — конфигурационный исход, а не
  молчаливый неаутентифицированный запуск.
- `extra_headers`/`extra_body`/`stream_usage` передаются только провайдерам,
  которые их принимают (нативные Anthropic/Gemini отличаются); остальные поля
  общие.
- `build()` возвращает `None` и вызывает `on_error("missing_key" |
  "missing_base_url" | "unknown_provider" | "build_error", exc)`;
  невалидные значения конструктора по-прежнему дают `ValueError`. Приложение
  остаётся на детерминированном fallback.

## Чат-провайдеры

`OpenAICompatProvider` — универсальный клиент, совместимый с OpenAI, он же основа
вендорных фабрик. Поддерживает:

- `api_base` / кастомный `proxy` (базовый URL) и `auth_scheme`/`auth_header`.
- `_network_knobs` — proxy/auth-заголовок/auth-схема из
  `<PREFIX>_PROXY`/`<PREFIX>_AUTH_HEADER`/`<PREFIX>_AUTH_SCHEME` или явных
  overrides.
- `retry_attempts` (по умолчанию `3`) — 429/5xx и сетевые ошибки (обрыв
  соединения, таймаут) повторяются с экспоненциальным backoff; 4xx никогда
  не повторяется. Работает только для `complete()`, не для `stream()`
  (стрим, упавший на середине, нельзя безопасно повторить без дублирования
  уже отданных чанков) — см. [Надёжность](#reliability) ниже.
- `stream` для посимвольных `LLMResponseChunk`.

11 из вендорных `*_llm` фабрик ниже (Cerebras, DeepSeek, Fireworks, GitHub
Models, Groq, NVIDIA NIM, Perplexity, Qwen, Together, xAI, z.ai) собраны из
одной фабрики-конструктора `_openai_compat_llm(env_prefix=...,
default_model=..., default_base_url=...)` — каждый вендорный модуль это
3-строчный конфиг, а не отдельно скопированная функция.

| Фабрика | Вендор |
| --- | --- |
| `openai_llm` / `openai_embedder` | OpenAI |
| `anthropic_llm` | Anthropic |
| `mistral_llm` / `mistral_embedder` | Mistral |
| `deepseek_llm` | DeepSeek |
| `openrouter_llm` / `openrouter_embedder` / `openrouter_speech` | OpenRouter (много вендоров: чат, изображения, видео, эмбеддинги, TTS — не STT, см. ниже) |
| `groq_llm` / `groq_transcriber` | Groq (быстрый инференс; Whisper STT) |
| `xai_llm` | xAI (`grok`) |
| `together_llm` / `together_embedder` | Together AI |
| `fireworks_llm` / `fireworks_embedder` | Fireworks |
| `azure_llm` | Azure OpenAI |
| `cerebras_llm` | Cerebras (быстрый инференс) |
| `github_models_llm` | GitHub Models |
| `qwen_llm` / `qwen_embedder` | Qwen/百炼 (DashScope) |
| `nvidia_nim_llm` / `nvidia_embedder` | NVIDIA NIM |
| `perplexity_llm` | Perplexity |
| `zai_llm` | Z.ai |
| `ollama_llm` | локальный Ollama |
| `gemini_llm` | Google Gemini (`GeminiProvider`) |

Каждая фабрика embedder/speech/transcriber выше проверена по документации
самого вендора, а не предположена из «раз чат OpenAI-совместим, то и
остальное тоже» — некоторые возможности, которые выглядят так, будто должны
существовать в OpenAI-совместимом виде, на деле нет. Конкретный пример:
`/audio/transcriptions` (STT) у OpenRouter принимает JSON с base64, а не
multipart-загрузку файла, которую ожидает `OpenAICompatTranscriber` (и любой
другой транскрайбер здесь) — реальное различие API, поэтому
`openrouter_transcriber` намеренно нет.

## Изображения, речь, видео

| Способность | Провайдеры |
| --- | --- |
| Изображения | `OpenAICompatImageProvider` (любой image API), `OpenRouterImageProvider`, `GeminiImageProvider`, `gemini_image`, `image_from_env` |
| Эмбеддинги | `OpenAICompatEmbedder`, `openai_embedder`, `mistral_embedder`, `openrouter_embedder`, `together_embedder`, `fireworks_embedder`, `qwen_embedder`, `nvidia_embedder`, `embedder_from_env` |
| Речь (TTS) | `OpenAICompatSpeech`, `openrouter_speech`, `speech_from_env` |
| Речь (STT) | `OpenAICompatTranscriber`, `groq_transcriber`, `transcriber_from_env` |
| Видео | `SoraVideoProvider`, `RunwayVideoProvider`, `LumaVideoProvider`, `OpenRouterVideoProvider`, `video_from_env` — `generate()`/`fetch()`/`download()` все с ретраями (та же политика, что у чата); сам `poll()` терпит `fetch()`, упавший даже после своего ретрая, и продолжает опрос до `timeout`, а не бросает многоминутную задачу из-за одного сетевого сбоя |

Каждая `*_from_env` принимает `**overrides`, чтобы в тестах можно было внедрить
конфигурацию без переменных окружения.

## .env-справочник (примеры)

В каждом демо есть `.env.example`; типичный набор для чат-модели:

```dotenv
# выбор модели (его читают фабрики *_from_env)
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-...
OPENROUTER_MODEL=deepseek/deepseek-chat

# опционально
EMBEDDING_PROVIDER=mistral
MISTRAL_API_KEY=...
IMAGE_PROVIDER=openrouter
IMAGE_MODEL=google/gemini-2.0-flash-exp:free
```

## Надёжность {: #reliability }

**Ретраи.** У каждого провайдера (`OpenAICompatProvider`/`Embedder`,
`AnthropicProvider`, `GeminiProvider`, `OpenAICompatImageProvider`,
`OpenAICompatSpeech`/`Transcriber`, все четыре видео-провайдера) есть
`retry_attempts` (по умолчанию `3`) — 429/5xx и сетевые ошибки повторяются с
экспоненциальным backoff через `reactifact.providers._retry.with_retry`; 4xx
(auth/bad request) никогда не повторяется, потому что повтор
неправильной настройки только откладывает реальную ошибку. `retry_attempts=1`
отключает ретраи.

**Failover.** `RouterLLM([primary, secondary, …])` вызывает провайдеров по
порядку и переходит к следующему при сбое — недоступность провайдера, лимит
запросов или отсутствующий ключ, пока другой работает. По умолчанию падение
любого исключения запускает fallback; `should_fallback=` сужает это (напр.
`retryable_only` пропускает 4xx, который повторится у всех). Fallback только у
`complete()` — `stream()` использует первого провайдера. Колбэк
`on_fallback(provider, exc)` вызывается перед переходом.

**Точный кэш.** `CachingLLM(provider)` ключует каждый `complete()` по всему
запросу (model, messages, temperature, …) и отвечает на повтор без вызова
провайдера — без токенов и задержки; `cache=KVCache(FileKVBackend(…))` делает
его переживающим рестарт. Хит помечается (`raw={"reactifact_cache": "hit"}`), и
`BudgetLLM` его **не** списывает. Сознательно не семантический (приближённое
совпадение ответило бы на другой вопрос), `stream()` не кэшируется.

```python
from reactifact.cache import CachingLLM, KVCache
from reactifact.checkpoints import SQLiteKVBackend
from reactifact.routing import RouterLLM, retryable_only

resources.llm = CachingLLM(                     # кэш снаружи
    RouterLLM([openrouter_llm(), groq_llm()],   # failover в середине
              should_fallback=retryable_only),
    cache=KVCache(SQLiteKVBackend("llm-cache.db")),
)
```

**Жизненный цикл HTTP-клиента.** Каждый провайдер лениво открывает
`httpx.AsyncClient` и владеет им — рантайм ничего не закрывает сам. Вызывайте
`await resources.aclose()` (`RuntimeResources`) сами при реальном завершении
работы (FastAPI `lifespan` или конец скрипта):

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await resources.aclose()

app = FastAPI(lifespan=lifespan)
```

Единственное исключение — `Chat` с **callable** `resources=`
(например, `resources=lambda: build_resources()`): свежий `RuntimeResources`
— и, как правило, свежий провайдер + HTTP-клиент — собирается на каждый ход,
поэтому `stream()` сам закрывает его после хода: больше никто на этот
инстанс не сошлётся. Обычный (не-callable) `resources=` не трогается — он
должен пережить ход.

## Температура и max_tokens — дефолты провайдера, переопределение на вызове

`temperature` и `max_tokens` — это **дефолты уровня провайдера**, которые можно
переопределить на каждом вызове. `None` на обоих уровнях означает, что поле не
отправляется в запрос и API применит свой дефолт.

```python
from reactifact.providers import openai_llm

# дефолт провайдера для всех запросов через него
llm = openai_llm(model="gpt-4o-mini", temperature=0.7, max_tokens=2048)
```

Переопределение на вызове побеждает (например, детерминированный экстрактор,
который не должен «блуждать»):

```python
body = await structured_llm(llm, schema=AnswerBody, user=text, temperature=0.0)
```

Порядок разрешения: **вызов → провайдер → поле не отправляется**. То же
касается `llm_reply`, `ToolUse`, `LLMAgent`/`HITLLMAgent` (атрибуты класса
`temperature`/`max_tokens`, `None` = дефолт провайдера) и image-провайдера
(дефолты `n`/`size`/`quality` в конструкторе, переопределение на вызове через
`generate(prompt, size=...)`). Anthropic всегда отправляет `max_tokens` (этого
требует API) — по умолчанию `4096`, меняется в конструкторе.

## Детерминированный демо-режим

Когда LLM не настроен (`llm_from_env()` возвращает `None` и фолбэк не задан),
демо переходят на детерминированное поведение: заготовленные варианты, фолбэк-
планы, правило-суммирование. Правило строгое: настроенная модель, *вернувшая
ничего полезного*, сообщается честно (никогда не подменяется заглушками
молча); детерминированный фолбэк — только для режима «модели нет вовсе».