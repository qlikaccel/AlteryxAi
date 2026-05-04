import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()


class QlikClient:
    def __init__(self, api_key: Optional[str] = None, tenant_url: Optional[str] = None):
        # ONLY use API Key authentication - NO OAuth fallback
        self.api_key = (api_key or os.getenv("QLIK_API_KEY") or "").strip()
        self.tenant_url = self._resolve_tenant_url(tenant_url or os.getenv("QLIK_TENANT_URL") or "")
        if self.tenant_url:
            self.api_base_url = f"{self.tenant_url}/api/v1"
        else:
            self.api_base_url = (os.getenv("QLIK_API_BASE_URL") or "").strip().rstrip("/")

        if not self.api_base_url:
            raise ValueError("QLIK_API_BASE_URL or QLIK_TENANT_URL is not set in environment variables")
        if not self.api_key:
            raise ValueError("QLIK_API_KEY must be set in environment variables. Please generate a new API key from Qlik Cloud console.")

        self._last_auth_error: Optional[str] = None
        self.headers = self._build_headers(self.api_key)

    def _resolve_tenant_url(self, raw_url: str) -> str:
        """Resolve input tenant URL to an API-capable base origin."""
        value = (raw_url or "").strip().rstrip("/")
        if not value:
            return ""

        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"

        parsed = urlparse(value)
        host = (parsed.netloc or parsed.path or "").lower()
        scheme = parsed.scheme or "https"

        if host.startswith("www."):
            host = host[4:]

        # Alteryx cloud portal URLs are not direct Qlik API tenant hosts for current keys.
        # Map them to configured Qlik tenant so existing API key can authenticate.
        if host.endswith("alteryxcloud.com"):
            fallback = (os.getenv("QLIK_TENANT_URL") or "").strip().rstrip("/")
            if fallback:
                return fallback

        return f"{scheme}://{host}".rstrip("/")

    def _build_headers(self, bearer_token: str) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request_with_auth_fallback(self, method: str, url: str, timeout: int = 10) -> requests.Response:
        """Make HTTP request with no OAuth fallback - API key only"""
        response = requests.request(method, url, headers=self.headers, timeout=timeout)

        if response.status_code == 401:
            print(f"🔐 Authentication failed: 401 Unauthorized")
            print(f"✅ Ensure QLIK_API_KEY in .env is valid and not expired")
            print(f"✅ Generate a NEW API key from Qlik Cloud console:")
            print(f"   https://vtcej92i1jgxph5.in.qlikcloud.com/console")
            print(f"   → Admin → API Keys → Create New")
            print(f"   → Add scopes: apps:read, data:read, spaces:read")

        return response

    def test_connection(self) -> Dict[str, Any]:
        """Test the connection to Qlik Cloud."""
        url = f"{self.api_base_url}/users/me"
        try:
            response = self._request_with_auth_fallback("GET", url, timeout=10)
            print(f"Connection test - Status: {response.status_code}")
            print(f"Response: {response.text}")
            response.raise_for_status()
            return {"status": "success", "data": response.json()}
        except requests.exceptions.RequestException as e:
            print(f"Connection test failed: {str(e)}")
            return {"status": "error", "message": str(e)}

    def get_applications(self) -> List[Dict[str, Any]]:
        """Retrieve list of all applications."""
        endpoints = [
            f"{self.api_base_url}/apps",
            f"{self.api_base_url}/apps?limit=100",
            f"{self.api_base_url.replace('/api/v1', '')}/api/v1/apps?limit=100",
        ]

        last_status = None
        last_error = None

        for url in endpoints:
            try:
                print(f"Fetching apps from: {url}")
                response = self._request_with_auth_fallback("GET", url, timeout=10)
                print(f"Response status: {response.status_code}")
                print(f"Response text: {response.text[:500]}")
                last_status = response.status_code

                if response.status_code == 200:
                    data = response.json()

                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        if "data" in data and isinstance(data["data"], list):
                            return data["data"]
                        if "items" in data and isinstance(data["items"], list):
                            return data["items"]
                        if "apps" in data and isinstance(data["apps"], list):
                            return data["apps"]
                        if "id" in data or "name" in data:
                            return [data]
            except Exception as e:
                last_error = str(e)
                print(f"Error with endpoint {url}: {last_error}")

        if last_status == 401:
            extra = f" OAuth detail: {self._last_auth_error}" if self._last_auth_error else ""
            raise RuntimeError(
                f"Qlik authentication failed (401). API key may be revoked/invalid, or OAuth client credentials/scopes are not valid.{extra}"
            )

        raise RuntimeError(
            f"Failed to fetch applications from Qlik (last_status={last_status}, last_error={last_error})"
        )

    def get_application_details(self, app_id: str) -> Dict[str, Any]:
        """Get details of a specific application."""
        url = f"{self.api_base_url}/apps/{app_id}"
        try:
            print(f"Fetching app details from: {url}")
            response = self._request_with_auth_fallback("GET", url, timeout=10)
            print(f"App details response status: {response.status_code}")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching application details: {str(e)}")
            return {"error": str(e)}

    def get_application_data(self, app_id: str) -> Dict[str, Any]:
        """Fetch data from a specific application using evaluation API."""
        endpoints_to_try = [
            f"{self.api_base_url}/apps/{app_id}/data/metadata",
            f"{self.api_base_url}/apps/{app_id}/evaluation/data",
            f"{self.api_base_url}/apps/{app_id}/objects",
        ]

        for url in endpoints_to_try:
            try:
                print(f"Trying to fetch app data from: {url}")
                response = self._request_with_auth_fallback("GET", url, timeout=10)
                print(f"Data response status: {response.status_code}")
                if response.status_code == 200:
                    return response.json()
            except requests.exceptions.RequestException as e:
                print(f"Error with endpoint {url}: {str(e)}")
                continue

        return {"error": "Could not retrieve application data from any endpoint"}

    def get_applications_with_details(self) -> List[Dict[str, Any]]:
        """Get applications with their details."""
        apps = self.get_applications()
        for app in apps:
            app_id = app.get("id") or app.get("qDocId")
            if app_id:
                try:
                    details = self.get_application_details(app_id)
                    app["details"] = details
                except Exception as e:
                    app["details"] = {"error": str(e)}
        return apps

    def get_spaces(self) -> List[Dict[str, Any]]:
        """Get list of spaces."""
        url = f"{self.api_base_url}/spaces"
        try:
            response = self._request_with_auth_fallback("GET", url, timeout=10)
            response.raise_for_status()
            data = response.json()
            return data.get("data", []) if isinstance(data, dict) else data
        except requests.exceptions.RequestException as e:
            print(f"Error fetching spaces: {str(e)}")
            return []
