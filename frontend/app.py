"""TODO (P4, U1): Streamlit-каркас — загрузка Excel, цель/ограничения, запуск на моках."""
import requests
import json
import streamlit as st
from io import BytesIO

HANDLE_POST = '/api/sessions/multipart/zz'

HANDLE_GET = '/api/sessions/{session_id}'

FILE_TYPES = ['xls', 'xlsx']

class LLMsWitnessUi:

    def __init__(self):
        self.state = st.session_state
        if 'files' not in self.state:
            self.state.files = dict()

        if 'respones'  not in self.state:
            self.state.responses = []
        if 'constraints' not in self.state:
            self.state.constraints = str()

        if 'weights' not in self.state:
            self.state.weights = dict()

        if 'goal' not in self.state:
            self.state.goal = str()
        try:
            with open('../configs/api.json', 'r') as f:
                data = json.load(f)
                self.port = data['port']
                self.host = data['host']
                self.addr = f"http://{self.host}:{self.port}"
        except Exception as e:
            raise Exception(f'Problem with api config[[\n{e}\n]]]')

    def export(self):
        ...

    def readme(self):
        ...

    def get_responses(self, k: int):
        
        if st.button('fetch' if not self.state.responses else 'fetch next', type='primary'):
            try:
                response = requests.get(self.addr + HANDLE_GET.replace('{session_id}',f'{k}'))
                if response.status_code == 200:
                    data = response.json()
                    self.state.responses.append(data)
            except Exception as e:
                st.error(f'Error: {e}')

    def send_data(self):
        if st.button('Send data', type='primary', width='stretch'):
            try:
                if self.state.files and self.state.goal:
                    json = {'goal':self.state.goal}
                    if self.state.constrains:
                        json = json | self.state.constrains
                    if self.state.weights:
                        json = json | self.state.weights

                    files = {filename: BytesIO(file.getvalue()) for filename, file in self.state.files.items()}
                    status = requests.post(self.addr + HANDLE_POST, files=files, json=json)
                    if status.status_code == 200:
                        st.success('Successfull sended')

                else:
                    st.error('Please upload files and goal')
            except Exception as e:
                st.error(f"Error: {e}")

    def draw_responses(self):
        """

        {"goal": "Тестовая цель для Postgres", "error": null, 
        "status": "done", 
            "weights": {"risk": 1.0, "impact": 1.0, "novelty": 1.0, "feasibility": 1.0},
            "progress": [], 
            "created_at": "2026-07-04T13:28:08.693429+00:00", 
            "hypotheses": [], 
            "session_id": "test-session-1",
            "constraints": "нет ограничений"
        }

        """
            
        if self.state.responses:
            for r in self.state.responses:
                ...
        else:
            st.text("🐭 nothing, use fetch")

    def write_goal(self):
        if self.state.goal:
            st.write({'goal':self.state.goal, 'constraints': self.state.constraints, 'weights': self.state.weights})

    def show_files(self):
        if self.state.files:
            columns = st.columns(len(self.state.files))
            for i, f in enumerate(list(self.state.files)):
                with columns[i]:
                    st.code(f, width='content')

    def input_goal(self):
        goal = st.text_area('Placeholder for your genius idea:').strip()
        create_button = st.button('Create goal' if not self.state.goal else 'Update goal', use_container_width=True,)
        if create_button and goal:
            self.state.goal = goal
            st.rerun()
        if self.state.goal:
                self.write_goal()

    def input_weights(self):
        risk = st.slider('risk', 0, 10)
        impact = st.slider('impact', 0, 10)
        novelty = st.slider('novelty', 0, 10)
        feasibility = st.slider('feasibility', 0, 10)


        if st.button('Add weights', width='stretch'):
            self.state.weights['risk'] = int(risk)
            self.state.weights['impact'] = int(impact)
            self.state.weights['novelty'] = int(novelty)
            self.state.weights['feasibility'] = int(feasibility)
            st.rerun()
            
    def load_file(self):
        file = st.file_uploader('Upload files', FILE_TYPES)
        if file: 
            self.state.files[file.name] = file
            st.success(f'{file.name} {file.size} was uploaded')
            st.rerun()

    def loop(self):
        tabs = st.tabs(['Goal studio', 'Agent responses'])
        with tabs[0]:
            st.header('Hello llmwui!')
            self.load_file()
            self.show_files()
            self.input_goal()
            if self.state.goal:
                constraints = st.text_input('Input constrains', )    
                if st.button('Update constraints', width='stretch'):
                    self.state.constraints = constraints
                    st.rerun()
                self.input_weights()


            if self.state.goal:
                self.send_data()
        
        with tabs[1]:
            self.get_responses(len(self.state.responses))
            self.draw_responses()

            

if __name__ == "__main__":
    app = LLMsWitnessUi()   
    app.loop()
