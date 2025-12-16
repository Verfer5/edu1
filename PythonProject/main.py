import os
import logging
import asyncio
import json
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from pymysql import connect, Connection
from pymysql.cursors import DictCursor
from dotenv import load_dotenv

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Загрузка переменных среды
load_dotenv()
API_TOKEN = os.getenv('API_TOKEN')

if not API_TOKEN:
    logger.error("API_TOKEN не установлен! Установите токен в переменных окружения или .env файле")
    raise ValueError("API_TOKEN обязателен для работы бота")

# Настройки подключения к базе данных из переменных окружения
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'db': os.getenv('DB_NAME'),
    'charset': os.getenv('DB_CHARSET', 'utf8mb4'),
    'cursorclass': DictCursor
}

# Проверка наличия обязательных параметров БД
if not DB_CONFIG['user'] or not DB_CONFIG['password'] or not DB_CONFIG['db']:
    logger.error("Не все параметры подключения к БД установлены! Проверьте файл .env")
    raise ValueError("Параметры подключения к БД обязательны для работы бота")

# Инициализация бота
storage = MemoryStorage()
bot = Bot(
    token=API_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN)
)
dp = Dispatcher(storage=storage)

# Состояния FSM
class QuizStates(StatesGroup):
    ChoosingCategory = State()
    WaitingForAnswer = State()

class LessonStates(StatesGroup):
    BrowsingTopics = State()
    ViewingLesson = State()

class AdminStates(StatesGroup):
    WaitingLessonTitle = State()
    WaitingLessonContent = State()
    WaitingLessonTopic = State()
    WaitingLessonOrder = State()
    WaitingQuestion = State()
    WaitingAnswer = State()
    WaitingOptions = State()
    WaitingCategory = State()
    WaitingExplanation = State()
    WaitingBroadcastMessage = State()
    WaitingAdminAdd = State()
    WaitingAdminRemove = State()

# ================== БАЗА ДАННЫХ ================== #
# Функции для работы с базой данных

# Создание подключения к базе данных
def get_connection() -> Connection:
    """Создает и возвращает подключение к базе данных."""
    try:
        return connect(**DB_CONFIG)
    except Exception as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        raise

# Синхронизация демо-уроков из кода с базой данных
def sync_demo_lessons(conn, demo_lessons: list):
    """
    Синхронизирует демо-уроки из кода с базой данных.
    Обновляет существующие, добавляет новые, удаляет устаревшие.
    """
    with conn.cursor() as cur:
        # Получаем все демо-уроки из БД (только из категории cpp-basics)
        cur.execute(
            "SELECT id, title, content, topic, order_num FROM lessons WHERE topic = %s",
            ("cpp-basics",)
        )
        db_lessons = {f"{lesson['topic']}_{lesson['order_num']}": lesson for lesson in cur.fetchall()}
        
        # Создаем множество идентификаторов уроков из кода
        code_lessons_keys = {f"{lesson['topic']}_{lesson['order_num']}" for lesson in demo_lessons}
        
        # Обновляем или добавляем уроки из кода
        for lesson in demo_lessons:
            key = f"{lesson['topic']}_{lesson['order_num']}"
            
            if key in db_lessons:
                # Обновляем существующий урок
                cur.execute(
                    """UPDATE lessons 
                    SET title = %s, content = %s 
                    WHERE topic = %s AND order_num = %s""",
                    (lesson['title'], lesson['content'], lesson['topic'], lesson['order_num'])
                )
                if cur.rowcount > 0:
                    logger.info(f"Обновлен урок: {lesson['title']}")
            else:
                # Добавляем новый урок
                cur.execute(
                    "INSERT INTO lessons (title, content, topic, order_num) VALUES (%s, %s, %s, %s)",
                    (lesson['title'], lesson['content'], lesson['topic'], lesson['order_num'])
                )
                logger.info(f"Добавлен новый урок: {lesson['title']}")
        
        # Удаляем уроки, которых нет в коде
        lessons_to_delete = set(db_lessons.keys()) - code_lessons_keys
        for key in lessons_to_delete:
            lesson = db_lessons[key]
            # Сначала удаляем связанные записи прогресса
            cur.execute("DELETE FROM user_progress WHERE lesson_id = %s", (lesson['id'],))
            # Затем удаляем сам урок
            cur.execute("DELETE FROM lessons WHERE id = %s", (lesson['id'],))
            logger.info(f"Удален устаревший урок: {lesson['title']}")


