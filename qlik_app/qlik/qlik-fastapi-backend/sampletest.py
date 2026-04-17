"""

Python Example:
Use ONLY Access Token + Refresh Token
(No Client ID / Client Secret)
 
This script:
1. Calls Alteryx API using access token
2. Detects expiry / 401 Unauthorized
3. Uses refresh token to generate new access token
4. Retries API automatically
5. Stores latest tokens in local file
"""
 
import requests
import time
import json
import os
 
# =====================================================
# CONFIGURATION
# =====================================================
 
BASE_URL = "https://us1.alteryxcloud.com"
TOKEN_URL = "https://us1.alteryxcloud.com/oauth2/token"
 
ACCESS_TOKEN = eyJraWQiOiI4ZTZmMzkxMC1mMzM3LTExZjAtYjczNi1mMzM4YTFhMzg5M2IiLCJhbGciOiJSUzI1NiJ9.eyJjbGllbnRfaWQiOiJhZjFiNTMyMS1hZmUwLTQ4YzItOTY2YS1jNzdkNzRlOTgwODUiLCJpc3MiOiJodHRwczovL3BpbmdhdXRoLmFsdGVyeXhjbG91ZC5jb20vYXMiLCJqdGkiOiIwYmJhOWI1OS0wNjYwLTQwNzMtYWZkNS1mNzM4ZTY5YjViZjAiLCJpYXQiOjE3NzY0MTExNzcsImV4cCI6MTc3NjQxMTQ3NywiYXVkIjpbInNvcmltLWFsdGVyeXgtdHJpYWwtMmhjZyJdLCJzY29wZSI6Inc6MDFLTlM3UlZRUzE0Wk0yMk1ZNTFFWlJHS0oiLCJzdWIiOiI4ZGNlYjZhNC02NTM1LTRlZjktOTRiZC04OWE5ZjJkYzU5NWQiLCJzaWQiOiJiODkwMDYxZS0zNDE1LTQxMjMtODhlZS1lZjk2ODZmNzdjZjgiLCJhdXRoX3RpbWUiOjE3NzY0MTExNzcsImFjciI6IkFsdGVyeXhBdXRoSURQUG9saWN5IiwiZW1haWwiOiJhY2NlbGVyYXRvcnNAc29yaW0uYWkiLCJlbnYiOiJjOTE4MmQzYy0zMWZjLTRmOTYtOThkOC1lMmVkY2VkZjQxMjIiLCJvcmciOiI0YWJjNGY2MC1hMDRmLTRhYmQtOGIyNC1lY2M4NzQxNTMxMmYifQ.yKkZN55NsDkJUUd5BxqTjvBTYvqB-DF3ltXXj5UP6Nu2pxNTOh0ndUtOaRkn4UQnqRJ5RpE5H8nMOno_4YBz-W5LSMLfl_cK0h2mjVovs67HMJw0o8fDyeeGg4503RYIh0J7czXr7PJTgdAXUe83xre9geed4MHwuAKmIk-SVimAfxF9GUM7aiM22eT-Ec7FmRwQXPtyzkFpd5LUkDR86-EJ9G6XI97iUoXK9w2dYRFSWo0qmw6Jfj8clAWbtC-BkTC0Fwqf9WFbHaJp0DTZt8QjtkUFlRGOzut6X0ZjDV56auPihAlr7w2KfZyn7-wyXabaZWvTweWMHG_KlJoIhw
REFRESH_TOKEN = eyJhbGciOiJSUzI1NiIsImtpZCI6ImRlZmF1bHQifQ.eyJzdWIiOiI4ZGNlYjZhNC02NTM1LTRlZjktOTRiZC04OWE5ZjJkYzU5NWQiLCJqdGkiOiI5ZjdhNDdmYi04YWI5LTRkZGItYmUyYS04YWEzMTI4ZTRhMDUiLCJleHAiOjE4MDc5NDcxNzcsInNpZCI6ImI4OTAwNjFlLTM0MTUtNDEyMy04OGVlLWVmOTY4NmY3N2NmOCIsInNjb3BlIjoidzowMUtOUzdSVlFTMTRaTTIyTVk1MUVaUkdLSiIsImF1dGhfdGltZSI6MTc3NjQxMTE3NywiYWNyIjoiQWx0ZXJ5eEF1dGhJRFBQb2xpY3kiLCJhbXIiOlsicHdkIl0sImlzcyI6Imh0dHBzOi8vcGluZ2F1dGguYWx0ZXJ5eGNsb3VkLmNvbS9hcyJ9.FxmMxaZomr27wnVmcgKAKb6QKrGH5asZnVugk3-q6xyA_ERMal9LQkje_DtBnJ00W7mv9-iKa49TnqUWfe8PFS07cOeXeYkXiKfmquyy8WCImPu22Ml2B0aAxXGrthsiCU0bYiDOGCMPNPCtHlBtTi99p_f-L_qevwOqLzci68n7h3M5AFVOP1WAxrXadXn965N7GgnNA7zwrLDGyxvZ8UFUhhyjm7m1u9Q0L5Y7LQ006NoaSDXt5LQUA7kJZYfGbfNGIqS6hlgYRTvmqHSQlPayJBXfuMiNYfmv5yltPUct5Edw7I2nf9Np120BDYyMewou4SODva_4veSBYYI7NA
 
TOKEN_FILE = "tokens.json"
 
 
# =====================================================
# TOKEN CLIENT
# =====================================================
 
class AlteryxTokenClient:
 
    def __init__(self, access_token, refresh_token):
        self.access_token = access_token
        self.refresh_token = refresh_token
 
    # -------------------------------------------------
    # Save Tokens
    # -------------------------------------------------
    def save_tokens(self):
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token
        }
 
        with open(TOKEN_FILE, "w") as f:
            json.dump(data, f, indent=4)
 
    # -------------------------------------------------
    # Load Tokens
    # -------------------------------------------------
    def load_tokens(self):
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, "r") as f:
                data = json.load(f)
                self.access_token = data["access_token"]
                self.refresh_token = data["refresh_token"]
 
    # -------------------------------------------------
    # Refresh Token
    # -------------------------------------------------
    def refresh_access_token(self):
        print("Refreshing token...")
 
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token
        }
 
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
 
        response = requests.post(TOKEN_URL, data=payload, headers=headers)
 
        if response.status_code == 200:
            token_data = response.json()
 
            self.access_token = token_data["access_token"]
 
            if "refresh_token" in token_data:
                self.refresh_token = token_data["refresh_token"]
 
            self.save_tokens()
 
            print("New token generated successfully.")
 
        else:
            raise Exception(
                f"Token refresh failed: {response.status_code} {response.text}"
            )
 
    # -------------------------------------------------
    # Generic GET API Call
    # -------------------------------------------------
    def get(self, endpoint):
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }
 
        url = BASE_URL + endpoint
 
        response = requests.get(url, headers=headers)
 
        # If token expired
        if response.status_code == 401:
            print("Access token expired.")
 
            self.refresh_access_token()
 
            headers["Authorization"] = f"Bearer {self.access_token}"
            response = requests.get(url, headers=headers)
 
        response.raise_for_status()
        return response.json()
 
 
# =====================================================
# MAIN
# =====================================================
 
client = AlteryxTokenClient(ACCESS_TOKEN, REFRESH_TOKEN)
 
# Load saved tokens if exists
client.load_tokens()
 
try:
    # Example API Call
    result = client.get("/v3/workspaces")
 
    print(json.dumps(result, indent=4))
 
except Exception as e:
    print("Error:", e)