from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException
import os
import re
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def clean_phone_number(phone):
    """Remove all non-digit characters except leading plus"""
    if not isinstance(phone, str):
        return "" # Handle non-string input
        
    # Remove all non-digit and non-plus characters
    cleaned = re.sub(r'[^0-9+]', '', phone)
    
    # If starts with plus, preserve it and remove any other pluses after the first
    if cleaned.startswith('+'):
        # Allow one leading plus, remove subsequent non-digits and pluses
        return '+' + re.sub(r'\D', '', cleaned[1:])
    return re.sub(r'\D', '', cleaned) # If no leading plus, just return digits

def send_sms(to_number, message):
    try:
        # Clean and validate TO number
        to_number = clean_phone_number(to_number)
        if not re.match(r"^\+\d{8,15}$", to_number):
            print(f"SMS Failed: Invalid TO number: {to_number}")
            return False
            
        # Validate message content
        if not message:
            print("SMS Failed: Message content is empty.")
            return False
            
        # Get credentials and clean FROM number
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        raw_from_number = os.getenv("TWILIO_PHONE_NUMBER", "")
        from_number = clean_phone_number(raw_from_number)
        
        # Validate FROM number format
        if not re.match(r"^\+\d{8,15}$", from_number):
            print(f"SMS Failed: Invalid FROM number: {raw_from_number} -> {from_number}. Must be E.164 format.")
            return False

        if not all([account_sid, auth_token]):
            print("SMS Failed: Missing Twilio credentials. Please check your .env file (TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN).")
            return False

        client = Client(account_sid, auth_token)
        message_body = message[:1600] 
        
        try:
            twilio_message = client.messages.create(
                body=message_body,
                from_=from_number,
                to=to_number
            )
            
            if twilio_message.sid:
                print(f"SMS Success: Message queued for {to_number} (SID: {twilio_message.sid})")
                return True
            else:
                print(f"SMS Failed: Twilio did not return SID. Status: {twilio_message.status}")
                return False
                
        except TwilioRestException as e:
            print(f"SMS Failed (Twilio API Error): {e}")
            return False
            
    except Exception as e:
        print(f"SMS Failed (General Error): {str(e)}")
        return False