# Синхронизация демо-вопросов из кода с базой данных
def sync_demo_questions(conn, demo_questions: list):
    """
    Синхронизирует демо-вопросы из кода с базой данных.
    Обновляет существующие, добавляет новые, удаляет устаревшие.
    """
    with conn.cursor() as cur:
        # Получаем все демо-вопросы из БД (только из категории cpp-basics)
        cur.execute(
            "SELECT id, question, answer, options, category, difficulty, explanation FROM questions WHERE category = %s",
            ("cpp-basics",)
        )
        db_questions = {q['question']: q for q in cur.fetchall()}
        
        # Создаем множество текстов вопросов из кода
        code_questions_texts = {q['question'] for q in demo_questions}
        
        # Обновляем или добавляем вопросы из кода
        for question in demo_questions:
            if question['question'] in db_questions:
                # Обновляем существующий вопрос
                db_q = db_questions[question['question']]
                cur.execute(
                    """UPDATE questions 
                    SET answer = %s, options = %s, difficulty = %s, explanation = %s 
                    WHERE question = %s AND category = %s""",
                    (
                        question['answer'],
                        json.dumps(question['options']),
                        question['difficulty'],
                        question['explanation'],
                        question['question'],
                        question['category']
                    )
                )
                if cur.rowcount > 0:
                    logger.info(f"Обновлен вопрос: {question['question'][:50]}...")
            else:
                # Добавляем новый вопрос
                cur.execute(
                    """INSERT INTO questions (question, answer, options, category, difficulty, explanation) 
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        question['question'],
                        question['answer'],
                        json.dumps(question['options']),
                        question['category'],
                        question['difficulty'],
                        question['explanation']
                    )
                )
                logger.info(f"Добавлен новый вопрос: {question['question'][:50]}...")
        
        # Удаляем вопросы, которых нет в коде
        questions_to_delete = set(db_questions.keys()) - code_questions_texts
        for question_text in questions_to_delete:
            q = db_questions[question_text]
            # Сначала удаляем связанные ответы пользователей
            cur.execute("DELETE FROM user_answers WHERE question_id = %s", (q['id'],))
            # Затем удаляем сам вопрос
            cur.execute("DELETE FROM questions WHERE id = %s", (q['id'],))
            logger.info(f"Удален устаревший вопрос: {question_text[:50]}...")


# Инициализация базы данных (создание таблиц и синхронизация демо-данных)
def init_db():
    try:
        conn = get_connection()
    except Exception as e:
        logger.error(f"Не удалось подключиться к базе данных: {e}")
        logger.error(f"Проверьте настройки: host={DB_CONFIG['host']}, user={DB_CONFIG['user']}, db={DB_CONFIG['db']}")
        raise
    try:
        with conn.cursor() as cur:
            # Таблица пользователей
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id BIGINT PRIMARY KEY,
                    username VARCHAR(255),
                    score INT DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    is_admin BOOLEAN DEFAULT FALSE
                )
            """)

            # Таблица уроков
            cur.execute("""
                CREATE TABLE IF NOT EXISTS lessons (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    title VARCHAR(255) NOT NULL,
                    content TEXT NOT NULL,
                    topic VARCHAR(100) NOT NULL,
                    order_num INT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Таблица вопросов
            cur.execute("""
                CREATE TABLE IF NOT EXISTS questions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    question TEXT NOT NULL,
                    answer VARCHAR(255) NOT NULL,
                    options JSON NOT NULL,
                    category VARCHAR(100) NOT NULL,
                    difficulty ENUM('easy', 'medium', 'hard') DEFAULT 'easy',
                    explanation TEXT
                )
            """)

            # Таблица прогресса
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_progress (
                    user_id BIGINT,
                    lesson_id INT,
                    completed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, lesson_id),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (lesson_id) REFERENCES lessons(id)
                )
            """)

            # Таблица ответов пользователей
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_answers (
                    user_id BIGINT,
                    question_id INT,
                    answer VARCHAR(255) NOT NULL,
                    is_correct BOOLEAN NOT NULL,
                    answered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, question_id),
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (question_id) REFERENCES questions(id)
                )
            """)

            # Таблица достижений
            cur.execute("""
                CREATE TABLE IF NOT EXISTS achievements (
                    user_id BIGINT,
                    name VARCHAR(255),
                    unlocked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, name),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Определение демо-данных для синхронизации
            demo_lessons = [
                    {
                        "title": "⚙️ Введение в C++",
                        "content": (
                            "**C++** - компилируемый язык программирования общего назначения.\n\n"
                            "✨ Основные особенности:\n"
                            "- Статическая типизация\n"
                            "- Ручное управление памятью\n"
                            "- Высокая производительность\n"
                            "- Богатая стандартная библиотека STL\n\n"
                            "Пример первой программы:\n"
                            "```cpp\n#include <iostream>\nint main() {\n    std::cout << \"Привет, мир!\" << std::endl;\n    return 0;\n}\n```"
                        ),
                        "topic": "cpp-basics",
                        "order_num": 1
                    },
                    {
                        "title": "🔢 Переменные и типы данных",
                        "content": (
                            "**Переменные** используются для хранения данных в программе.\n\n"
                            "📚 Основные типы данных:\n"
                            "- `int`: целые числа (42)\n"
                            "- `float`: числа с плавающей точкой (3.14f)\n"
                            "- `double`: числа двойной точности (3.14)\n"
                            "- `char`: символы ('A')\n"
                            "- `bool`: логические значения (true/false)\n"
                            "- `string`: строки (\"Привет\")\n\n"
                            "Примеры:\n"
                            "```cpp\n// int: целые числа\nint age = 25;\n\n// float: числа с плавающей точкой\nfloat height = 1.75f;\n\n// double: числа двойной точности\ndouble price = 99.95;\n\n// char: символы\nchar grade = 'A';\n\n// bool: логические значения\nbool is_active = true;\n\n// string: строки\nstd::string name = \"Анна\";\n```"
                        ),
                        "topic": "cpp-basics",
                        "order_num": 2
                    },
                    {
                        "title": "🔀 Условные операторы и циклы",
                        "content": (
                            "**Условные операторы** позволяют выполнять код в зависимости от условий.\n\n"
                            "📚 Основные конструкции:\n"
                            "- `if/else`: условное выполнение\n"
                            "- `switch`: выбор из нескольких вариантов\n"
                            "- `for`: цикл с известным количеством итераций\n"
                            "- `while`: цикл с условием\n"
                            "- `do-while`: цикл с постусловием\n\n"
                            "Примеры:\n"
                            "```cpp\n// if/else: условное выполнение\nif (age >= 18) {\n    std::cout << \"Совершеннолетний\" << std::endl;\n} else {\n    std::cout << \"Несовершеннолетний\" << std::endl;\n}\n\n// switch: выбор из нескольких вариантов\nswitch (grade) {\n    case 'A': std::cout << \"Отлично\" << std::endl; break;\n    case 'B': std::cout << \"Хорошо\" << std::endl; break;\n    default: std::cout << \"Другое\" << std::endl;\n}\n\n// for: цикл с известным количеством итераций\nfor (int i = 0; i < 10; i++) {\n    std::cout << i << std::endl;\n}\n\n// while: цикл с условием\nint count = 0;\nwhile (count < 5) {\n    std::cout << count << std::endl;\n    count++;\n}\n\n// do-while: цикл с постусловием\nint num = 0;\ndo {\n    std::cout << num << std::endl;\n    num++;\n} while (num < 3);\n```"
                        ),
                        "topic": "cpp-basics",
                        "order_num": 3
                    },
                    {
                        "title": "📦 Функции и области видимости",
                        "content": (
                            "**Функции** - это блоки кода, которые можно вызывать многократно.\n\n"
                            "✨ Ключевые концепции:\n"
                            "- Объявление функции: `тип имя(параметры)`\n"
                            "- Возврат значения: `return значение`\n"
                            "- Передача по значению и по ссылке\n"
                            "- Области видимости: локальная, глобальная\n"
                            "- Перегрузка функций\n\n"
                            "Примеры:\n"
                            "```cpp\n// Объявление функции: тип имя(параметры)\nint add(int a, int b) {\n    // Возврат значения: return значение\n    return a + b;\n}\n\n// Передача по значению (создается копия)\nvoid incrementByValue(int x) {\n    x++;  // изменяется только копия, оригинал не меняется\n}\n\n// Передача по ссылке (работа с оригиналом)\nvoid swap(int& x, int& y) {\n    int temp = x;\n    x = y;\n    y = temp;  // оригинальные переменные изменяются\n}\n\n// Области видимости: локальная, глобальная\nint global = 10;  // глобальная переменная (доступна везде)\n\nvoid example() {\n    int local = 5;  // локальная переменная (только в этой функции)\n    std::cout << global << \" \" << local << std::endl;\n}\n\n// Перегрузка функций (одно имя, разные параметры)\nint multiply(int a, int b) {\n    return a * b;\n}\n\ndouble multiply(double a, double b) {\n    return a * b;\n}\n```"
                        ),
                        "topic": "cpp-basics",
                        "order_num": 4
                    },
                    {
                        "title": "🎯 Указатели и ссылки",
                        "content": (
                            "**Указатели** и **ссылки** - мощные инструменты для работы с памятью.\n\n"
                            "📚 Основные концепции:\n"
                            "- Указатель: переменная, хранящая адрес памяти\n"
                            "- Ссылка: альтернативное имя для переменной\n"
                            "- Оператор `&`: получение адреса\n"
                            "- Оператор `*`: разыменование указателя\n"
                            "- Динамическая память: `new` и `delete`\n\n"
                            "Примеры:\n"
                            "```cpp\nint x = 10;\n\n// Указатель: переменная, хранящая адрес памяти\nint* ptr = &x;  // оператор & получает адрес переменной x\n\n// Ссылка: альтернативное имя для переменной\nint& ref = x;\n\n// Оператор *: разыменование указателя (получение значения)\n*ptr = 20;  // изменение значения через указатель\n\n// Изменение через ссылку\nref = 30;\n\n// Динамическая память: new и delete\nint* dynamic = new int(42);  // выделение памяти через new\nstd::cout << *dynamic << std::endl;\ndelete dynamic;  // освобождение памяти через delete\n```"
                        ),
                        "topic": "cpp-basics",
                        "order_num": 5
                    }
                ]
            
            # Синхронизация уроков с БД
            sync_demo_lessons(conn, demo_lessons)
            
            # Определение демо-вопросов для синхронизации
            demo_questions = [
                    # Урок 1: Введение в C++ (2 вопроса)
                    {
                        "question": "Какой заголовочный файл нужен для работы с std::cout?",
                        "answer": "<iostream>",
                        "options": ["<iostream>", "<stdio.h>", "<cout>", "<stream>"],
                        "category": "cpp-basics",
                        "difficulty": "easy",
                        "explanation": "<iostream> содержит определения для потоков ввода-вывода, включая std::cout"
                    },
                    {
                        "question": "Какая функция является точкой входа в программу на C++?",
                        "answer": "main",
                        "options": ["main", "start", "begin", "init"],
                        "category": "cpp-basics",
                        "difficulty": "easy",
                        "explanation": "Функция main() - обязательная точка входа в любую программу на C++"
                    },
                    # Урок 2: Переменные и типы данных (2 вопроса)
                    {
                        "question": "Какой тип данных используется для хранения одного символа?",
                        "answer": "char",
                        "options": ["char", "string", "int", "char*"],
                        "category": "cpp-basics",
                        "difficulty": "easy",
                        "explanation": "Тип char используется для хранения одного символа"
                    },
                    {
                        "question": "Какой тип данных у числа 5.5 в C++?",
                        "answer": "double",
                        "options": ["int", "double", "float", "char"],
                        "category": "cpp-basics",
                        "difficulty": "medium",
                        "explanation": "Числа с плавающей точкой без суффикса по умолчанию имеют тип double"
                    },
                    # Урок 3: Условные операторы и циклы (2 вопроса)
                    {
                        "question": "Какой цикл выполнится хотя бы один раз?",
                        "answer": "do-while",
                        "options": ["for", "while", "do-while", "foreach"],
                        "category": "cpp-basics",
                        "difficulty": "easy",
                        "explanation": "Цикл do-while проверяет условие после выполнения тела, поэтому выполнится минимум один раз"
                    },
                    {
                        "question": "Что выведет код: int x = 5; if (x > 3) std::cout << \"Да\"; else std::cout << \"Нет\";",
                        "answer": "Да",
                        "options": ["Да", "Нет", "Ошибка", "Ничего"],
                        "category": "cpp-basics",
                        "difficulty": "easy",
                        "explanation": "Условие x > 3 истинно (5 > 3), поэтому выполнится блок if и выведется \"Да\""
                    },
                    # Урок 4: Функции и области видимости (2 вопроса)
                    {
                        "question": "Как объявить функцию в C++?",
                        "answer": "тип имя(параметры)",
                        "options": ["func имя()", "тип имя(параметры)", "function имя()", "def имя()"],
                        "category": "cpp-basics",
                        "difficulty": "easy",
                        "explanation": "Функции в C++ объявляются с указанием типа возвращаемого значения, имени функции и параметров"
                    },
                    {
                        "question": "Что такое перегрузка функций?",
                        "answer": "Определение нескольких функций с одним именем, но разными параметрами",
                        "options": [
                            "Определение нескольких функций с одним именем, но разными параметрами",
                            "Изменение имени функции",
                            "Удаление функции",
                            "Копирование функции"
                        ],
                        "category": "cpp-basics",
                        "difficulty": "medium",
                        "explanation": "Перегрузка позволяет определить несколько функций с одинаковым именем, но разными типами или количеством параметров"
                    },
                    # Урок 5: Указатели и ссылки (2 вопроса)
                    {
                        "question": "Что такое указатель в C++?",
                        "answer": "Переменная, хранящая адрес памяти",
                        "options": ["Переменная, хранящая адрес памяти", "Тип данных", "Функция", "Оператор"],
                        "category": "cpp-basics",
                        "difficulty": "medium",
                        "explanation": "Указатель - это переменная, которая хранит адрес другой переменной в памяти"
                    },
                    {
                        "question": "Как правильно освободить память, выделенную через new?",
                        "answer": "delete",
                        "options": ["delete", "free", "remove", "clear"],
                        "category": "cpp-basics",
                        "difficulty": "medium",
                        "explanation": "Оператор delete освобождает память, выделенную через new. Для массивов используется delete[]"
                    }
                ]
            
            # Синхронизация вопросов с БД
            sync_demo_questions(conn, demo_questions)

            conn.commit()
            logger.info("✅ Синхронизация демо-данных завершена")
    finally:
        conn.close()

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ================== #
# Проверка прав администратора (проверяет флаг is_admin в БД)
async def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT is_admin FROM users WHERE id = %s", (user_id,))
            result = cur.fetchone()
            return bool(result and result.get('is_admin', False))
    except Exception as e:
        logger.error(f"Ошибка проверки администратора: {e}")
        return False
    finally:
        conn.close()


