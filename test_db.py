import os
import httpx
from dotenv import load_dotenv

load_dotenv()

headers = {
    "apikey": os.environ["SUPABASE_SERVICE_KEY"],
    "Authorization": f"Bearer {os.environ['SUPABASE_SERVICE_KEY']}",
    "Content-Type": "application/json"
}

url = os.environ["SUPABASE_URL"] + "/rest/v1/rpc/exec_sql"
data = {"query": "ALTER TABLE chat_history ADD COLUMN integration_source TEXT DEFAULT 'web';"}

r = httpx.post(url, headers=headers, json=data)
print(r.status_code, r.text)
