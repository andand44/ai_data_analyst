import os
import duckdb
import io
import pandas as pd
from dotenv import load_dotenv
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncOpenAI

load_dotenv()

db = duckdb.connect(database=':memory:')

# Глобальная переменная для запоминания рабочей модели
WORKING_MODEL = None

class Dependencies:
    pass

agent = Agent(
    model=OpenAIModel(model_name="placeholder", provider=OpenAIProvider(openai_client=AsyncOpenAI(base_url='https://api.orcarouter.ai/v1', api_key=os.getenv("ORCA_API_KEY")))),
    deps_type=Dependencies,
    system_prompt=(
        "Ты Senior Data Analyst. Отвечай на вопросы пользователя используя данные из DuckDB. "
        "Таблица называется data. Используй инструмент execute_sql для получения данных. "
        "ВАЖНОЕ ПРАВИЛО. Если запрос выдает ошибку сделай его максимально простым. "
        "НИКОГДА не выдумывай данные. НИКОГДА не выполняй команды изменения данных."
    )
)

@agent.tool
async def execute_sql(ctx: RunContext[Dependencies], query: str) -> str:
    try:
        clean_query = query.strip().upper()
        if not (clean_query.startswith("SELECT") or clean_query.startswith("WITH")):
            return "Ошибка безопасности. Разрешены только запросы на чтение данных."
        if any(cmd in clean_query for cmd in ["DROP", "DELETE", "UPDATE", "INSERT"]):
            return "Ошибка безопасности. Изменение структуры и данных запрещено."

        print(f"DEBUG SQL: {query}")
        result = db.execute(query).df()
        if result.empty:
            return "Запрос вернул пустой результат."
        return result.head(20).to_markdown(index=False)
    except Exception as e:
        return f"Ошибка SQL: {str(e)}"

async def run_agent_with_fallback(prompt: str):
    global WORKING_MODEL
    orca_client = AsyncOpenAI(base_url='https://api.orcarouter.ai/v1', api_key=os.getenv("ORCA_API_KEY"))
    orca_provider = OpenAIProvider(openai_client=orca_client)
    local_client = AsyncOpenAI(base_url='http://localhost:11434/v1', api_key='ollama')
    local_provider = OpenAIProvider(openai_client=local_client)

    try:
        # Если у нас уже есть рабочая модель, пробуем её в первую очередь
        if WORKING_MODEL:
            try:
                new_model = OpenAIModel(model_name=WORKING_MODEL, provider=orca_provider)
                result = await agent.run(prompt, model=new_model)
                return result
            except Exception:
                print("Рабочая модель упала. Ищем новую.")
                WORKING_MODEL = None # Сбрасываем

        # Если рабочей нет или она упала, получаем список
        available_models = []
        try:
            models_response = await orca_client.models.list()
            available_models = [m.id for m in models_response.data if m.id.endswith("-free")]
        except Exception:
            available_models = ["deepseek/deepseek-v4-flash-free", "tencent/hy3-free"]

        for model_name in available_models:
            try:
                print(f"Пробуем модель: {model_name}")
                new_model = OpenAIModel(model_name=model_name, provider=orca_provider)
                result = await agent.run(prompt, model=new_model)
                WORKING_MODEL = model_name # ЗАПОМИНАЕМ УСПЕШНУЮ МОДЕЛЬ
                return result
            except Exception as e:
                print(f"Ошибка с {model_name}. Переключаемся...")
                
        print("Все облачные модели недоступны. Переключаемся на локальную.")
        local_model = OpenAIModel(model_name='qwen2.5:1.5b', provider=local_provider)
        result = await agent.run(prompt, model=local_model)
        return result

    except Exception as e:
        raise Exception(f"Критическая ошибка: {e}")
    finally:
        await orca_client.close()
        await local_client.close()

def load_csv_to_db(file_bytes):
    global db
    db = duckdb.connect(database=':memory:')
    try:
        df = pd.read_csv(io.BytesIO(file_bytes), on_bad_lines='skip', encoding='utf-8')
    except UnicodeDecodeError:
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), on_bad_lines='skip', encoding='cp1251')
        except Exception:
            raise ValueError("Файл не является текстовым CSV. Возможно, вы загрузили видео или картинку.")
    except Exception as e:
        raise ValueError(f"Ошибка чтения файла: {e}")

    if df.empty:
        raise ValueError("Файл пустой или не содержит данных.")

    db.register("data", df)
    return db.execute("DESCRIBE data").df().to_string(index=False)

def clear_db():
    global db
    db = duckdb.connect(database=':memory:')
    print("База данных очищена.")