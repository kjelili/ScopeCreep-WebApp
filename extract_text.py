import os
import tempfile
import fitz  # PyMuPDF
import docx

def extract_text_from_file(uploaded_file):
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploaded_file.name)[-1]) as tmp:
        tmp.write(uploaded_file.read())
        temp_path = tmp.name

    text = ""
    if uploaded_file.name.endswith(".pdf"):
        with fitz.open(temp_path) as doc:
            text = "\n".join(page.get_text() for page in doc)
    elif uploaded_file.name.endswith(".docx"):
        doc = docx.Document(temp_path)
        text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

    os.remove(temp_path)
    return text
