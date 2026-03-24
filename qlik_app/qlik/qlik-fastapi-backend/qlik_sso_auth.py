"""
Qlik Cloud SSO/OAuth 2.0 Authentication Module
This module handles OAuth 2.0 authorization code flow for Qlik Cloud SSO
"""

import os
import secrets
import string
import requests
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional, Dict
import json
import hashlib
import base64
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

# OAuth Configuration
QLIK_CLIENT_ID = os.getenv('QLIK_CLIENT_ID', '')
QLIK_CLIENT_SECRET = os.getenv('QLIK_CLIENT_SECRET', '')
QLIK_TENANT_URL = os.getenv('QLIK_TENANT_URL', 'https://vtcej92i1jgxph5.in.qlikcloud.com')
QLIK_API_BASE_URL = os.getenv('QLIK_API_BASE_URL', f'{QLIK_TENANT_URL}/api/v1')

# Generate a random string for state parameter
def generate_state() -> str:
    """Generate a random state parameter for OAuth security"""
    return secrets.token_urlsafe(32)

# Generate code verifier for PKCE
def generate_code_verifier() -> str:
    """Generate a code verifier for PKCE extension"""
    return secrets.token_urlsafe(32)

# Create code challenge from verifier
def create_code_challenge(verifier: str) -> str:
    """Create PKCE code challenge from verifier"""
    digest = hashlib.sha256(verifier.encode('utf-8')).digest()
    return base64.urlsafe_b64encode(digest).decode('utf-8').rstrip('=')

# In-memory storage for OAuth state (in production, use Redis or similar)
# Format: {state: {code_verifier, tenant_url, redirect_uri, created_at}}
oauth_states: Dict[str, dict] = {}

# Session storage for authenticated users
# Format: {session_token: {user_info, tenant_url, access_token, refresh_token}}
user_sessions: Dict[str, dict] = {}


class SSOConfigPayload(BaseModel):
    tenant_url: str
    redirect_uri: str = "http://localhost:5173/callback"


class SSOInitResponse(BaseModel):
    authorization_url: str
    state: str
    code_verifier: str


class SSOCallbackPayload(BaseModel):
    code: str
    state: str
    code_verifier: str


class SSOValidatePayload(BaseModel):
    session_token: str


@router.post("/sso/initiate")
async def sso_initiate(payload: SSOConfigPayload):
    """
    Initiate OAuth 2.0 Authorization Code flow with Qlik Cloud
    Returns the authorization URL for user to authenticate
    """
    if not QLIK_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="OAuth client ID not configured. Please set QLIK_CLIENT_ID in .env"
        )
    
    # Normalize tenant URL
    tenant_url = payload.tenant_url.rstrip('/')
    if not tenant_url.startswith('http'):
        tenant_url = f'https://{tenant_url}'
    
    # Build OAuth URLs for this tenant
    auth_endpoint = f"{tenant_url}/oauth/authorize"
    token_endpoint = f"{tenant_url}/oauth/token"
    
    # Generate security parameters
    state = generate_state()
    code_verifier = generate_code_verifier()
    code_challenge = create_code_challenge(code_verifier)
    
    # Store state with verifier for later validation
    oauth_states[state] = {
        'code_verifier': code_verifier,
        'tenant_url': tenant_url,
        'redirect_uri': payload.redirect_uri,
        'token_endpoint': token_endpoint,
        'created_at': __import__('time').time()
    }
    
    # Build authorization URL with PKCE
    auth_params = {
        'response_type': 'code',
        'client_id': QLIK_CLIENT_ID,
        'redirect_uri': payload.redirect_uri,
        'scope': 'read read:analysis read:apps read:data',
        'state': state,
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256'
    }
    
    authorization_url = auth_endpoint + '?' + '&'.join([f'{k}={requests.utils.quote(v)}' for k, v in auth_params.items()])
    
    return {
        "authorization_url": authorization_url,
        "state": state,
        "code_verifier": code_verifier
    }


