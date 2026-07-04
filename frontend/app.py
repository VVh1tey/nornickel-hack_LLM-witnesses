"""TODO (P4, U1): Streamlit-каркас — загрузка Excel, цель/ограничения, запуск на моках."""
import requests
import json
import streamlit as st
from streamlit.runtime.uploaded_file_manager import UploadedFile




def file_options_button(text: str, action: callable):
    columns = st.columns([1,8])
    with columns[0]:


        if st.button(text, width=96):
            with columns[1]:
                action()

def upload_file() -> UploadedFile:
    file = st.file_uploader('Load file')
    if file:
        st.success('File uploaded')
        return file

class LLMsWitnessUi:

    def __init__(self):
        self.state = st.session_state
        if 'file' not in self.state:
            self.state.file = None

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

    def send_hypotheses(self):
        if self.state.hypotheses:
            sending_button = st.button('send to ai', use_container_width= True, type='primary')
            if sending_button:
                for  hypo in enumerate(self.state.hypotheses):
                    try:
                        response = requests.post(self.addr + '/v1/send_hypo/"', json={
                            'text': hypo
                        })
                        if response.status_code == 200:
                            self.state.messages.append({'code': response.status_code, 'text': hypo})
                    
                    except requests.ConnectionError:
                        st.error(f'Connection faild, check your server or config: {self.addr}')
                        self.state.messages.append({'code': response, 'text': hypo})
                else:
                    self.state.hypotheses = []
                    st.rerun()
                
        if self.state.messages:
            for m in self.state.messages:
                if m['code'] != 200:
                    st.error(f"{m}")
                else:
                    st.success(f"{m}")
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
        file = upload_file()
        if file:
            self.state.file = file
        if self.state.file:
            file_options_button('size',lambda: st.markdown(f"{self.state.file.size}b"))
            file_options_button('name', lambda: st.markdown(f"{self.state.file.name}"))
            file_options_button('head', lambda: st.markdown(f"{self.state.file.read().decode('utf-8')[:256]}"))


    def loop(self):
        tabs = st.tabs(['Hypotheses studio', 'Agent responses', 'logs'])
        with tabs[0]:
            st.header('Hello llmwui!')
            self.load_file()
            self.input_hypo()                
            self.send_hypotheses()
        
        with tabs[1]:
            st.table()
            

if __name__ == "__main__":
    app = LLMsWitnessUi()   
    app.loop()
