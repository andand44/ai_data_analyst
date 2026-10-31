import streamlit as st
import asyncio
import os
import uuid
import sqlite3
from agent import run_agent_with_fallback, load_csv_to_db, clear_db
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="AI Data Analyst", page_icon="📊", layout="wide")

# --- КАСТОМНЫЙ ДИЗАЙН (CSS) ---
st.markdown("""
<style>
    /* Красивая кнопка Новый чат */
    div[data-testid="stSidebar"] button[kind="secondary"][data-testid="baseButton-secondary"] {
        background-color: #f0f2f6 !important;
        color: #31333f !important;
        border: 1px solid #d6d6d6 !important;
        border-radius: 8px !important;
        text-align: center !important;
        justify-content: center !important;
    }
    div[data-testid="stSidebar"] button[kind="secondary"][data-testid="baseButton-secondary"]:hover {
        background-color: #e0e2e6 !important;
    }
    /* Кнопки истории чатов делаем плоскими и выравниваем по левому краю */
    div[data-testid="stSidebar"] button[kind="secondary"][data-testid="baseButton-secondary"]:has(div:contains("📄")),
    div[data-testid="stSidebar"] button[kind="secondary"][data-testid="baseButton-secondary"]:has(div:contains("🟢")) {
        background-color: transparent !important;
        border: none !important;
        text-align: left !important;
        justify-content: flex-start !important;
        padding: 10px !important;
    }
    div[data-testid="stSidebar"] button[kind="secondary"][data-testid="baseButton-secondary"]:has(div:contains("📄")):hover,
    div[data-testid="stSidebar"] button[kind="secondary"][data-testid="baseButton-secondary"]:has(div:contains("🟢")):hover {
        background-color: #ececf1 !important;
    }
</style>
""", unsafe_allow_html=True)

DB_PATH = "chat_database.db"
CSV_DIR = "csv_data"
if not os.path.exists(CSV_DIR):
    os.makedirs(CSV_DIR)

