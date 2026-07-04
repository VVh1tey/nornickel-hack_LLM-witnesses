"""TODO (P4, U1): Streamlit-каркас — загрузка Excel, цель/ограничения, запуск на моках."""
import requests
import json
import streamlit as st
from io import BytesIO

class LLMsWitnessUi:

    def __init__(self):
        self.state = st.session_state
        if 'files' not in self.state:
            self.state.files = dict()


        if 'hypotheses' not in self.state:
            self.state.hypotheses = []
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


    def send_package(self):
        if self.state.files and self.state.hypotheses:
            hypotheses = {
                'hypotheses':self.state.hypotheses,
            }

            files = {filename: BytesIO(file) for filename, file in self.state.files}

            #post request

        else:
            if not self.state.files and self.state.hyotheses:
                st.error('Any file wasnt upload')
            else:
                st.error('Any hypo wasnt upload')


        if self.state.messages:
            for m in self.state.messages:
                if m['code'] == 200:
                    st.success(f"{m}")
                    i = self.state.hypotheses.index(m['text'])
                    self.state.hypotheses.pop(i)
                else:
                    st.error(f"{m}")
            self.state.messages = []

    def write_hypotheses(self):
        if self.state.hypotheses:
            for i, h in enumerate(self.state.get('hypotheses')):
                columns = st.columns([8,1], vertical_alignment='center')
                with columns[0]:
                    st.text(h)

                with columns[1]:
                    drop_button = st.button('', icon='🗑',key=f'drop_hypo#{i}')
                    if drop_button:
                        self.state.hypotheses.pop(i)
                        st.rerun()
                st.divider()

    def show_files(self):
        if self.state.files:
            columns = st.columns(len(self.state.files))
            for i, f in enumerate(list(self.state.files)):
                with columns[i]:
                    st.text(f)

    def input_hypo(self):
        hypo = st.text_area('Placeholder for your genius idea:').strip()
        hypo_button = st.button('create hypo', use_container_width=True,)
        if hypo_button and hypo:
            if hypo not in self.state.hypotheses:
                self.state.hypotheses.append(hypo)
                st.rerun()
            else:
                st.warning("dont repeat your self")
        else:
            if not hypo:
                st.info('send me not None pls')
        if self.state.hypotheses:
            st.text('Created hypotheses:')
            with st.container(border=True, gap='xxsmall'):
                self.write_hypotheses()
        
        
    def load_file(self):
        file = st.file_uploader('Upload files')
        if file:
            self.state.files[file.name] = file
            st.success(f'{file.name} {file.size} uploaded')
            st.rerun()

    def loop(self):
        tabs = st.tabs(['Hypotheses studio', 'Agent responses'])
        with tabs[0]:
            st.header('Hello llmwui!')
            self.load_file()
            self.show_files()
            self.input_hypo()                
            self.send_hypotheses()
        
        with tabs[1]:
            st.table()
            

if __name__ == "__main__":
    app = LLMsWitnessUi()   
    app.loop()
