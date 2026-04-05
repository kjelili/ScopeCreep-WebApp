# Scope Creep RAG Detector (SMS Only)

## Features
- Upload PDF/DOCX scope documents
- Upload project emails in CSV
- Detect scope creep using GPT-4 and RAG
- Notify stakeholders via SMS

## Setup
```bash
pip install -r requirements.txt
streamlit run streamlit_app_rag.py
```

## Env Variables Required
- TWILIO_ACCOUNT_SID
- TWILIO_AUTH_TOKEN
- TWILIO_PHONE_NUMBER