# ================== ОБРАБОТЧИКИ УРОКОВ ================== #
# Переход к следующему уроку в теме
@dp.callback_query(F.data.startswith("next_lesson:"))
async def next_lesson_handler(cq: types.CallbackQuery):
    try:
        current_lesson_id = int(cq.data.split(":")[1])
    except (ValueError, IndexError):
        await cq.answer("❌ Ошибка: неверный формат данных", show_alert=True)
        return
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT topic, order_num FROM lessons WHERE id = %s",
                (current_lesson_id,)
            )
            lesson_info = cur.fetchone()
            
            if not lesson_info:
                await cq.answer("❌ Урок не найден", show_alert=True)
                return

            cur.execute(
                "SELECT id FROM lessons "
                "WHERE topic = %s AND order_num > %s "
                "ORDER BY order_num ASC LIMIT 1",
                (lesson_info['topic'], lesson_info['order_num'])
            )
            next_lesson = cur.fetchone()
    except Exception as e:
        logger.error(f"Ошибка в next_lesson_handler: {e}")
        await cq.answer("❌ Произошла ошибка", show_alert=True)
    finally:
        conn.close()

    if next_lesson:
        await show_lesson(cq, next_lesson['id'])
    else:
        await cq.answer("🎉 Это последний урок в теме!")


