"""TODO (P4, U1): Streamlit-каркас — загрузка Excel, цель/ограничения, запуск на моках."""
import requests
import json
import streamlit as st
from io import BytesIO
import time
import uuid

# Обновленные эндпоинты под новое API
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
            st.warning(f'⚠️ Using default API config: {self.addr}')

    def export(self, format: str = "csv", unique_id: str = None):
        """Экспорт сессии в указанном формате"""
        if not self.state.session_id:
            st.warning("No active session to export")
            return
        
        # Генерируем уникальный ключ
        if unique_id is None:
            unique_id = str(uuid.uuid4())[:8]
        
        button_key = f"export_{format}_{unique_id}"
        
        if st.button(f'📤 Export as {format.upper()}', type='secondary', key=button_key, width=256):
            with st.spinner(f'Exporting as {format.upper()}...'):
                try:
                    response = requests.get(
                        self.addr + HANDLE_EXPORT.replace('{session_id}', self.state.session_id),
                        params={'format': format}
                    )
                    if response.status_code == 200:
                        st.success(f'✅ Export to {format.upper()} completed')
                        # Показываем результат
                        if format == "json":
                            st.json(response.json())
                        else:
                            st.code(response.text[:500] + "..." if len(response.text) > 500 else response.text)
                    else:
                        st.error(f'❌ Export failed: {response.status_code}')
                        st.code(response.text)
                except Exception as e:
                    st.error(f'❌ Error: {e}')

    def readme(self):
        """Отображает информацию о приложении"""
        with st.expander("ℹ️ About this app"):
            st.markdown("""
            ### 🧠 LLM Witness UI
            
            Загрузите Excel-файл с данными, задайте цель и ограничения,
            затем отправьте на обработку. Система сгенерирует гипотезы
            на основе загруженных данных.
            
            **Возможности:**
            - 📁 Загрузка Excel файлов (.xls, .xlsx)
            - 🎯 Настройка цели и ограничений
            - ⚖️ Весовые коэффициенты для ранжирования
            - 💡 Получение и просмотр сгенерированных гипотез
            - 📤 Экспорт результатов (CSV, JSON, DOCX)
            - 🔄 Переранжирование гипотез
            - 📊 Обратная связь по гипотезам
            """)

    def get_responses(self):
        """Получение ответов от сервера"""
        col1, col2 = st.columns([1, 1])
        
        with col1:
            if st.button('🔄 Fetch all sessions', type='primary', use_container_width=True):
                with st.spinner('Fetching sessions...'):
                    try:
                        response = requests.get(self.addr + HANDLE_GET_ALL)
                        if response.status_code == 200:
                            sessions = response.json()
                            self.state.responses = sessions
                            st.success(f'✅ Fetched {len(sessions)} sessions')
                            st.rerun()
                        else:
                            st.error(f'❌ Error: {response.status_code}')
                    except Exception as e:
                        st.error(f'❌ Error: {e}')
        
        with col2:
            if self.state.session_id:
                if st.button('📋 Fetch current session', type='secondary', use_container_width=True):
                    with st.spinner('Fetching session data...'):
                        try:
                            response = requests.get(
                                self.addr + HANDLE_GET.replace('{session_id}', self.state.session_id)
                            )
                            if response.status_code == 200:
                                self.state.session_data = response.json()
                                st.success('✅ Session data updated')
                                st.rerun()
                            else:
                                st.error(f'❌ Error: {response.status_code}')
                        except Exception as e:
                            st.error(f'❌ Error: {e}')

    def send_data(self):
        """Отправка данных на сервер"""
        if st.button('🚀 Send data', type='primary', use_container_width=True):
            if not self.state.files:
                st.error('❌ Please upload at least one file')
                return
            if not self.state.goal:
                st.error('❌ Please set a goal')
                return
            
            with st.spinner('Sending data to server...'):
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
                        st.success(f'✅ Successfully sent! Session ID: `{self.state.session_id[:8]}...`')
                        # Получаем данные сессии сразу
                        self.state.session_data = result
                        st.balloons()
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f'❌ Error: {response.status_code}')
                        st.code(response.text)
                        
                except Exception as e:
                    st.error(f"❌ Error: {e}")

    def send_feedback(self, session_id: str, hypothesis_id: str, action: str, unique_id: str):
        """Отправка обратной связи по гипотезе"""
        try:
            response = requests.post(
                self.addr + HANDLE_FEEDBACK.replace('{session_id}', session_id).replace('{hypothesis_id}', hypothesis_id),
                json={'action': action}
            )
            if response.status_code == 200:
                st.success(f'✅ Feedback sent: {action}')
                # Обновляем данные сессии
                self.get_session_data(session_id)
                time.sleep(0.5)
                st.rerun()
            else:
                st.error(f'❌ Feedback failed: {response.status_code}')
        except Exception as e:
            st.error(f'❌ Error: {e}')

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
            st.error(f'❌ Error fetching session: {e}')
        return None

    def rerank_session(self):
        """Переранжирование сессии"""
        if not self.state.session_id:
            return
            
        if st.button('🔄 Rerank session', type='secondary', use_container_width=True):
            with st.spinner('Reranking...'):
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
                        st.success('✅ Session re-ranked successfully')
                        self.state.session_data = response.json()
                        st.rerun()
                    else:
                        st.error(f'❌ Error: {response.status_code}')
                except Exception as e:
                    st.error(f'❌ Error: {e}')

    def draw_responses(self):
        """Отображение ответов от сервера"""
        if not self.state.responses:
            st.info("🐭 No sessions yet. Use 'Fetch all sessions' to load data.")
            return
        
        # Отображаем список сессий
        st.subheader(f"📚 Sessions ({len(self.state.responses)})")
        
        # Фильтр по статусу
        status_filter = st.selectbox(
            "Filter by status",
            ["All", "running", "done", "failed"],
            index=0,
            key="status_filter"
        )
        
        filtered_sessions = self.state.responses
        if status_filter != "All":
            filtered_sessions = [s for s in self.state.responses if s.get('status') == status_filter]
        
        for session in filtered_sessions:
            session_id = session.get('session_id', 'N/A')
            status = session.get('status', 'unknown')
            status_emoji = "🟢" if status == "done" else "🟡" if status == "running" else "🔴"
            
            with st.expander(f"{status_emoji} Session: {session_id[:8]}... (Status: {status})"):
                col1, col2 = st.columns(2)
                
                with col1:
                    st.metric("📌 Session ID", session_id[:12])
                    st.metric("📊 Status", status)
                    st.metric("🎯 Goal", session.get('goal', 'N/A')[:50] + "..." if len(session.get('goal', '')) > 50 else session.get('goal', 'N/A'))
                
                with col2:
                    st.metric("🔒 Constraints", session.get('constraints', 'None') or 'None')
                    if 'weights' in session:
                        st.json(session.get('weights', {}))
                    if 'created_at' in session:
                        st.metric("📅 Created", session.get('created_at')[:16] if session.get('created_at') else 'N/A')
                
                # Отображаем гипотезы если есть
                hypotheses = session.get('hypotheses', [])
                if hypotheses:
                    st.subheader(f"💡 Hypotheses ({len(hypotheses)})")
                    
                    for i, hyp in enumerate(hypotheses, 1):
                        with st.container():
                            col1, col2, col3 = st.columns([5, 1, 1])
                            hyp_id = hyp.get('id', f"hyp_{i}")
                            unique_key = f"{session_id}_{hyp_id}_{i}"
                            
                            with col1:
                                st.markdown(f"**{i}.** {hyp.get('text', 'No text')}")
                                if hyp.get('score'):
                                    st.caption(f"Score: {hyp.get('score'):.2f}")
                                if hyp.get('status'):
                                    status_color = "green" if hyp.get('status') == "approved" else "red" if hyp.get('status') == "rejected" else "orange"
                                    st.markdown(f"Status: **<span style='color:{status_color}'>{hyp.get('status')}</span>**", unsafe_allow_html=True)
                            
                            with col2:
                                if hyp.get('status') not in ['approved', 'rejected']:
                                    if st.button("👍 Approve", key=f"approve_{unique_key}"):
                                        self.send_feedback(session_id, hyp_id, "approve", unique_key)
                            
                            with col3:
                                if hyp.get('status') not in ['approved', 'rejected']:
                                    if st.button("👎 Reject", key=f"reject_{unique_key}"):
                                        self.send_feedback(session_id, hyp_id, "reject", unique_key)
                            
                            st.divider()
                else:
                    st.info("💭 No hypotheses yet")
                
                # Кнопка для загрузки этой сессии
                col1, col2 = st.columns(2)
                with col1:
                    if st.button(f"📥 Load session", key=f"load_{session_id}"):
                        self.state.session_id = session_id
                        self.state.session_data = session
                        st.success(f"✅ Loaded session {session_id[:8]}...")
                        st.rerun()
                
                with col2:
                    # Кнопка удаления сессии (только если есть)
                    if st.button(f"🗑️ Delete", key=f"delete_{session_id}"):
                        st.warning("Delete functionality not implemented in API")
        
        # Отображение текущей сессии
        if self.state.session_data:
            st.divider()
            st.subheader("📌 Current Session")
            
            data = self.state.session_data
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.metric("Session ID", data.get('session_id', 'N/A')[:8] + "...")
            with col2:
                st.metric("Status", data.get('status', 'unknown'))
            with col3:
                st.metric("Goal", data.get('goal', 'N/A')[:30] + "..." if len(data.get('goal', '')) > 30 else data.get('goal', 'N/A'))
            
            # Экспорт с уникальными ключами
            st.subheader("📤 Export")
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
            st.info(f"🎯 **Current goal:** {self.state.goal}")
            if self.state.constraints:
                st.info(f"🔒 **Constraints:** {self.state.constraints}")
            if self.state.weights:
                st.info(f"⚖️ **Weights:** {self.state.weights}")

    def show_files(self):
        """Отображение загруженных файлов"""
        if self.state.files:
            st.subheader("📎 Uploaded files")
            columns = st.columns(min(len(self.state.files), 4))
            for i, (filename, file) in enumerate(list(self.state.files.items())):
                with columns[i % len(columns)]:
                    st.code(filename, language='text')
                    size_kb = len(file.getvalue()) / 1024
                    st.caption(f"{size_kb:.1f} KB")
                    
                    # Кнопка удаления файла
                    if st.button(f"❌ Remove", key=f"remove_{filename}_{i}"):
                        del self.state.files[filename]
                        st.rerun()

    def input_goal(self):
        """Ввод цели"""
        with st.container():
            st.subheader("🎯 Goal Studio")
            
            # Поле для ввода цели
            current_goal = self.state.goal if self.state.goal else ""
            goal = st.text_area(
                'Enter your goal:',
                value=current_goal,
                placeholder="Example: Analyze customer feedback to identify improvement areas...",
                height=100
            )
            
            col1, col2 = st.columns(2)
            with col1:
                if st.button('✅ Set goal', use_container_width=True):
                    if goal.strip():
                        self.state.goal = goal.strip()
                        st.success('✅ Goal updated!')
                        st.rerun()
                    else:
                        st.warning('⚠️ Please enter a goal')
            
            with col2:
                if st.button('🗑️ Clear goal', use_container_width=True):
                    self.state.goal = ''
                    st.rerun()
            
            if self.state.goal:
                self.write_goal()

    def input_weights(self):
        """Ввод весов"""
        with st.container():
            st.subheader("⚖️ Weights Configuration")
            
            st.caption("Configure weights for different criteria (0-10)")
            
            col1, col2 = st.columns(2)
            with col1:
                relevance = st.slider('Relevance', 0, 10, 
                                     value=int(self.state.weights.get('relevance', 5)),
                                     key="slider_relevance")
                novelty = st.slider('Novelty', 0, 10,
                                   value=int(self.state.weights.get('novelty', 5)),
                                   key="slider_novelty")
            
            with col2:
                feasibility = st.slider('Feasibility', 0, 10,
                                       value=int(self.state.weights.get('feasibility', 5)),
                                       key="slider_feasibility")
                impact = st.slider('Impact (optional)', 0, 10,
                                  value=int(self.state.weights.get('impact', 5)),
                                  key="slider_impact")
            
            if st.button('💾 Save weights', use_container_width=True):
                self.state.weights = {
                    'relevance': float(relevance),
                    'novelty': float(novelty),
                    'feasibility': float(feasibility),
                    'impact': float(impact)
                }
                st.success('✅ Weights saved!')
                st.rerun()
            
            # Отображаем текущие веса
            if self.state.weights:
                with st.expander("📊 Current weights"):
                    st.json(self.state.weights)

    def load_file(self):
        """Загрузка файла"""
        with st.container():
            st.subheader("📁 File Upload")
            
            file = st.file_uploader(
                'Upload Excel file',
                type=FILE_TYPES,
                help="Supported formats: .xls, .xlsx"
            )
            
            if file:
                # Проверяем, не загружен ли уже этот файл
                if file.name not in self.state.files:
                    self.state.files[file.name] = file
                    st.success(f'✅ {file.name} uploaded successfully!')
                    st.rerun()
                else:
                    st.info(f'ℹ️ {file.name} already uploaded')

    def input_constraints(self):
        """Ввод ограничений"""
        if self.state.goal:
            with st.container():
                st.subheader("🔒 Constraints")
                
                constraints = st.text_area(
                    'Input constraints (optional)',
                    value=self.state.constraints,
                    placeholder="Example: Budget: $10000, Timeline: 3 months, Must include: ...",
                    height=80
                )
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button('💾 Save constraints', use_container_width=True):
                        self.state.constraints = constraints.strip() if constraints.strip() else ""
                        st.success('✅ Constraints updated!')
                        st.rerun()
                
                with col2:
                    if st.button('🗑️ Clear constraints', use_container_width=True):
                        self.state.constraints = ""
                        st.rerun()

    def loop(self):
        """Основной цикл приложения"""
        st.set_page_config(
            page_title="LLM Witness UI",
            page_icon="🧠",
            layout="wide",
            initial_sidebar_state="expanded"
        )
        
        # Sidebar
        with st.sidebar:
            st.title("🧠 LLM Witness")
            st.caption("Generate hypotheses from data")
            
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
                st.caption(f"📌 Active session: `{self.state.session_id[:8]}...`")
                if self.state.session_data:
                    status = self.state.session_data.get('status', 'unknown')
                    st.caption(f"📊 Status: {status}")
        
        # Основные вкладки
        tabs = st.tabs(['🎯 Goal Studio', '💡 Hypotheses', '📊 Analytics'])
        
        with tabs[0]:
            st.header("🎯 Goal Studio")
            
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
            st.header("💡 Hypotheses")
            
            # Получение ответов
            self.get_responses()
            
            # Отображение ответов
            self.draw_responses()
        
        with tabs[2]:
            st.header("📊 Analytics")
            
            if self.state.session_data:
                st.subheader("📋 Session Details")
                
                data = self.state.session_data
                col1, col2 = st.columns(2)
                
                with col1:
                    st.metric("🆔 Session ID", data.get('session_id', 'N/A')[:12])
                    st.metric("📊 Status", data.get('status', 'unknown'))
                    st.metric("📅 Created", data.get('created_at', 'N/A')[:16] if data.get('created_at') else 'N/A')
                
                with col2:
                    st.metric("🎯 Goal", data.get('goal', 'N/A')[:50] + "..." if len(data.get('goal', '')) > 50 else data.get('goal', 'N/A'))
                    st.metric("🔒 Constraints", data.get('constraints', 'None') or 'None')
                    if 'weights' in data:
                        st.metric("⚖️ Weights", "Configured")
                
                # Показываем прогресс если есть
                progress = data.get('progress', [])
                if progress:
                    st.subheader("📈 Progress")
                    for item in progress:
                        st.info(item)
                
                # Количество гипотез
                hypotheses = data.get('hypotheses', [])
                if hypotheses:
                    st.subheader(f"💡 Hypotheses ({len(hypotheses)})")
                    for i, hyp in enumerate(hypotheses, 1):
                        st.markdown(f"**{i}.** {hyp.get('text', 'No text')}")
                        if hyp.get('score'):
                            st.caption(f"Score: {hyp.get('score'):.2f}")
                
                # Экспорт с уникальными ключами
                st.subheader("📤 Export")
                col1, col2, col3 = st.columns(3)
                with col1:
                    self.export("csv", "analytics_csv")
                with col2:
                    self.export("json", "analytics_json")
                with col3:
                    self.export("docx", "analytics_docx")
            else:
                st.info("💡 No session data loaded. Send data or fetch a session first.")


if __name__ == "__main__":
    app = LLMsWitnessUi()
    app.loop()