# --- АВТО-ОБНОВЛЕНИЕ БАЗЫ ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("SELECT filename FROM chats LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("DROP TABLE IF EXISTS chats")
        c.execute("DROP TABLE IF EXISTS messages")
    
    c.execute('''CREATE TABLE IF NOT EXISTS chats 
                 (id TEXT PRIMARY KEY, title TEXT, filename TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT, role TEXT, content TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- ФУНКЦИИ РАБОТЫ С БД ---
def get_all_chats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, title, filename FROM chats ORDER BY rowid DESC")
    chats = c.fetchall()
    conn.close()
    return chats

def get_messages(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT role, content FROM messages WHERE chat_id = ?", (chat_id,))
    msgs = [{"role": r, "content": c} for r, c in c.fetchall()]
    conn.close()
    return msgs

def save_message(chat_id, role, content):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)", (chat_id, role, content))
    
    c.execute("SELECT title FROM chats WHERE id = ?", (chat_id,))
    if not c.fetchone():
        title = content[:30] + "..." if len(content) > 30 else content
        filename = st.session_state.get("current_filename", "Файл не указан")
        c.execute("INSERT INTO chats (id, title, filename) VALUES (?, ?, ?)", (chat_id, title, filename))
    conn.commit()
    conn.close()

def delete_chat(chat_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
    c.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
    conn.commit()
    conn.close()
    
    csv_path = os.path.join(CSV_DIR, f"{chat_id}.csv")
    if os.path.exists(csv_path):
        os.remove(csv_path)

# --- ИНИЦИАЛИЗАЦИЯ СТЕЙТА ---
if "current_chat_id" not in st.session_state:
    st.session_state.current_chat_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "file_loaded" not in st.session_state:
    st.session_state.file_loaded = False
if "schema" not in st.session_state:
    st.session_state.schema = ""
if "is_generating" not in st.session_state:
    st.session_state.is_generating = False
if "current_filename" not in st.session_state:
    st.session_state.current_filename = ""

if "app_initialized" not in st.session_state:
    clear_db()
    st.session_state.app_initialized = True

def load_chat(chat_id):
    st.session_state.is_generating = False
    st.session_state.current_chat_id = chat_id
    st.session_state.messages = get_messages(chat_id)
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT filename FROM chats WHERE id = ?", (chat_id,))
    row = c.fetchone()
    st.session_state.current_filename = row[0] if row else "Файл не указан"
    conn.close()

    csv_path = os.path.join(CSV_DIR, f"{chat_id}.csv")
    if os.path.exists(csv_path):
        with open(csv_path, "rb") as f:
            file_bytes = f.read()
        st.session_state.schema = load_csv_to_db(file_bytes)
        st.session_state.file_loaded = True
    else:
        clear_db()
        st.session_state.file_loaded = False
        st.session_state.schema = ""

# Переменная блокировки всего интерфейса
is_ui_locked = st.session_state.is_generating

# --- БОКОВАЯ ПАНЕЛЬ ---
with st.sidebar:
    # Блокируем кнопку создания, пока модель думает
    if st.button("➕ Новый чат", use_container_width=True, disabled=is_ui_locked):
        clear_db()
        st.session_state.current_chat_id = None
        st.session_state.messages = []
        st.session_state.file_loaded = False
        st.session_state.schema = ""
        st.session_state.current_filename = ""
        st.session_state.is_generating = False
        st.rerun()

    st.divider()

    if not st.session_state.file_loaded:
        st.header("Загрузка данных")
        # Блокируем загрузку файлов во время генерации
        uploaded_file = st.file_uploader("Выберите CSV файл", type=["csv"], disabled=is_ui_locked)
        
        if uploaded_file is not None:
            with st.spinner("Анализ файла..."):
                try:
                    file_bytes = uploaded_file.getvalue()
                    st.session_state.schema = load_csv_to_db(file_bytes)
                    st.session_state.file_loaded = True
                    st.session_state.current_filename = uploaded_file.name
                    
                    if not st.session_state.current_chat_id:
                        st.session_state.current_chat_id = str(uuid.uuid4())[:8]
                        
                    csv_path = os.path.join(CSV_DIR, f"{st.session_state.current_chat_id}.csv")
                    with open(csv_path, "wb") as f:
                        f.write(file_bytes)
                        
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")
    else:
        st.success(f"✅ {st.session_state.current_filename}")
        with st.expander("Показать схему данных"):
            st.code(st.session_state.schema)

    st.divider()
    st.header("История чатов")
    
    for chat_id, title, filename in get_all_chats():
        col1, col2 = st.columns([0.85, 0.15])
        with col1:
            # 🌟 ВЫДЕЛЕНИЕ АКТИВНОГО ЧАТА (Зеленая точка)
            prefix = "🟢 " if chat_id == st.session_state.current_chat_id else "📄 "
            display_text = f"{prefix}{filename[:10]}...\n💬 {title[:15]}..." if filename else f"{prefix}💬 {title[:20]}..."
            
            # Блокируем кнопки чатов во время генерации
            if st.button(display_text, key=f"btn_{chat_id}", use_container_width=True, disabled=is_ui_locked):
                if st.session_state.current_chat_id != chat_id:
                    load_chat(chat_id)
                    st.rerun()
        with col2:
            # Блокируем удаление во время генерации
            if st.button("🗑️", key=f"del_{chat_id}", disabled=is_ui_locked):
                delete_chat(chat_id)
                if st.session_state.current_chat_id == chat_id:
                    st.session_state.current_chat_id = None
                    st.session_state.messages = []
                    st.session_state.file_loaded = False
                    st.session_state.current_filename = ""
                    st.session_state.is_generating = False
                    clear_db()
                st.rerun()

# --- ОСНОВНАЯ ОБЛАСТЬ ---
if not st.session_state.file_loaded:
    st.markdown("""
        <div style="text-align: center; margin-top: 100px;">
            <h1>👋 Привет! Я AI Data Analyst</h1>
            <p style="font-size: 20px; color: gray;">
                Загрузите CSV-файл в левой панели, и я помогу вам проанализировать его с помощью SQL.
            </p>
        </div>
    """, unsafe_allow_html=True)
else:
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    if st.session_state.is_generating:
        st.info("⏳ Модель генерирует ответ. Пожалуйста, подождите...")

    # Поле ввода блокируется, если идет генерация
    if prompt := st.chat_input("Задайте вопрос по данным...", disabled=is_ui_locked):
        if not st.session_state.current_chat_id:
            st.session_state.current_chat_id = str(uuid.uuid4())[:8]
        
        save_message(st.session_state.current_chat_id, "user", prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.session_state.is_generating = True
        st.rerun()

# --- ГЕНЕРАЦИЯ ---
if st.session_state.is_generating and st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
    generating_chat_id = st.session_state.current_chat_id
    prompt = st.session_state.messages[-1]["content"]
    
    with st.chat_message("assistant"):
        with st.spinner("Думаю и пишу SQL..."):
            try:
                result = asyncio.run(run_agent_with_fallback(prompt))
                
                # Проверка на всякий случай (хотя UI заблокирован)
                if st.session_state.current_chat_id != generating_chat_id:
                    st.session_state.is_generating = False
                    st.rerun()
                
                answer_text = result.output
                st.markdown(answer_text)
                
                save_message(st.session_state.current_chat_id, "assistant", answer_text)
                st.session_state.messages.append({"role": "assistant", "content": answer_text})
            except Exception as e:
                st.error(f"Ошибка: {e}")
            finally:
                st.session_state.is_generating = False
                st.rerun()