# Обработка команды /start (регистрация пользователя и показ главного меню)
@dp.message(Command('start'))
async def cmd_start(msg: types.Message):
    user = msg.from_user
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO users (id, username) 
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE username = VALUES(username)""",
                (user.id, user.full_name)
            )
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")
    finally:
        conn.close()

    text = (
        f"👋 Привет, {user.full_name}!\n\n"
        "🚀 Я твой помощник в изучении C++!\n\n"
        "📚 Доступные команды:\n"
        "/lessons - Уроки по C++\n"
        "/quiz - Проверь свои знания\n"
        "/progress - Твой прогресс\n"
        "/achievements - Достижения\n"
        "/help - Справка по командам"
    )
    await msg.answer(text)


# Показ главного меню (то же, что и /start, но можно вызвать из callback)
async def show_main_menu(msg_or_cq):
    """Показывает главное меню (то же, что и /start)."""
    if isinstance(msg_or_cq, types.Message):
        user = msg_or_cq.from_user
        send_func = msg_or_cq.answer
    else:  # CallbackQuery
        user = msg_or_cq.from_user
        send_func = msg_or_cq.message.answer
    
    text = (
        f"👋 Привет, {user.full_name}!\n\n"
        "🚀 Я твой помощник в изучении C++!\n\n"
        "📚 Доступные команды:\n"
        "/lessons - Уроки по C++\n"
        "/quiz - Проверь свои знания\n"
        "/progress - Твой прогресс\n"
        "/achievements - Достижения\n"
        "/help - Справка по командам"
    )
    await send_func(text)


# Обработчик возврата на главное меню (очищает состояние FSM)
@dp.callback_query(F.data == "main_menu")
async def main_menu_handler(cq: types.CallbackQuery, state: FSMContext):
    """Обработчик возврата на главное меню."""
    await state.clear()
    await show_main_menu(cq)
    await cq.answer()


# Обработка команды /help (показ справки по командам)
@dp.message(Command('help'))
async def cmd_help(msg: types.Message):
    text = (
        "📖 Список доступных команд:\n\n"
        "/start - Начать работу с ботом\n"
        "/lessons - Список уроков\n"
        "/quiz - Начать викторину\n"
        "/progress - Прогресс обучения\n"
        "/achievements - Полученные достижения\n"
        "/leaderboard - Топ пользователей\n"
        "/help - Эта справка"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    await msg.answer(text, reply_markup=builder.as_markup())

# Обработка команды /lessons (показ списка тем для изучения)
@dp.message(Command('lessons'))
async def cmd_lessons(msg: types.Message, state: FSMContext):
    """Показывает список тем для изучения."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT topic FROM lessons ORDER BY topic")
            topics = [row['topic'] for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Ошибка получения тем: {e}")
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="main_menu")
        await msg.answer("❌ Произошла ошибка при загрузке тем", reply_markup=builder.as_markup())
        return
    finally:
        conn.close()

    if not topics:
        await msg.answer("📚 Уроки пока не добавлены. Обратитесь к администратору.")
        return

    builder = InlineKeyboardBuilder()
    for topic in topics:
        builder.button(text=topic.replace('-', ' ').title(), callback_data=f"topic:{topic}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await msg.answer("📚 Выберите тему для изучения:", reply_markup=builder.as_markup())
    await state.set_state(LessonStates.BrowsingTopics)

# Показ списка уроков выбранной темы
@dp.callback_query(F.data.startswith("topic:"), LessonStates.BrowsingTopics)
async def show_topic_lessons(cq: types.CallbackQuery, state: FSMContext):
    try:
        topic = cq.data.split(":")[1]
    except IndexError:
        await cq.answer("❌ Ошибка: неверный формат данных", show_alert=True)
        return
    
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT l.id, l.title, up.user_id IS NOT NULL AS completed "
                "FROM lessons l "
                "LEFT JOIN user_progress up ON l.id = up.lesson_id AND up.user_id = %s "
                "WHERE l.topic = %s "
                "ORDER BY l.order_num",
                (cq.from_user.id, topic)
            )
            lessons = cur.fetchall()
    except Exception as e:
        logger.error(f"Ошибка в show_topic_lessons: {e}")
        await cq.answer("❌ Произошла ошибка", show_alert=True)
        return
    finally:
        conn.close()

    if not lessons:
        await cq.answer("📚 Уроки по этой теме пока не добавлены", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for lesson in lessons:
        emoji = "✅" if lesson['completed'] else "📘"
        builder.button(
            text=f"{emoji} {lesson['title']}",
            callback_data=f"lesson:{lesson['id']}"
        )
    builder.button(text="🔙 Назад", callback_data="back_to_topics")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    try:
        await cq.message.edit_text(
            f"📖 Тема: {topic.replace('-', ' ').title()}\n"
            "Выберите урок:",
            reply_markup=builder.as_markup()
        )
        await cq.answer()
    except Exception as e:
        logger.error(f"Ошибка редактирования сообщения: {e}")
        await cq.answer("❌ Не удалось обновить сообщение")


# Показ конкретного урока (сохраняет прогресс и проверяет достижения)
@dp.callback_query(F.data.startswith("lesson:"))
async def show_lesson(cq: types.CallbackQuery, lesson_id: int = None):
    # Если lesson_id не передан, извлекаем его из callback_data
    if lesson_id is None:
        try:
            lesson_id = int(cq.data.split(":")[1])
        except (ValueError, IndexError):
            await cq.answer("❌ Ошибка: неверный формат данных", show_alert=True)
            return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM lessons WHERE id = %s", (lesson_id,))
            lesson = cur.fetchone()
            
            if not lesson:
                await cq.answer("❌ Урок не найден", show_alert=True)
                return

            cur.execute(
                "INSERT IGNORE INTO user_progress (user_id, lesson_id) "
                "VALUES (%s, %s)",
                (cq.from_user.id, lesson_id)
            )
            
            # Проверяем достижение "Первые шаги в теории" - за прохождение первого урока
            cur.execute(
                "SELECT COUNT(*) as completed_count FROM user_progress WHERE user_id = %s",
                (cq.from_user.id,)
            )
            completed_lessons_count = cur.fetchone()['completed_count']
            
            if completed_lessons_count == 1:
                achievement_name = "Первые шаги в теории"
                cur.execute(
                    """INSERT IGNORE INTO achievements 
                    (user_id, name) VALUES (%s, %s)""",
                    (cq.from_user.id, achievement_name)
                )
            
            conn.commit()
    except Exception as e:
        logger.error(f"Ошибка в show_lesson: {e}")
        await cq.answer("❌ Произошла ошибка при загрузке урока", show_alert=True)
        return
    finally:
        conn.close()

    text = (
        f"📚 *{lesson['title']}*\n\n"
        f"{lesson['content']}\n\n"
        "_Прогресс сохранён_ ✅"
    )

    builder = InlineKeyboardBuilder()
    builder.button(text="📚 Следующий урок", callback_data=f"next_lesson:{lesson_id}")
    builder.button(text="🔙 К урокам", callback_data="back_to_lessons")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    try:
        await cq.message.answer(text, reply_markup=builder.as_markup())
        await cq.answer()
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")
        await cq.answer("❌ Не удалось отправить сообщение")

# Возврат к списку уроков текущей темы
@dp.callback_query(F.data == "back_to_lessons")
async def back_to_lessons_handler(cq: types.CallbackQuery, state: FSMContext):
    """Возвращает к списку тем."""
    await cmd_lessons(cq.message, state)
    await cq.answer()

# Возврат к списку тем
@dp.callback_query(F.data == "back_to_topics")
async def back_to_topics_handler(cq: types.CallbackQuery, state: FSMContext):
    """Возвращает к списку тем."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT topic FROM lessons ORDER BY topic")
            topics = [row['topic'] for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Ошибка получения тем: {e}")
        await cq.answer("❌ Произошла ошибка", show_alert=True)
        return
    finally:
        conn.close()

    if not topics:
        await cq.answer("📚 Уроки пока не добавлены", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    for topic in topics:
        builder.button(text=topic.replace('-', ' ').title(), callback_data=f"topic:{topic}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    try:
        await cq.message.edit_text(
            "📚 Выберите тему для изучения:",
            reply_markup=builder.as_markup()
        )
        await state.set_state(LessonStates.BrowsingTopics)
        await cq.answer()
    except Exception as e:
        logger.error(f"Ошибка редактирования сообщения: {e}")
        await cq.answer("❌ Не удалось обновить сообщение")

# Обработка команды /leaderboard (показ таблицы лидеров - топ-10 пользователей)
@dp.message(Command('leaderboard'))
async def cmd_leaderboard(msg: types.Message):
    """Показывает таблицу лидеров."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT username, COALESCE(score, 0) as score FROM users ORDER BY score DESC LIMIT 10"
            )
            leaders = cur.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения лидеров: {e}")
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="main_menu")
        await msg.answer("❌ Произошла ошибка при загрузке таблицы лидеров", reply_markup=builder.as_markup())
        return
    finally:
        conn.close()

    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    
    if not leaders:
        await msg.answer("🏆 Пока нет участников в таблице лидеров", reply_markup=builder.as_markup())
        return

    text = "🏆 Топ-10 пользователей:\n\n"
    medals = ["🥇", "🥈", "🥉"]
    for i, user in enumerate(leaders, 1):
        medal = medals[i-1] if i <= 3 else f"{i}."
        username = user.get('username', 'Неизвестный')
        score = user.get('score', 0)
        text += f"{medal} {username} - {score} баллов\n"

    await msg.answer(text, reply_markup=builder.as_markup())

