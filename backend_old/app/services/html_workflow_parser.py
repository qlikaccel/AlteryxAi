"""
HTML-based Workflow Parser for Alteryx Designer

Fetches workflow information by parsing HTML from /designer/ page
without relying on API endpoints. Uses BeautifulSoup to extract
workflow names from the page.

Method:
1. Fetch HTML from Designer page with authenticated session
2. Parse with BeautifulSoup
3. Extract workflow names matching criteria:
   - Starts with "workflow_"
   - Contains "data"
4. Return unique list
"""

import logging
import re
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
import requests

logger = logging.getLogger(__name__)


class HTMLWorkflowParser:
    """Extracts workflows from Alteryx Designer HTML"""
    
    def __init__(self):
        self.workflows: List[Dict[str, str]] = []
    
    def parse_designer_page(
        self,
        session: requests.Session,
        base_url: str,
        timeout: int = 10
    ) -> List[Dict[str, str]]:
        """
        Fetch and parse Alteryx Designer page to extract workflows.
        
        Args:
            session: Authenticated requests.Session with cookies
            base_url: Alteryx Cloud base URL (e.g., https://us1.alteryxcloud.com)
            timeout: Request timeout in seconds
            
        Returns:
            List of workflow dictionaries with 'id' and 'name' keys
        """
        try:
            base_url = base_url.rstrip('/')
            designer_url = f"{base_url}/designer/"
            
            logger.info(f"Fetching Designer page from: {designer_url}")
            
            # Fetch the Designer page with session cookies
            headers = {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": base_url
            }
            
            response = session.get(
                designer_url,
                timeout=timeout,
                verify=True,
                headers=headers
            )
            
            logger.info(f"Designer page status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"Failed to fetch Designer page: {response.status_code}")
                return []
            
            # Parse HTML
            html_content = response.text
            logger.debug(f"Received HTML content: {len(html_content)} bytes")
            
            workflows = self._extract_workflows_from_html(html_content)
            logger.info(f"Extracted {len(workflows)} workflows from HTML")
            
            self.workflows = workflows
            return workflows
            
        except requests.Timeout:
            logger.error(f"Timeout fetching Designer page")
            return []
        except requests.ConnectionError as e:
            logger.error(f"Connection error: {e}")
            return []
        except Exception as e:
            logger.error(f"Error parsing Designer page: {e}")
            return []
    
    def _extract_workflows_from_html(self, html_content: str) -> List[Dict[str, str]]:
        """
        Parse HTML and extract workflow information.
        
        Looks for:
        1. Workflow names in text nodes
        2. Data attributes containing workflow info
        3. Links/buttons with workflow references
        
        Filters by:
        - Names starting with "workflow_"
        - Names containing "data"
        
        Args:
            html_content: Raw HTML content from Designer page
            
        Returns:
            List of unique workflow dictionaries
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            workflows = []
            workflow_names = set()  # Track unique names
            
            # Strategy 1: Look for workflow names in text content
            logger.debug("Scanning for workflow names in page text...")
            
            # Find all text nodes and look for workflow patterns
            for element in soup.find_all(['div', 'span', 'p', 'a', 'button', 'li']):
                text = element.get_text(strip=True)
                
                if text and len(text) > 0:
                    # Extract potential workflow names
                    workflow_names.update(self._find_workflow_names(text))
            
            # Strategy 2: Look for workflow names in data attributes
            logger.debug("Scanning for workflow names in data attributes...")
            
            for element in soup.find_all(True):  # Find all elements
                # Check data attributes
                for key, value in element.attrs.items():
                    if key.startswith('data-') and isinstance(value, str):
                        workflow_names.update(self._find_workflow_names(value))
                
                # Check common attributes that might contain workflow info
                for attr in ['data-workflow', 'data-name', 'id', 'name', 'value', 'title']:
                    if attr in element.attrs:
                        value = element.attrs[attr]
                        if isinstance(value, str):
                            workflow_names.update(self._find_workflow_names(value))
            
            # Strategy 3: Look for JSON-embedded workflow data
            logger.debug("Scanning for JSON-embedded workflow data...")
            
            # Find script tags that might contain workflow data
            for script in soup.find_all('script'):
                script_content = script.string
                if script_content:
                    workflow_names.update(self._find_workflow_names(script_content))
            
            # Create workflow objects and remove duplicates
            workflow_ids = set()
            unique_workflows = []
            
            for name in sorted(workflow_names):
                # Generate consistent ID from name
                workflow_id = self._generate_workflow_id(name)
                
                if workflow_id not in workflow_ids:
                    workflow_ids.add(workflow_id)
                    unique_workflows.append({
                        "id": workflow_id,
                        "name": name,
                        "source": "html_parser"
                    })
                    logger.debug(f"Extracted workflow: {name}")
            
            logger.info(f"Found {len(unique_workflows)} unique workflows")
            return unique_workflows
            
        except Exception as e:
            logger.error(f"Error extracting workflows from HTML: {e}")
            return []
    
    def _find_workflow_names(self, text: str) -> set:
        """
        Find workflow names matching criteria in text.
        
        Criteria:
        1. Starts with "workflow_"
        2. Contains "data" (case-insensitive)
        
        Args:
            text: Text to search
            
        Returns:
            Set of workflow names found
        """
        names = set()
        
        try:
            # Pattern 1: workflow_* (alphanumeric, underscore, hyphen, dot)
            workflow_pattern = r'\bworkflow_[\w\-\.]+\b'
            for match in re.finditer(workflow_pattern, text):
                name = match.group(0)
                if self._is_valid_workflow_name(name):
                    names.add(name)
            
            # Pattern 2: Contains "data" (case-insensitive) with word boundaries
            # Look for sequences that contain "data" and are likely workflow names
            data_patterns = [
                r'\b[\w\-\.]*data[\w\-\.]*\b',  # Contains "data"
                r'\b[\w\-\.]*dataset[\w\-\.]*\b',  # Contains "dataset"
                r'\b[\w\-\.]*table[\w\-\.]*\b',  # Contains "table"
                r'\b[\w\-\.]*workflow[\w\-\.]*\b',  # Contains "workflow"
            ]
            
            for pattern in data_patterns:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    name = match.group(0)
                    if self._is_valid_workflow_name(name):
                        names.add(name)
        
        except Exception as e:
            logger.debug(f"Error finding workflow names: {e}")
        
        return names
    
    def _is_valid_workflow_name(self, name: str) -> bool:
        """
        Validate workflow name.
        
        Rules:
        - Must be 3-255 characters
        - Must contain alphanumeric, underscore, hyphen, or dot
        - Must not be a generic HTML element or attribute
        
        Args:
            name: Workflow name to validate
            
        Returns:
            True if valid, False otherwise
        """
        # Length check
        if not name or len(name) < 3 or len(name) > 255:
            return False
        
        # Must match workflow pattern
        if not re.match(r'^[\w\-\.]+$', name):
            return False
        
        # Exclude common HTML attributes/elements
        excluded = {
            'id', 'class', 'style', 'data', 'type', 'name', 'value',
            'href', 'src', 'alt', 'title', 'placeholder', 'data-id',
            'onclick', 'onload', 'onerror', 'content', 'http', 'https',
            'www', 'com', 'org', 'json', 'html', 'css', 'js', 'script',
            'button', 'input', 'form', 'div', 'span', 'p', 'a', 'img',
            'true', 'false', 'null', 'undefined', 'NaN', 'Infinity'
        }
        
        if name.lower() in excluded:
            return False
        
        # Must start with letter or underscore (not number)
        if not re.match(r'^[a-zA-Z_]', name):
            return False
        
        return True
    
    def _generate_workflow_id(self, name: str) -> str:
        """
        Generate consistent workflow ID from name.
        
        Converts to lowercase and replaces special chars with hyphens.
        
        Args:
            name: Workflow name
            
        Returns:
            Normalized workflow ID
        """
        # Convert to lowercase
        id_str = name.lower()
        
        # Replace dots and underscores with hyphens for consistency
        id_str = re.sub(r'[._]', '-', id_str)
        
        # Remove any remaining special characters
        id_str = re.sub(r'[^a-z0-9\-]', '', id_str)
        
        # Remove duplicate hyphens
        id_str = re.sub(r'-+', '-', id_str)
        
        # Remove leading/trailing hyphens
        id_str = id_str.strip('-')
        
        return id_str or 'workflow'
    
    def get_workflows(self) -> List[Dict[str, str]]:
        """Get parsed workflows"""
        return self.workflows


# Global parser instance
_parser = None


def get_html_workflow_parser() -> HTMLWorkflowParser:
    """Get global HTML workflow parser instance"""
    global _parser
    if _parser is None:
        _parser = HTMLWorkflowParser()
    return _parser
