# yoppychat-nextjs razorpay setup/utils/razorpay_client.py

import os
import razorpay
from dotenv import load_dotenv

load_dotenv()

def get_razorpay_client():
    """
    Initializes and returns the Razorpay client.
    """
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")

    if not key_id or not key_secret:
        print("Razorpay API keys not found in environment variables.")
        return None

    return razorpay.Client(auth=(key_id, key_secret))