@router.post("/sso/callback")
async def sso_callback(payload: SSOCallbackPayload):
    """
    Handle OAuth callback and exchange authorization code for tokens
    """
    state = payload.state
    code = payload.code
    code_verifier = payload.code_verifier
    
    # Validate state
    if state not in oauth_states:
        raise HTTPException(
            status_code=400,
            detail="Invalid state parameter. Possible CSRF attack."
        )
    
    state_data = oauth_states[state]
    
    # Check if state has expired (10 minutes)
    import time
    if time.time() - state_data['created_at'] > 600:
        del oauth_states[state]
        raise HTTPException(
            status_code=400,
            detail="State parameter expired. Please try again."
        )
    
    tenant_url = state_data['tenant_url']
    token_endpoint = state_data['token_endpoint']
    redirect_uri = state_data['redirect_uri']
    
    # Exchange authorization code for tokens
    token_data = {
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': QLIK_CLIENT_ID,
        'client_secret': QLIK_CLIENT_SECRET,
        'code_verifier': code_verifier
    }
    
    try:
        response = requests.post(
            token_endpoint,
            data=token_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=30
        )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Token exchange failed: {response.text}"
            )
        
        tokens = response.json()
        access_token = tokens.get('access_token')
        refresh_token = tokens.get('refresh_token', '')
        
        # Get user info from Qlik Cloud
        user_info = await get_user_info(tenant_url, access_token)
        
        # Create session token
        session_token = secrets.token_urlsafe(32)
        
        # Store user session
        user_sessions[session_token] = {
            'user_info': user_info,
            'tenant_url': tenant_url,
            'access_token': access_token,
            'refresh_token': refresh_token,
            'created_at': time.time()
        }
        
        # Clean up state
        del oauth_states[state]
        
        return {
            "success": True,
            "session_token": session_token,
            "user_info": user_info,
            "tenant_url": tenant_url
        }
        
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to exchange authorization code: { str(e)}"
        )


async def get_user_info(tenant_url: str, access_token: str) -> dict:
    """Get user information from Qlik Cloud"""
    try:
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        # Try to get user info from user endpoint
        response = requests.get(
            f"{tenant_url}/api/v1/users/me",
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            user_data = response.json()
            return {
                'id': user_data.get('id', ''),
                'name': user_data.get('name', ''),
                'email': user_data.get('email', ''),
                'tenant': tenant_url
            }
        else:
            # Return basic info if we can't get user details
            return {
                'id': 'unknown',
                'name': 'Qlik User',
                'email': 'unknown',
                'tenant': tenant_url
            }
    except Exception as e:
        return {
            'id': 'unknown',
            'name': 'Qlik User',
            'email': 'unknown',
            'tenant': tenant_url,
            'error': str(e)
        }


@router.post("/sso/validate")
async def sso_validate(payload: SSOValidatePayload):
    """
    Validate a session token and return user info
    """
    session_token = payload.session_token
    
    if session_token not in user_sessions:
        raise HTTPException(
            status_code=401,
            detail="Invalid session token"
        )
    
    session_data = user_sessions[session_token]
    
    # Check if session has expired (24 hours)
    import time
    if time.time() - session_data['created_at'] > 86400:
        del user_sessions[session_token]
        raise HTTPException(
            status_code=401,
            detail="Session expired"
        )
    
    return {
        "valid": True,
        "user_info": session_data['user_info'],
        "tenant_url": session_data['tenant_url']
    }


@router.post("/sso/logout")
async def sso_logout(payload: SSOValidatePayload):
    """
    Logout user and invalidate session
    """
    session_token = payload.session_token
    
    if session_token in user_sessions:
        del user_sessions[session_token]
    
    return {"success": True, "message": "Logged out successfully"}


@router.post("/sso/refresh")
async def sso_refresh(payload: SSOValidatePayload):
    """
    Refresh access token using refresh token
    """
    session_token = payload.session_token
    
    if session_token not in user_sessions:
        raise HTTPException(
            status_code=401,
            detail="Invalid session token"
        )
    
    session_data = user_sessions[session_token]
    refresh_token = session_data.get('refresh_token')
    
    if not refresh_token:
        raise HTTPException(
            status_code=400,
            detail="No refresh token available"
        )
    
    # Refresh the token
    tenant_url = session_data['tenant_url']
    token_endpoint = f"{tenant_url}/oauth/token"
    
    token_data = {
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': QLIK_CLIENT_ID,
        'client_secret': QLIK_CLIENT_SECRET
    }
    
    try:
        response = requests.post(
            token_endpoint,
            data=token_data,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=30
        )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=400,
                detail=f"Token refresh failed: {response.text}"
            )
        
        tokens = response.json()
        new_access_token = tokens.get('access_token')
        new_refresh_token = tokens.get('refresh_token', refresh_token)
        
        # Update session
        session_data['access_token'] = new_access_token
        session_data['refresh_token'] = new_refresh_token
        user_sessions[session_token] = session_data
        
        return {
            "success": True,
            "access_token": new_access_token
        }
        
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh token: {str(e)}"
        )


def get_user_session(session_token: str) -> Optional[dict]:
    """Get user session data if valid"""
    return user_sessions.get(session_token)


def get_user_access_token(session_token: str) -> Optional[str]:
    """Get access token for a session"""
    session = user_sessions.get(session_token)
    if session:
        return session.get('access_token')
    return None
