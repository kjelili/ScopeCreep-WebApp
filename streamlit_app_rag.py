import streamlit as st
import pandas as pd
import os
import hashlib
import re
import time
from extract_text import extract_text_from_file
from scope_rag_checker import check_scope_creep_with_rag
from send_sms import send_sms, clean_phone_number

# Initialize session state
if 'analysis_done' not in st.session_state:
    st.session_state.analysis_done = False
if 'results_df' not in st.session_state:
    st.session_state.results_df = None

st.set_page_config(page_title="Scope Creep Detector", layout="wide")
st.title("📊 Scope Creep Detection with GPT + RAG + SMS Alerts")

api_key = st.text_input("🔑 Enter your OpenAI API Key", type="password")
stakeholder_phones = st.text_area("📲 Stakeholder Phone Numbers (comma-separated)", 
                                  placeholder="+1234567890, +0987654321")

# Alert threshold selector
ALERT_THRESHOLD_SELECTION = st.selectbox(
    "🔔 Alert Threshold", 
    ["High+Extreme", "Moderate+High+Extreme"],
    index=0,
    help="Select the risk level(s) for which SMS alerts should be sent."
)

scope_file = st.file_uploader("📄 Upload Scope Document", type=["pdf", "docx"])
uploaded_file = st.file_uploader("📨 Upload Email CSV", type=["csv"])

# Define alert thresholds
alert_thresholds_map = { 
    "High+Extreme": ["high", "extreme"],
    "Moderate+High+Extreme": ["moderate", "high", "extreme"]
}
current_threshold_list = [r.lower() for r in alert_thresholds_map[ALERT_THRESHOLD_SELECTION]]

def run_analysis():
    if not api_key:
        st.error("Please enter your OpenAI API Key.")
        return None
    if not scope_file:
        st.error("Please upload a Scope Document.")
        return None
    if not uploaded_file:
        st.error("Please upload an Email CSV.")
        return None
    
    scope_text = extract_text_from_file(scope_file)
    if not scope_text:
        st.error("Could not extract text from the scope document. Please ensure it's a valid PDF/DOCX.")
        return None

    try:
        df = pd.read_csv(uploaded_file, encoding='latin1')
    except Exception as e:
        st.error(f"Error reading CSV: {e}. Please ensure it's a valid CSV file.")
        return None

    if "email_body" not in df.columns:
        st.error("CSV must contain an 'email_body' column.")
        return None
    
    # Add email hash for unique identification
    df["_email_hash"] = df["email_body"].apply(
        lambda x: hashlib.md5(str(x).strip().encode()).hexdigest()
    )
    
    # Initialize new columns with default values
    df["scope_creep"] = "N/A"
    df["relevant_scope"] = "N/A"
    df["justification"] = "N/A"
    df["suggestion"] = "N/A"
    df["risk_level"] = "N/A"
    df["impact_analysis"] = "N/A"
    df["debug_traceback"] = ""

    progress_bar = st.progress(0)
    total_emails = len(df)
    
    # Check Twilio credentials early
    twilio_ready = True
    if not os.getenv("TWILIO_ACCOUNT_SID"):
        st.warning("⚠️ TWILIO_ACCOUNT_SID not set in environment variables")
        twilio_ready = False
    if not os.getenv("TWILIO_AUTH_TOKEN"):
        st.warning("⚠️ TWILIO_AUTH_TOKEN not set in environment variables")
        twilio_ready = False
    
    raw_from_number = os.getenv("TWILIO_PHONE_NUMBER", "")
    from_number = clean_phone_number(raw_from_number)
    if not re.match(r"^\+\d{8,15}$", from_number):
        st.warning(f"⚠️ Invalid TWILIO_PHONE_NUMBER: {raw_from_number} -> {from_number}. Must be E.164 format")
        twilio_ready = False
    elif not raw_from_number:
        st.warning("⚠️ TWILIO_PHONE_NUMBER not set in environment variables")
        twilio_ready = False
    
    # Preprocess and validate phone numbers
    valid_numbers = []
    if stakeholder_phones:
        raw_numbers = [num.strip() for num in re.split(r'[,;]', stakeholder_phones) if num.strip()]
        for number in raw_numbers:
            cleaned = clean_phone_number(number)
            if re.match(r"^\+\d{8,15}$", cleaned):
                valid_numbers.append(cleaned)
            else:
                st.warning(f"Invalid phone number format detected and skipped: {number}. Please use E.164 format (e.g., +447911123456).")

    if valid_numbers:
        if not twilio_ready:
            st.error("❌ Valid phone numbers found but Twilio credentials are invalid/missing. SMS alerts disabled.")
            valid_numbers = []  # Disable SMS sending
        else:
            st.info(f"📲 Valid phone numbers for alerts: {', '.join(valid_numbers)}")
    else:
        st.warning("⚠️ No valid phone numbers provided or all numbers were invalid. SMS alerts will not be sent.")

    for i, row in df.iterrows():
        # Ensure email_body is a string
        email_body_str = str(row["email_body"])
        
        result = check_scope_creep_with_rag(email_body_str, scope_text, api_key)
        
        # Populate DataFrame with results
        if not isinstance(result, dict):
            df.at[i, "scope_creep"] = "Error"
            df.at[i, "relevant_scope"] = "None"
            df.at[i, "justification"] = f"AI returned unexpected result type: {type(result).__name__}: {result!r}"
            df.at[i, "suggestion"] = "Check logs / debug traceback."
            df.at[i, "debug_traceback"] = ""
            df.at[i, "risk_level"] = "Unknown"
            df.at[i, "impact_analysis"] = "unknown"
            progress_bar.progress((i+1) / total_emails)
            continue

        df.at[i, "scope_creep"] = str(result.get("scope_creep", "Error")).capitalize()
        df.at[i, "relevant_scope"] = str(result.get("reference_scope_line", "None"))
        df.at[i, "justification"] = str(result.get("justification", "Error during analysis"))
        df.at[i, "suggestion"] = str(result.get("suggestion", "Check logs for AI error"))
        df.at[i, "debug_traceback"] = str(result.get("_traceback", ""))
        
        # Normalize and capitalize risk level for display
        risk_level_normalized = str(result.get("risk_level", "Unknown")).strip().lower()
        df.at[i, "risk_level"] = risk_level_normalized.capitalize()
        
        df.at[i, "impact_analysis"] = str(result.get("impact_analysis", "Unknown"))

        # SMS Alert Logic
        if str(result.get("scope_creep", "")).lower() == "yes" and risk_level_normalized in current_threshold_list:
            message = (
                f"SCOPE CREEP ALERT! Risk: {risk_level_normalized.upper()}\n"
                f"Email Snippet: {email_body_str[:150]}...\n"
                f"Suggested Action: {result.get('suggestion', 'Review immediately.')}\n"
                f"Justification: {result.get('justification', '')[:200]}..."
            )
            
            for number in valid_numbers:
                if send_sms(number, message): 
                    st.toast(f"📱 SMS alert successfully sent/queued for {number}")
                else:
                    st.error(f"❌ Failed to send SMS to {number} for email (hash: {row['_email_hash'][:6]}). Check terminal/console logs for details.")
        
        progress_bar.progress((i+1) / total_emails)
        
    # Remove internal columns before displaying/downloading
    return df.drop(columns=["_email_hash"])