# ================== ОБРАБОТЧИКИ ВИКТОРИНЫ ================== #
# Запуск викторины после прохождения урока (викторина по теме урока)
@dp.callback_query(F.data.startswith("quiz_after:"))
async def start_lesson_quiz(cq: types.CallbackQuery, state: FSMContext):
    lesson_id = int(cq.data.split(":")[1])

    # Получаем тему урока
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT topic FROM lessons WHERE id = %s",
                (lesson_id,)
            )
            topic = cur.fetchone()['topic']
    finally:
        conn.close()

    # Запускаем викторину по теме
    await state.update_data(category=topic)
    await start_quiz(cq, state)


# Обработка команды /quiz (показ списка категорий для викторины)
@dp.message(Command('quiz'))
async def cmd_quiz(msg: types.Message, state: FSMContext):
    """Показывает список категорий для викторины."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT category FROM questions ORDER BY category")
            categories = [row['category'] for row in cur.fetchall()]
    except Exception as e:
        logger.error(f"Ошибка получения категорий: {e}")
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="main_menu")
        await msg.answer("❌ Произошла ошибка при загрузке категорий", reply_markup=builder.as_markup())
        return
    finally:
        conn.close()

    if not categories:
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="main_menu")
        await msg.answer("🎲 Вопросы пока не добавлены. Обратитесь к администратору.", reply_markup=builder.as_markup())
        return

    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(
            text=category.replace('-', ' ').title(),
            callback_data=f"quiz_category:{category}"
        )
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(2)

    await msg.answer("🎲 Выберите категорию для викторины:", reply_markup=builder.as_markup())
    await state.set_state(QuizStates.ChoosingCategory)

# Начало викторины (поиск неотвеченного вопроса в выбранной категории)
@dp.callback_query(F.data.startswith("quiz_category:"), QuizStates.ChoosingCategory)
async def start_quiz(cq: types.CallbackQuery, state: FSMContext):
    try:
        category = cq.data.split(":")[1]
    except IndexError:
        await cq.answer("❌ Ошибка: неверный формат данных", show_alert=True)
        return
    
    user_id = cq.from_user.id
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT q.* FROM questions q "
                "LEFT JOIN user_answers ua ON q.id = ua.question_id AND ua.user_id = %s AND ua.is_correct = 1 "
                "WHERE q.category = %s AND ua.question_id IS NULL "
                "ORDER BY RAND() LIMIT 1",
                (user_id, category)
            )
            question = cur.fetchone()
    except Exception as e:
        logger.error(f"Ошибка в start_quiz: {e}")
        await cq.answer("❌ Произошла ошибка", show_alert=True)
        return
    finally:
        conn.close()

    if not question:
        await cq.message.answer("🎉 Вы ответили на все вопросы в этой категории!")
        await state.clear()
        await cq.answer()
        # Предлагаем вернуться на главную
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="main_menu")
        await cq.message.answer("Выберите действие:", reply_markup=builder.as_markup())
        return

    builder = InlineKeyboardBuilder()
    options = json.loads(question['options'])
    # Используем индекс вместо полного текста, чтобы избежать превышения лимита callback_data (64 байта)
    for index, option in enumerate(options):
        builder.button(text=option, callback_data=f"answer:{index}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(2)

    await state.update_data(
        question_id=question['id'],
        correct_answer=question['answer'],
        category=category,
        options=options  # Сохраняем варианты ответов для получения по индексу
    )

    await cq.message.answer(
        f"❓ *Вопрос:* {question['question']}\n\n"
        f"_Сложность:_ {question['difficulty'].upper()}",
        reply_markup=builder.as_markup()
    )
    await state.set_state(QuizStates.WaitingForAnswer)
    await cq.answer()


# Обработка ответа пользователя (проверка правильности, начисление баллов, проверка достижений)
@dp.callback_query(F.data.startswith("answer:"), QuizStates.WaitingForAnswer)
async def handle_answer(cq: types.CallbackQuery, state: FSMContext):
    try:
        answer_index = int(cq.data.split(":")[1])
    except (IndexError, ValueError):
        await cq.answer("❌ Ошибка: неверный формат данных", show_alert=True)
        return
    
    data = await state.get_data()
    if 'question_id' not in data or 'correct_answer' not in data or 'options' not in data:
        await cq.answer("❌ Ошибка: данные вопроса не найдены", show_alert=True)
        return
    
    # Получаем текст ответа по индексу
    options = data['options']
    if answer_index < 0 or answer_index >= len(options):
        await cq.answer("❌ Ошибка: неверный индекс ответа", show_alert=True)
        return
    
    user_answer = options[answer_index]
    
    explanation = ""
    response = ""

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Получаем правильный ответ и объяснение
            cur.execute(
                "SELECT answer, explanation FROM questions WHERE id = %s",
                (data['question_id'],)
            )
            question_data = cur.fetchone()
            
            if not question_data:
                await cq.answer("❌ Вопрос не найден", show_alert=True)
                return
                
            correct_answer = question_data['answer']
            explanation = question_data['explanation'] or "Объяснение отсутствует"

            # Проверяем правильность ответа
            is_correct = user_answer == correct_answer

            # Записываем ответ пользователя
            cur.execute(
                """INSERT INTO user_answers 
                (user_id, question_id, answer, is_correct)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                answer = VALUES(answer), 
                is_correct = VALUES(is_correct)""",
                (cq.from_user.id, data['question_id'], user_answer, is_correct)
            )

            # Начисляем баллы за правильный ответ
            if is_correct:
                cur.execute(
                    "UPDATE users SET score = score + 10 WHERE id = %s",
                    (cq.from_user.id,)
                )
                response = "✅ Правильно! +10 баллов"
            else:
                response = f"❌ Неверно! Правильный ответ: {correct_answer}"

            # Проверяем достижения
            # 1. Достижение "Первые шаги в практике" - за правильный ответ на первый вопрос
            if is_correct:
                cur.execute(
                    "SELECT COUNT(*) as correct_count FROM user_answers WHERE user_id = %s AND is_correct = 1",
                    (cq.from_user.id,)
                )
                total_correct = cur.fetchone()['correct_count']
                
                if total_correct == 1:
                    achievement_name = "Первые шаги в практике"
                    cur.execute(
                        """INSERT IGNORE INTO achievements 
                        (user_id, name) VALUES (%s, %s)""",
                        (cq.from_user.id, achievement_name)
                    )
                    if cur.rowcount > 0:
                        response += "\n\n🏆 *НОВОЕ ДОСТИЖЕНИЕ!*\n🌟 Первые шаги в практике!"
            
            # 2. Достижение "100%" - за прохождение ВСЕХ уроков И всех вопросов
            # Проверяем прохождение всех уроков
            cur.execute(
                """SELECT COUNT(DISTINCT l.id) as total_lessons,
                COUNT(DISTINCT up.lesson_id) as completed_lessons
                FROM lessons l
                LEFT JOIN user_progress up ON l.id = up.lesson_id AND up.user_id = %s""",
                (cq.from_user.id,)
            )
            lessons_progress = cur.fetchone()
            
            # Проверяем правильные ответы на все вопросы
            cur.execute(
                """SELECT COUNT(DISTINCT q.id) AS total_questions,
                COUNT(DISTINCT ua.question_id) AS answered_correct
                FROM questions q
                LEFT JOIN user_answers ua 
                    ON q.id = ua.question_id 
                    AND ua.user_id = %s 
                    AND ua.is_correct = 1""",
                (cq.from_user.id,)
            )
            questions_progress = cur.fetchone()
            
            # Если все уроки пройдены И все вопросы правильно отвечены
            all_lessons_completed = (lessons_progress['completed_lessons'] >= lessons_progress['total_lessons'] 
                                     and lessons_progress['total_lessons'] > 0)
            all_questions_completed = (questions_progress['answered_correct'] >= questions_progress['total_questions'] 
                                       and questions_progress['total_questions'] > 0)
            
            if all_lessons_completed and all_questions_completed:
                achievement_name = "100%"
                cur.execute(
                    """INSERT IGNORE INTO achievements 
                    (user_id, name) VALUES (%s, %s)""",
                    (cq.from_user.id, achievement_name)
                )
                if cur.rowcount > 0:
                    response += "\n\n🏆 *НОВОЕ ДОСТИЖЕНИЕ!*\n💯 100% - Вы прошли все уроки и ответили на все вопросы!"

            conn.commit()

    except Exception as e:
        logger.error(f"Ошибка обработки ответа: {e}")
        response = "⚠️ Произошла ошибка при обработке ответа"
    finally:
        conn.close()

    # Создаем клавиатуру для продолжения
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="➡️ Следующий вопрос", callback_data="next_question")
    keyboard.button(text="🔚 Завершить викторину", callback_data="cancel_quiz")
    keyboard.button(text="🏠 Главное меню", callback_data="main_menu")
    keyboard.adjust(1)

    # Редактируем сообщение с результатом
    try:
        await cq.message.edit_text(
            f"{response}\n\n📝 Объяснение: {explanation}",
            reply_markup=keyboard.as_markup()
        )
        await cq.answer()
    except Exception as e:
        logger.error(f"Ошибка редактирования сообщения: {e}")
        await cq.message.answer(
            f"{response}\n\n📝 Объяснение: {explanation}",
            reply_markup=keyboard.as_markup()
        )
        await cq.answer()


# Переход к следующему вопросу в викторине
@dp.callback_query(F.data == "next_question")
async def next_question_handler(cq: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    category = data.get('category')
    
    if not category:
        await cq.answer("❌ Категория не выбрана", show_alert=True)
        await state.clear()
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Ищем неотвеченные вопросы в категории
            cur.execute(
                """SELECT q.* FROM questions q
                LEFT JOIN user_answers ua 
                    ON q.id = ua.question_id 
                    AND ua.user_id = %s 
                    AND ua.is_correct = 1
                WHERE q.category = %s AND ua.question_id IS NULL
                ORDER BY RAND() LIMIT 1""",
                (cq.from_user.id, category)
            )
            question = cur.fetchone()

            if not question:
                await cq.message.answer("🎉 Вы ответили на все вопросы в этой категории!")
                await state.clear()
                await cq.answer()
                # Предлагаем вернуться на главную
                builder = InlineKeyboardBuilder()
                builder.button(text="🏠 Главное меню", callback_data="main_menu")
                await cq.message.answer("Выберите действие:", reply_markup=builder.as_markup())
                return

            # Создаем варианты ответов
            builder = InlineKeyboardBuilder()
            options = json.loads(question['options'])
            # Используем индекс вместо полного текста, чтобы избежать превышения лимита callback_data (64 байта)
            for index, option in enumerate(options):
                builder.button(text=option, callback_data=f"answer:{index}")
            builder.button(text="🏠 Главное меню", callback_data="main_menu")
            builder.adjust(2)

            # Обновляем состояние
            await state.update_data(
                question_id=question['id'],
                correct_answer=question['answer'],
                options=options  # Сохраняем варианты ответов для получения по индексу
            )

            # Отправляем новый вопрос
            await cq.message.answer(
                f"❓ Вопрос: {question['question']}\n"
                f"Сложность: {question['difficulty'].upper()}",
                reply_markup=builder.as_markup()
            )
            await cq.answer()

    except Exception as e:
        logger.error(f"Ошибка получения следующего вопроса: {e}")
        await cq.answer("⚠️ Не удалось загрузить следующий вопрос")
    finally:
        conn.close()

# ================== АДМИН-ПАНЕЛЬ ================== #
# Построение клавиатуры админ-панели
def build_admin_keyboard() -> InlineKeyboardMarkup:
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="📝 Добавить урок",     callback_data="admin:add_lesson")
    keyboard.button(text="❓ Добавить вопрос",    callback_data="admin:add_question")
    keyboard.button(text="👑 Управление админами", callback_data="admin:manage_admins")
    keyboard.button(text="📢 Сделать рассылку",  callback_data="admin:broadcast")
    keyboard.button(text="📊 Статистика",        callback_data="admin:stats")
    keyboard.button(text="🏠 Главное меню", callback_data="main_menu")
    keyboard.adjust(1)
    return keyboard.as_markup()

# Обработка команды /admin (открытие админ-панели, проверка прав доступа)
@dp.message(Command('admin'))
async def admin_panel(msg: types.Message):
    """Открывает админ-панель."""
    if not await is_admin(msg.from_user.id):
        await msg.answer("⛔ Доступ запрещён!")
        return

    await msg.answer("👑 Админ-панель:", reply_markup=build_admin_keyboard())

# Обработка действий администратора из админ-панели
@dp.callback_query(F.data.startswith("admin:"))
async def handle_admin_actions(cq: types.CallbackQuery, state: FSMContext):
    """Обрабатывает действия администратора."""
    user_id = cq.from_user.id
    if not await is_admin(user_id):
        await cq.answer("⛔ Доступ запрещён!", show_alert=True)
        return

    try:
        action = cq.data.split(":", 1)[1]
    except IndexError:
        await cq.answer("❌ Ошибка: неверный формат данных", show_alert=True)
        return

    if action == "add_lesson":
        await cq.message.answer("Введите название урока:")
        await state.set_state(AdminStates.WaitingLessonTitle)

    elif action == "add_question":
        await cq.message.answer("Введите вопрос:")
        await state.set_state(AdminStates.WaitingQuestion)


    elif action == "manage_admins":
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="➕ Добавить администратора", callback_data="admin:add_admin")
        keyboard.button(text="➖ Удалить администратора", callback_data="admin:remove_admin")
        keyboard.button(text="🔙 Назад", callback_data="admin:back")
        keyboard.button(text="🏠 Главное меню", callback_data="main_menu")
        keyboard.adjust(2)
        await cq.message.edit_text("Выберите действие:", reply_markup=keyboard.as_markup())

    elif action == "broadcast":
        await cq.message.answer("Введите сообщение для рассылки:")
        await state.set_state(AdminStates.WaitingBroadcastMessage)

    elif action == "stats":
        await show_admin_stats(cq)

    elif action == "add_admin":
        await cq.message.answer("Введите ID нового администратора:")
        await state.set_state(AdminStates.WaitingAdminAdd)

    elif action == "remove_admin":
        await cq.message.answer("Введите ID администратора для удаления:")
        await state.set_state(AdminStates.WaitingAdminRemove)

    elif action == "back":
        await cq.message.edit_text("👑 Админ-панель:", reply_markup=build_admin_keyboard())

    await cq.answer()


# Обработка названия урока (первый шаг добавления урока)
@dp.message(AdminStates.WaitingLessonTitle)
async def process_lesson_title(msg: types.Message, state: FSMContext):
    await state.update_data(title=msg.text)
    await msg.answer("Введите содержание урока:")
    await state.set_state(AdminStates.WaitingLessonContent)


# Обработка содержания урока (второй шаг добавления урока)
@dp.message(AdminStates.WaitingLessonContent)
async def process_lesson_content(msg: types.Message, state: FSMContext):
    await state.update_data(content=msg.text)
    await msg.answer("Введите тему урока (например, cpp-basics):")
    await state.set_state(AdminStates.WaitingLessonTopic)


# Обработка темы урока (третий шаг добавления урока)
@dp.message(AdminStates.WaitingLessonTopic)
async def process_lesson_topic(msg: types.Message, state: FSMContext):
    await state.update_data(topic=msg.text)
    await msg.answer("Введите порядковый номер урока:")
    await state.set_state(AdminStates.WaitingLessonOrder)


# Обработка порядкового номера урока (четвертый шаг, сохранение урока в БД)
@dp.message(AdminStates.WaitingLessonOrder)
async def process_lesson_order(msg: types.Message, state: FSMContext):
    try:
        order_num = int(msg.text)
    except ValueError:
        await msg.answer("Пожалуйста, введите число:")
        return

    data = await state.get_data()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lessons (title, content, topic, order_num) "
                "VALUES (%s, %s, %s, %s)",
                (data['title'], data['content'], data['topic'], order_num)
            )
            conn.commit()
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="main_menu")
        await msg.answer("✅ Урок успешно добавлен!", reply_markup=builder.as_markup())
    except Exception as e:
        logger.error(f"Ошибка добавления урока: {e}")
        await msg.answer("❌ Ошибка при добавлении урока")
    finally:
        conn.close()
    await state.clear()


# Обработка текста вопроса (первый шаг добавления вопроса)
@dp.message(AdminStates.WaitingQuestion)
async def process_question(msg: types.Message, state: FSMContext):
    await state.update_data(question=msg.text)
    await msg.answer("Введите правильный ответ:")
    await state.set_state(AdminStates.WaitingAnswer)


# Обработка правильного ответа (второй шаг добавления вопроса)
@dp.message(AdminStates.WaitingAnswer)
async def process_answer(msg: types.Message, state: FSMContext):
    await state.update_data(answer=msg.text)
    await msg.answer("Введите варианты ответов через запятую:")
    await state.set_state(AdminStates.WaitingOptions)


# Обработка вариантов ответов (третий шаг добавления вопроса)
@dp.message(AdminStates.WaitingOptions)
async def process_options(msg: types.Message, state: FSMContext):
    options = [opt.strip() for opt in msg.text.split(',')]
    if len(options) < 2:
        await msg.answer("Нужно как минимум 2 варианта ответа. Попробуйте снова:")
        return

    await state.update_data(options=options)
    await msg.answer("Введите категорию вопроса (например, cpp-basics):")
    await state.set_state(AdminStates.WaitingCategory)


# Обработка категории вопроса (четвертый шаг добавления вопроса)
@dp.message(AdminStates.WaitingCategory)
async def process_category(msg: types.Message, state: FSMContext):
    await state.update_data(category=msg.text)
    await msg.answer("Введите объяснение ответа (необязательно):")
    await state.set_state(AdminStates.WaitingExplanation)


# Обработка команды /cancel (отмена текущей операции, очистка состояния FSM)
@dp.message(Command('cancel'))
async def cmd_cancel(msg: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        return

    await state.clear()
    await msg.answer("Действие отменено", reply_markup=types.ReplyKeyboardRemove())

# Обработка объяснения ответа (пятый шаг, сохранение вопроса в БД)
@dp.message(AdminStates.WaitingExplanation)
async def process_explanation(msg: types.Message, state: FSMContext):
    data = await state.get_data()
    explanation = msg.text if msg.text else "Объяснение отсутствует."

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO questions (question, answer, options, category, explanation) "
                "VALUES (%s, %s, %s, %s, %s)",
                (data['question'], data['answer'], json.dumps(data['options']), data['category'], explanation)
            )
            conn.commit()
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="main_menu")
        await msg.answer("✅ Вопрос успешно добавлен!", reply_markup=builder.as_markup())
    except Exception as e:
        logger.error(f"Ошибка добавления вопроса: {e}")
        await msg.answer("❌ Ошибка при добавлении вопроса")
    finally:
        conn.close()
    await state.clear()


# Добавление нового администратора (установка флага is_admin для пользователя)
@dp.message(AdminStates.WaitingAdminAdd)
async def process_add_admin(msg: types.Message, state: FSMContext):
    try:
        new_admin_id = int(msg.text)
    except ValueError:
        await msg.answer("Некорректный ID. Введите числовой ID:")
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_admin = TRUE WHERE id = %s",
                (new_admin_id,)
            )
            if cur.rowcount == 0:
                await msg.answer("Пользователь не найден. Сначала он должен запустить бота.")
            else:
                conn.commit()
                builder = InlineKeyboardBuilder()
                builder.button(text="🏠 Главное меню", callback_data="main_menu")
                await msg.answer(f"✅ Пользователь {new_admin_id} назначен администратором!", reply_markup=builder.as_markup())
    except Exception as e:
        logger.error(f"Ошибка назначения администратора: {e}")
        await msg.answer("❌ Ошибка при назначении администратора")
    finally:
        conn.close()
    await state.clear()


# Удаление администратора (сброс флага is_admin для пользователя)
@dp.message(AdminStates.WaitingAdminRemove)
async def process_remove_admin(msg: types.Message, state: FSMContext):
    try:
        admin_id = int(msg.text)
    except ValueError:
        await msg.answer("Некорректный ID. Введите числовой ID:")
        return

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET is_admin = FALSE WHERE id = %s",
                (admin_id,)
            )
            conn.commit()
            builder = InlineKeyboardBuilder()
            builder.button(text="🏠 Главное меню", callback_data="main_menu")
            await msg.answer(f"✅ Пользователь {admin_id} удалён из администраторов!", reply_markup=builder.as_markup())
    except Exception as e:
        logger.error(f"Ошибка удаления администратора: {e}")
        await msg.answer("❌ Ошибка при удалении администратора")
    finally:
        conn.close()
    await state.clear()


# Рассылка сообщения всем пользователям бота
@dp.message(AdminStates.WaitingBroadcastMessage)
async def process_broadcast_message(msg: types.Message, state: FSMContext):
    users = []
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM users")
            users = [row['id'] for row in cur.fetchall()]
    finally:
        conn.close()

    success = 0
    for user_id in users:
        try:
            await bot.send_message(user_id, msg.text)
            success += 1
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения {user_id}: {e}")

    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    await msg.answer(f"✉️ Рассылка завершена. Успешно отправлено: {success}/{len(users)}", reply_markup=builder.as_markup())
    await state.clear()

# Обработка команды /progress (показ прогресса пользователя: уроки, баллы, достижения, статистика по категориям)
@dp.message(Command('progress'))
async def cmd_progress(msg: types.Message):
    """Показывает прогресс пользователя."""
    user_id = msg.from_user.id
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Общая статистика
            cur.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM user_progress WHERE user_id = %s) lessons_completed, "
                "(SELECT COALESCE(score, 0) FROM users WHERE id = %s) score, "
                "(SELECT COUNT(*) FROM achievements WHERE user_id = %s) achievements_unlocked",
                (user_id, user_id, user_id)
            )
            stats = cur.fetchone()

            if not stats:
                builder = InlineKeyboardBuilder()
                builder.button(text="🏠 Главное меню", callback_data="main_menu")
                await msg.answer("❌ Не удалось загрузить статистику", reply_markup=builder.as_markup())
                return

            # Прогресс по категориям
            cur.execute(
                "SELECT q.category, "
                "COUNT(DISTINCT q.id) total_questions, "
                "COUNT(DISTINCT ua.question_id) answered, "
                "COALESCE(SUM(ua.is_correct), 0) correct_answers "
                "FROM questions q "
                "LEFT JOIN user_answers ua ON q.id = ua.question_id AND ua.user_id = %s "
                "GROUP BY q.category",
                (user_id,)
            )
            categories = cur.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения прогресса: {e}")
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="main_menu")
        await msg.answer("❌ Произошла ошибка при загрузке прогресса", reply_markup=builder.as_markup())
        return
    finally:
        conn.close()

    text = (
        f"📊 *Ваш прогресс*\n\n"
        f"🎓 Пройдено уроков: {stats.get('lessons_completed', 0)}\n"
        f"🏆 Баллов: {stats.get('score', 0)}\n"
        f"🎖 Достижений: {stats.get('achievements_unlocked', 0)}\n\n"
    )
    
    if categories:
        text += "📈 Статистика по категориям:\n"
        for cat in categories:
            total = cat.get('total_questions', 0)
            answered = cat.get('answered', 0)
            correct = cat.get('correct_answers', 0)
            progress = f"{answered}/{total}"
            accuracy = (f"({(correct / answered * 100):.1f}%)" if answered > 0 else "(0%)")
            text += (
                f"\n🔹 *{cat['category'].replace('-', ' ').title()}*\n"
                f"Вопросы: {progress} {accuracy}\n"
            )

    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    await msg.answer(text, reply_markup=builder.as_markup())

# Обработка команды /achievements (показ всех достижений пользователя)
@dp.message(Command('achievements'))
async def cmd_achievements(msg: types.Message):
    """Показывает достижения пользователя."""
    user_id = msg.from_user.id
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, unlocked_at FROM achievements "
                "WHERE user_id = %s ORDER BY unlocked_at DESC",
                (user_id,)
            )
            achievements = cur.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения достижений: {e}")
        await msg.answer("❌ Произошла ошибка при загрузке достижений")
        return
    finally:
        conn.close()

    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    
    if not achievements:
        await msg.answer("🎖 У вас пока нет достижений. Продолжайте учиться!", reply_markup=builder.as_markup())
        return

    text = "🏆 *Ваши достижения:*\n\n"
    for ach in achievements:
        date = ach['unlocked_at'].strftime("%d.%m.%Y") if ach.get('unlocked_at') else "Неизвестно"
        name = ach.get('name', 'Неизвестное достижение')
        # Улучшаем отображение названий достижений
        if name == "Первые шаги в теории":
            emoji = "📖"
        elif name == "Первые шаги в практике":
            emoji = "✏️"
        elif name == "100%":
            emoji = "💯"
        else:
            emoji = "🏅"
        
        text += (
            f"{emoji} *{name}*\n"
            f"_Получено:_ {date}\n\n"
        )

    await msg.answer(text, reply_markup=builder.as_markup())

# Показ статистики бота администратору (количество пользователей, уроков, вопросов, прогресс)
async def show_admin_stats(cq: types.CallbackQuery):
    """Показывает статистику для администратора."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # Общая статистика
            cur.execute("SELECT COUNT(*) as total_users FROM users")
            total_users = cur.fetchone().get('total_users', 0)
            
            cur.execute("SELECT COUNT(*) as total_lessons FROM lessons")
            total_lessons = cur.fetchone().get('total_lessons', 0)
            
            cur.execute("SELECT COUNT(*) as total_questions FROM questions")
            total_questions = cur.fetchone().get('total_questions', 0)
            
            cur.execute("SELECT COUNT(*) as total_progress FROM user_progress")
            total_progress = cur.fetchone().get('total_progress', 0)
            
            cur.execute("SELECT SUM(score) as total_score FROM users")
            total_score = cur.fetchone().get('total_score', 0) or 0
            
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        await cq.answer("❌ Произошла ошибка при загрузке статистики", show_alert=True)
        return
    finally:
        conn.close()

    text = (
        "📊 *Статистика бота:*\n\n"
        f"👥 Всего пользователей: {total_users}\n"
        f"📚 Всего уроков: {total_lessons}\n"
        f"❓ Всего вопросов: {total_questions}\n"
        f"✅ Всего пройдено уроков: {total_progress}\n"
        f"🏆 Всего набрано баллов: {total_score}\n"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    
    try:
        await cq.message.answer(text, reply_markup=builder.as_markup())
        await cq.answer()
    except Exception as e:
        logger.error(f"Ошибка отправки статистики: {e}")
        await cq.answer("❌ Не удалось отправить статистику", show_alert=True)

# Завершение викторины (очистка состояния, возврат на главную)
@dp.callback_query(F.data == "cancel_quiz")
async def cancel_quiz_handler(cq: types.CallbackQuery, state: FSMContext):
    """Завершает викторину."""
    await state.clear()
    await cq.message.answer("✅ Викторина завершена")
    await cq.answer()
    
    # Предлагаем вернуться на главную
    builder = InlineKeyboardBuilder()
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    await cq.message.answer("Выберите действие:", reply_markup=builder.as_markup())

# ================== ЗАПУСК БОТА ================== #
# Основная функция запуска бота (запуск polling)
async def main():
    """Основная функция запуска бота."""
    try:
        logger.info("🚀 Бот запущен")
        await dp.start_polling(bot, drop_pending_updates=True)
    except KeyboardInterrupt:
        logger.info("Остановка бота по запросу пользователя")
    except Exception as e:
        logger.error(f"❌ Ошибка в основном цикле: {e}", exc_info=True)
    finally:
        logger.info("Бот остановлен")

if __name__ == '__main__':
    try:
        init_db()
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске: {e}", exc_info=True)
        raise