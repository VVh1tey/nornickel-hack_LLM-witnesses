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


        if 'goal' not in self.state:
            self.state.goal = ''
        try:
            with open('../configs/api.json', 'r') as f:
                data = json.load(f)
                self.port = data['port']
                self.host = data['host']
                self.addr = f"http://{self.host}:{self.port}"
        except Exception as e:
            raise Exception(f'Problem with api config[[\n{e}\n]]]')

        if 'messages' not in self.state:
            self.state.messages = []


    def readme(self):
        ...

    def get_responses(self):
        ...

    def send_data(self):

        if st.button('Send data', type='primary', width='stretch'):

            try:
                if self.state.files and self.state.goal:
                    goal = {
                        'goal':self.state.goal,   
                    }
                    files = {filename: BytesIO(file.getvalue()) for filename, file in self.state.files.items()}
                    status = requests.post(self.addr + HANDLE_POST, files=files, json=goal)
                    if status.status_code == 200:
                        st.success('Successfull sended')

                else:
                    st.error('Please upload files and goal')
            except Exception as e:
                st.error(f"Error: {e}")

    def write_goal(self):
        if self.state.goal:
            columns = st.columns([16,1])
            with columns[0]:
                    st.write(self.state.goal)
            with columns[1]:
                if st.button('', icon='🗑', width='content', type='secondary'):
                    self.state.goal = ''
                    st.rerun()

    def show_files(self):
        if self.state.files:
            columns = st.columns(len(self.state.files))
            for i, f in enumerate(list(self.state.files)):
                with columns[i]:
                    st.code(f, width='content')

    def input_goal(self):
        goal = st.text_area('Placeholder for your genius idea:').strip()
        create_button = st.button('Create goal', use_container_width=True,)
        if create_button and goal:
            self.state.goal = goal
            st.rerun()
        else:
            if not goal:
                st.info('send me not None pls')
        if self.state.goal:
                self.write_goal()
        
        
    def load_file(self):
        file = st.file_uploader('Upload files', FILE_TYPES)
        if file: 
            self.state.files[file.name] = file
            st.success(f'{file.name} {file.size} uploaded')
            st.rerun()

    def loop(self):
        tabs = st.tabs(['Goal studio', 'Agent responses'])
        with tabs[0]:
            st.header('Hello llmwui!')
            self.load_file()
            self.show_files()
            self.input_goal()    
            if self.state.goal:
                self.send_data()
        
        with tabs[1]:
            st.table()
            

if __name__ == "__main__":
    app = LLMsWitnessUi()   
    app.loop()