if st.button("🚀 Run Analysis"):
    if not st.session_state.analysis_done:
        with st.spinner("Analyzing emails for scope creep... This may take a while for large files."):
            results_df = run_analysis()
            if results_df is not None:
                st.session_state.results_df = results_df
                st.session_state.analysis_done = True
                st.success("✅ Analysis Complete!")
            else:
                st.error("Analysis failed. Please check inputs and API key.")

if st.session_state.analysis_done:
    st.subheader("Analysis Results")
    st.dataframe(st.session_state.results_df)

    # Debug output for AI failures (helps pinpoint environment/dependency issues like proxies/proxy mismatches)
    error_rows = st.session_state.results_df[
        st.session_state.results_df["scope_creep"].astype(str).str.lower().eq("error")
    ]
    if len(error_rows) > 0:
        with st.expander("Debug: show AI error tracebacks", expanded=False):
            st.warning(
                "One or more rows failed during AI analysis. "
                "If you see '__init__() got an unexpected keyword argument \\'proxies\\'', "
                "this is usually due to OpenAI/HTTPX version mismatch in the Python environment running Streamlit."
            )
            # Show up to 5 tracebacks to avoid overwhelming the UI
            for idx, row in error_rows.head(5).iterrows():
                st.markdown(f"**Row {idx} traceback**")
                st.code(row.get("debug_traceback", "") or "(no traceback captured)")
    
    # Download button
    csv_data = st.session_state.results_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        "📥 Download Results CSV", 
        data=csv_data, 
        file_name="scope_creep_results.csv", 
        mime="text/csv"
    )
    
    # Clear button to reset analysis
    if st.button("🧹 Clear Results and Restart"):
        st.session_state.analysis_done = False
        st.session_state.results_df = None
        st.rerun()