"""Streamlit-интерфейс «Фабрики гипотез»: загрузка Excel, цель/ограничения, запуск пайплайна, фидбэк, экспорт."""
import requests
import json
import streamlit as st
from io import BytesIO
import time
import uuid

# Эндпоинты backend API
HANDLE_POST = '/api/sessions'
HANDLE_GET = '/api/sessions/{session_id}'
HANDLE_GET_ALL = '/api/sessions'
HANDLE_RERANK = '/api/sessions/{session_id}/rerank'
HANDLE_EXPORT = '/api/sessions/{session_id}/export'
HANDLE_FEEDBACK = '/api/sessions/{session_id}/hypotheses/{hypothesis_id}/feedback'

FILE_TYPES = ['xls', 'xlsx']

class LLMsWitnessUi:
    def __init__(self):
        self.state = st.session_state

        # Инициализация состояния
        if 'files' not in self.state:
            self.state.files = dict()
        if 'responses' not in self.state:
            self.state.responses = []
        if 'constraints' not in self.state:
            self.state.constraints = str()
        if 'weights' not in self.state:
            self.state.weights = dict()
        if 'goal' not in self.state:
            self.state.goal = str()
        if 'session_id' not in self.state:
            self.state.session_id = None
        if 'session_data' not in self.state:
            self.state.session_data = None
        if 'is_loading' not in self.state:
            self.state.is_loading = False
        if 'export_counter' not in self.state:
            self.state.export_counter = 0

        try:
            with open('../configs/api.json', 'r') as f:
                data = json.load(f)
                self.port = data['port']
                self.host = data['host']
                self.addr = f"http://{self.host}:{self.port}"
        except Exception as e:
            # Дефолтные значения для разработки
            self.port = 8000
            self.host = 'localhost'
            self.addr = f"http://{self.host}:{self.port}"
            st.warning(f'⚠️ Не найден configs/api.json, использую адрес по умолчанию: {self.addr}')

    def export(self, format: str = "csv", unique_id: str = None):
        """Экспорт сессии в указанном формате"""
        if not self.state.session_id:
            st.warning("Нет активной сессии для экспорта")
            return

        # Генерируем уникальный ключ
        if unique_id is None:
            unique_id = str(uuid.uuid4())[:8]

        button_key = f"export_{format}_{unique_id}"
        data_key = f"export_data_{button_key}"

        if st.button(
            f'📤 Экспорт в {format.upper()}',
            type='secondary',
            key=button_key,
            width=256,
            help=f"Запросить у сервера файл со всеми гипотезами сессии в формате {format.upper()}",
        ):
            with st.spinner(f'Формируем {format.upper()}...'):
                try:
                    response = requests.get(
                        self.addr + HANDLE_EXPORT.replace('{session_id}', self.state.session_id),
                        params={'format': format}
                    )
                    if response.status_code == 200:
                        # response.content - сырые байты: для docx (бинарный zip)
                        # response.text пытается декодировать их как текст и
                        # выдаёт нечитаемую кашу вместо настоящего файла.
                        st.session_state[data_key] = response.content
                    else:
                        st.error(f'❌ Не удалось экспортировать: {response.status_code}')
                        st.code(response.text)
                except Exception as e:
                    st.error(f'❌ Ошибка: {e}')

        if data_key in st.session_state:
            mime_map = {
                "csv": "text/csv",
                "json": "application/json",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
            st.download_button(
                f'💾 Скачать {format.upper()}',
                data=st.session_state[data_key],
                file_name=f"{self.state.session_id}.{format}",
                mime=mime_map.get(format, "application/octet-stream"),
                key=f"dl_{button_key}",
                help="Файл уже получен с сервера — сохранить его на диск",
            )

    def readme(self):
        """Отображает информацию о приложении"""
        with st.expander("ℹ️ О приложении"):
            st.markdown("""
            ### 🧠 Фабрика гипотез

            Загрузите Excel-файл с данными по хвостам, задайте цель и
            ограничения, затем отправьте на обработку. Система сгенерирует
            и проранжирует исследовательские гипотезы на основе загруженных
            данных и базы знаний.

            **Возможности:**
            - 📁 Загрузка Excel-файлов (.xls, .xlsx)
            - 🎯 Настройка цели и ограничений
            - ⚖️ Весовые коэффициенты для ранжирования гипотез
            - 💡 Просмотр сгенерированных гипотез
            - 📤 Экспорт результатов (CSV, JSON, DOCX)
            - 🔄 Переранжирование по новым весам без повторного вызова LLM
            - 👍👎 Обратная связь эксперта по каждой гипотезе
            """)

    def get_responses(self):
        """Получение ответов от сервера"""
        col1, col2 = st.columns([1, 1])

        with col1:
            if st.button(
                '🔄 Загрузить все сессии',
                type='primary',
                use_container_width=True,
                help="Запросить у сервера список всех созданных сессий",
            ):
                with st.spinner('Загружаем список сессий...'):
                    try:
                        response = requests.get(self.addr + HANDLE_GET_ALL)
                        if response.status_code == 200:
                            sessions = response.json()
                            self.state.responses = sessions
                            st.success(f'✅ Загружено сессий: {len(sessions)}')
                            st.rerun()
                        else:
                            st.error(f'❌ Ошибка: {response.status_code}')
                    except Exception as e:
                        st.error(f'❌ Ошибка: {e}')

        with col2:
            if self.state.session_id:
                if st.button(
                    '📋 Обновить текущую сессию',
                    type='secondary',
                    use_container_width=True,
                    help="Запросить у сервера свежий статус и результаты активной сессии",
                ):
                    with st.spinner('Обновляем данные сессии...'):
                        try:
                            response = requests.get(
                                self.addr + HANDLE_GET.replace('{session_id}', self.state.session_id)
                            )
                            if response.status_code == 200:
                                self.state.session_data = response.json()
                                st.success('✅ Данные сессии обновлены')
                                st.rerun()
                            else:
                                st.error(f'❌ Ошибка: {response.status_code}')
                        except Exception as e:
                            st.error(f'❌ Ошибка: {e}')

    def send_data(self):
        """Отправка данных на сервер"""
        if st.button(
            '🚀 Запустить генерацию',
            type='primary',
            use_container_width=True,
            help="Отправить Excel, цель и ограничения на сервер — запустится полный пайплайн генерации гипотез в фоне",
        ):
            if not self.state.files:
                st.error('❌ Загрузите хотя бы один файл')
                return
            if not self.state.goal:
                st.error('❌ Укажите цель')
                return

            with st.spinner('Отправляем данные на сервер...'):
                try:
                    # Берем первый файл из загруженных
                    first_filename = list(self.state.files.keys())[0]
                    first_file = self.state.files[first_filename]

                    # Подготовка файла для отправки
                    files = {
                        'excel_file': (
                            first_filename,
                            BytesIO(first_file.getvalue()),
                            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                        )
                    }

                    # Формируем данные формы
                    data = {
                        'goal': self.state.goal,
                        'constraints': self.state.constraints if self.state.constraints else ""
                    }

                    # Добавляем веса как JSON строку
                    if self.state.weights:
                        data['weights'] = json.dumps(self.state.weights)

                    # Отправляем запрос
                    response = requests.post(
                        self.addr + HANDLE_POST,
                        files=files,
                        data=data
                    )

                    if response.status_code == 200:
                        result = response.json()
                        self.state.session_id = result.get('session_id')
                        st.success(f'✅ Отправлено! ID сессии: `{self.state.session_id[:8]}...`')
                        # Получаем данные сессии сразу
                        self.state.session_data = result
                        st.balloons()
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f'❌ Ошибка: {response.status_code}')
                        st.code(response.text)

                except Exception as e:
                    st.error(f"❌ Ошибка: {e}")

    def send_feedback(self, session_id: str, hypothesis_id: str, action: str, unique_id: str):
        """Отправка обратной связи по гипотезе"""
        try:
            response = requests.post(
                self.addr + HANDLE_FEEDBACK.replace('{session_id}', session_id).replace('{hypothesis_id}', hypothesis_id),
                json={'action': action}
            )
            if response.status_code == 200:
                st.success(f'✅ Отправлен фидбэк: {action}')
                # Обновляем данные сессии
                self.get_session_data(session_id)
                time.sleep(0.5)
                st.rerun()
            else:
                st.error(f'❌ Не удалось отправить фидбэк: {response.status_code}')
        except Exception as e:
            st.error(f'❌ Ошибка: {e}')

    def get_session_data(self, session_id: str):
        """Получение данных конкретной сессии"""
        try:
            response = requests.get(
                self.addr + HANDLE_GET.replace('{session_id}', session_id)
            )
            if response.status_code == 200:
                self.state.session_data = response.json()
                return self.state.session_data
        except Exception as e:
            st.error(f'❌ Ошибка получения сессии: {e}')
        return None

    def rerank_session(self):
        """Переранжирование сессии"""
        if not self.state.session_id:
            return

        if st.button(
            '🔄 Пересчитать рейтинг (Rerank)',
            type='secondary',
            use_container_width=True,
            help=(
                "Пересчитать итоговый score гипотез по текущим весам критериев "
                "(новизна/реализуемость/эффект) — БЕЗ повторного обращения к LLM. "
                "Оценки по каждому критерию уже посчитаны один раз при генерации, "
                "это просто пересортировка. Можно жать сколько угодно раз."
            ),
        ):
            with st.spinner('Пересчитываем рейтинг...'):
                try:
                    weights_data = {
                        'relevance': float(self.state.weights.get('relevance', 1.0)),
                        'novelty': float(self.state.weights.get('novelty', 1.0)),
                        'feasibility': float(self.state.weights.get('feasibility', 1.0))
                    }

                    response = requests.post(
                        self.addr + HANDLE_RERANK.replace('{session_id}', self.state.session_id),
                        json=weights_data
                    )

                    if response.status_code == 200:
                        st.success('✅ Рейтинг пересчитан')
                        self.state.session_data = response.json()
                        st.rerun()
                    else:
                        st.error(f'❌ Ошибка: {response.status_code}')
                except Exception as e:
                    st.error(f'❌ Ошибка: {e}')

    def draw_responses(self):
        """Отображение ответов от сервера"""
        if not self.state.responses:
            st.info("🐭 Пока нет сессий. Нажмите «Загрузить все сессии», чтобы получить данные.")
            return

        # Отображаем список сессий
        st.subheader(f"📚 Сессии ({len(self.state.responses)})")

        # Фильтр по статусу
        status_filter = st.selectbox(
            "Фильтр по статусу",
            ["Все", "running", "done", "failed"],
            index=0,
            key="status_filter",
            help="Показать только сессии с выбранным статусом",
        )

        filtered_sessions = self.state.responses
        if status_filter != "Все":
            filtered_sessions = [s for s in self.state.responses if s.get('status') == status_filter]

        for session in filtered_sessions:
            session_id = session.get('session_id', 'N/A')
            status = session.get('status', 'unknown')
            status_emoji = "🟢" if status == "done" else "🟡" if status == "running" else "🔴"

            with st.expander(f"{status_emoji} Сессия: {session_id[:8]}... (статус: {status})"):
                col1, col2 = st.columns(2)

                with col1:
                    st.metric("📌 ID сессии", session_id[:12])
                    st.metric("📊 Статус", status)
                    st.metric("🎯 Цель", session.get('goal', 'N/A')[:50] + "..." if len(session.get('goal', '')) > 50 else session.get('goal', 'N/A'))

                with col2:
                    st.metric("🔒 Ограничения", session.get('constraints', 'Нет') or 'Нет')
                    if 'weights' in session:
                        st.json(session.get('weights', {}))
                    if 'created_at' in session:
                        st.metric("📅 Создана", session.get('created_at')[:16] if session.get('created_at') else 'N/A')

                # Отображаем гипотезы если есть
                hypotheses = session.get('hypotheses', [])
                if hypotheses:
                    st.subheader(f"💡 Гипотезы ({len(hypotheses)})")

                    for i, hyp in enumerate(hypotheses, 1):
                        with st.container():
                            col1, col2, col3 = st.columns([5, 1, 1])
                            hyp_id = hyp.get('id', f"hyp_{i}")
                            unique_key = f"{session_id}_{hyp_id}_{i}"

                            with col1:
                                st.markdown(f"**{i}.** {hyp.get('statement', 'Нет текста')}")
                                if hyp.get('score'):
                                    st.caption(f"Оценка: {hyp.get('score'):.2f}")
                                if hyp.get('status'):
                                    status_color = "green" if hyp.get('status') == "approved" else "red" if hyp.get('status') == "rejected" else "orange"
                                    st.markdown(f"Статус: **<span style='color:{status_color}'>{hyp.get('status')}</span>**", unsafe_allow_html=True)

                            with col2:
                                if hyp.get('status') not in ['approved', 'rejected']:
                                    if st.button(
                                        "👍 Принять",
                                        key=f"approve_{unique_key}",
                                        help="Отметить гипотезу как одобренную экспертом",
                                    ):
                                        self.send_feedback(session_id, hyp_id, "approve", unique_key)

                            with col3:
                                if hyp.get('status') not in ['approved', 'rejected']:
                                    if st.button(
                                        "👎 Отклонить",
                                        key=f"reject_{unique_key}",
                                        help="Отметить гипотезу как отклонённую экспертом",
                                    ):
                                        self.send_feedback(session_id, hyp_id, "reject", unique_key)

                            st.divider()
                else:
                    st.info("💭 Гипотез пока нет")

                # Кнопка для загрузки этой сессии
                col1, col2 = st.columns(2)
                with col1:
                    if st.button(
                        "📥 Сделать активной",
                        key=f"load_{session_id}",
                        help="Сделать эту сессию текущей — она появится во вкладках «Гипотезы» и «Аналитика»",
                    ):
                        self.state.session_id = session_id
                        self.state.session_data = session
                        st.success(f"✅ Сессия {session_id[:8]}... сделана активной")
                        st.rerun()

                with col2:
                    # Кнопка удаления сессии (только если есть)
                    if st.button(
                        "🗑️ Удалить",
                        key=f"delete_{session_id}",
                        help="Удаление сессий пока не реализовано на бэкенде",
                    ):
                        st.warning("Удаление сессий не реализовано в API")

        # Отображение текущей сессии
        if self.state.session_data:
            st.divider()
            st.subheader("📌 Текущая сессия")

            data = self.state.session_data
            col1, col2, col3 = st.columns(3)

            with col1:
                st.metric("ID сессии", data.get('session_id', 'N/A')[:8] + "...")
            with col2:
                st.metric("Статус", data.get('status', 'unknown'))
            with col3:
                st.metric("Цель", data.get('goal', 'N/A')[:30] + "..." if len(data.get('goal', '')) > 30 else data.get('goal', 'N/A'))

            # Экспорт с уникальными ключами
            st.subheader("📤 Экспорт")
            col1, col2, col3 = st.columns(3)
            with col1:
                self.export("csv", "current_csv")
            with col2:
                self.export("json", "current_json")
            with col3:
                self.export("docx", "current_docx")

    def write_goal(self):
        """Отображение текущей цели"""
        if self.state.goal:
            st.info(f"🎯 **Текущая цель:** {self.state.goal}")
            if self.state.constraints:
                st.info(f"🔒 **Ограничения:** {self.state.constraints}")
            if self.state.weights:
                st.info(f"⚖️ **Веса:** {self.state.weights}")

    def show_files(self):
        """Отображение загруженных файлов"""
        if self.state.files:
            st.subheader("📎 Загруженные файлы")
            columns = st.columns(min(len(self.state.files), 4))
            for i, (filename, file) in enumerate(list(self.state.files.items())):
                with columns[i % len(columns)]:
                    st.code(filename, language='text')
                    size_kb = len(file.getvalue()) / 1024
                    st.caption(f"{size_kb:.1f} КБ")

                    # Кнопка удаления файла
                    if st.button(
                        "❌ Убрать",
                        key=f"remove_{filename}_{i}",
                        help="Убрать файл из списка загруженных (на сервер ещё не отправлялся)",
                    ):
                        del self.state.files[filename]
                        st.rerun()

    def input_goal(self):
        """Ввод цели"""
        with st.container():
            st.subheader("🎯 Цель")

            # Поле для ввода цели
            current_goal = self.state.goal if self.state.goal else ""
            goal = st.text_area(
                'Сформулируйте цель:',
                value=current_goal,
                placeholder="Например: Снизить потери элементов 28 и 29 с хвостами",
                height=100,
                help="Свободный текст — что нужно улучшить/снизить/повысить. Именно это уйдёт в LLM для разбора цели и KPI.",
            )

            col1, col2 = st.columns(2)
            with col1:
                if st.button(
                    '✅ Сохранить цель',
                    use_container_width=True,
                    help="Зафиксировать цель для этой сессии",
                ):
                    if goal.strip():
                        self.state.goal = goal.strip()
                        st.success('✅ Цель сохранена!')
                        st.rerun()
                    else:
                        st.warning('⚠️ Введите цель')

            with col2:
                if st.button(
                    '🗑️ Очистить цель',
                    use_container_width=True,
                    help="Стереть введённую цель",
                ):
                    self.state.goal = ''
                    st.rerun()

            if self.state.goal:
                self.write_goal()

    def input_weights(self):
        """Ввод весов"""
        with st.container():
            st.subheader("⚖️ Веса критериев ранжирования")

            st.caption("Настройте вес каждого критерия (0-10) — влияет на итоговый score и на кнопку Rerank")

            col1, col2 = st.columns(2)
            with col1:
                relevance = st.slider(
                    'Релевантность', 0, 10,
                    value=int(self.state.weights.get('relevance', 5)),
                    key="slider_relevance",
                    help="Насколько гипотеза должна быть привязана к заявленной цели",
                )
                novelty = st.slider(
                    'Новизна', 0, 10,
                    value=int(self.state.weights.get('novelty', 5)),
                    key="slider_novelty",
                    help="Насколько гипотеза должна быть непохожа на уже известные/опробованные решения",
                )

            with col2:
                feasibility = st.slider(
                    'Реализуемость', 0, 10,
                    value=int(self.state.weights.get('feasibility', 5)),
                    key="slider_feasibility",
                    help="Насколько легко гипотезу внедрить с текущим оборудованием",
                )
                impact = st.slider(
                    'Эффект (опционально)', 0, 10,
                    value=int(self.state.weights.get('impact', 5)),
                    key="slider_impact",
                    help="Насколько велик ожидаемый эффект на целевой KPI",
                )

            if st.button(
                '💾 Сохранить веса',
                use_container_width=True,
                help="Сохранить веса — они применятся при следующей генерации и при нажатии Rerank",
            ):
                self.state.weights = {
                    'relevance': float(relevance),
                    'novelty': float(novelty),
                    'feasibility': float(feasibility),
                    'impact': float(impact)
                }
                st.success('✅ Веса сохранены!')
                st.rerun()

            # Отображаем текущие веса
            if self.state.weights:
                with st.expander("📊 Текущие веса"):
                    st.json(self.state.weights)

    def load_file(self):
        """Загрузка файла"""
        with st.container():
            st.subheader("📁 Загрузка файла")

            file = st.file_uploader(
                'Загрузите Excel-файл с отчётом по хвостам',
                type=FILE_TYPES,
                help="Поддерживаемые форматы: .xls, .xlsx"
            )

            if file:
                # Проверяем, не загружен ли уже этот файл
                if file.name not in self.state.files:
                    self.state.files[file.name] = file
                    st.success(f'✅ {file.name} успешно загружен!')
                    st.rerun()
                else:
                    st.info(f'ℹ️ {file.name} уже загружен')

    def input_constraints(self):
        """Ввод ограничений"""
        if self.state.goal:
            with st.container():
                st.subheader("🔒 Ограничения")

                constraints = st.text_area(
                    'Ограничения (необязательно)',
                    value=self.state.constraints,
                    placeholder="Например: Без остановки текущей технологической схемы",
                    height=80,
                    help="Технические/организационные ограничения — учитываются при проверке гипотез на реализуемость",
                )

                col1, col2 = st.columns(2)
                with col1:
                    if st.button(
                        '💾 Сохранить ограничения',
                        use_container_width=True,
                        help="Зафиксировать ограничения для этой сессии",
                    ):
                        self.state.constraints = constraints.strip() if constraints.strip() else ""
                        st.success('✅ Ограничения сохранены!')
                        st.rerun()

                with col2:
                    if st.button(
                        '🗑️ Очистить ограничения',
                        use_container_width=True,
                        help="Стереть введённые ограничения",
                    ):
                        self.state.constraints = ""
                        st.rerun()

    def loop(self):
        """Основной цикл приложения"""
        st.set_page_config(
            page_title="Фабрика гипотез",
            page_icon="🧠",
            layout="wide",
            initial_sidebar_state="expanded"
        )

        # Sidebar
        with st.sidebar:
            st.title("🧠 Фабрика гипотез")
            st.caption("Генерация гипотез по данным обогатительной фабрики")

            # Информация о подключении
            st.divider()
            col1, col2 = st.columns(2)
            with col1:
                st.caption("🔗 API:")
            with col2:
                st.code(self.addr, language='text')

            # README
            self.readme()

            # Информация о сессии
            if self.state.session_id:
                st.divider()
                st.caption(f"📌 Активная сессия: `{self.state.session_id[:8]}...`")
                if self.state.session_data:
                    status = self.state.session_data.get('status', 'unknown')
                    st.caption(f"📊 Статус: {status}")

        # Основные вкладки
        tabs = st.tabs(['🎯 Цель и параметры', '💡 Гипотезы', '📊 Аналитика'])

        with tabs[0]:
            st.header("🎯 Цель и параметры")

            # Загрузка файла
            self.load_file()

            # Отображение загруженных файлов
            self.show_files()

            # Ввод цели
            self.input_goal()

            # Ввод ограничений
            if self.state.goal:
                self.input_constraints()
                self.input_weights()

            # Отправка данных
            if self.state.goal and self.state.files:
                st.divider()
                self.send_data()

                # Кнопка переранжирования
                if self.state.session_id:
                    st.divider()
                    self.rerank_session()

        with tabs[1]:
            st.header("💡 Гипотезы")

            # Получение ответов
            self.get_responses()

            # Отображение ответов
            self.draw_responses()

        with tabs[2]:
            st.header("📊 Аналитика")

            if self.state.session_data:
                st.subheader("📋 Детали сессии")

                data = self.state.session_data
                col1, col2 = st.columns(2)

                with col1:
                    st.metric("🆔 ID сессии", data.get('session_id', 'N/A')[:12])
                    st.metric("📊 Статус", data.get('status', 'unknown'))
                    st.metric("📅 Создана", data.get('created_at', 'N/A')[:16] if data.get('created_at') else 'N/A')

                with col2:
                    st.metric("🎯 Цель", data.get('goal', 'N/A')[:50] + "..." if len(data.get('goal', '')) > 50 else data.get('goal', 'N/A'))
                    st.metric("🔒 Ограничения", data.get('constraints', 'Нет') or 'Нет')
                    if 'weights' in data:
                        st.metric("⚖️ Веса", "Настроены")

                # Показываем прогресс если есть
                progress = data.get('progress', [])
                if progress:
                    st.subheader("📈 Прогресс по узлам пайплайна")
                    for item in progress:
                        st.info(item)

                # Количество гипотез
                hypotheses = data.get('hypotheses', [])
                if hypotheses:
                    st.subheader(f"💡 Гипотезы ({len(hypotheses)})")
                    for i, hyp in enumerate(hypotheses, 1):
                        st.markdown(f"**{i}.** {hyp.get('statement', 'Нет текста')}")
                        if hyp.get('score'):
                            st.caption(f"Оценка: {hyp.get('score'):.2f}")

                # Экспорт с уникальными ключами
                st.subheader("📤 Экспорт")
                col1, col2, col3 = st.columns(3)
                with col1:
                    self.export("csv", "analytics_csv")
                with col2:
                    self.export("json", "analytics_json")
                with col3:
                    self.export("docx", "analytics_docx")
            else:
                st.info("💡 Данные сессии не загружены. Сначала запустите генерацию или загрузите сессию во вкладке «Гипотезы».")


if __name__ == "__main__":
    app = LLMsWitnessUi()
    app.loop()
