import requests

url = "http://localhost:5000/contacts/sync-shopify"

try:
    response = requests.post(url, timeout=300)  # 5 min timeout for large syncs
    print(f"Status: {response.status_code}")
    print(f"Response: {response.text[:500]}")
except Exception as e:
    print(f"Error: {e}")
