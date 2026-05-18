"""
Conversion Logger Module - Live Progress Tracking

Tracks conversion progress with visual logging that can be retrieved via API endpoints.
Maintains in-memory session storage for real-time progress updates.
"""

import logging
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from enum import Enum
import uuid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class LogLevel(str, Enum):
    """Log level enumeration"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


class ConversionSession:
    """Represents a single conversion session with logging"""
    
    def __init__(self, session_id: str = None):
        """Initialize a new conversion session"""
        self.session_id = session_id or str(uuid.uuid4())
        self.start_time = datetime.now()
        self.end_time = None
        self.logs: List[Dict[str, Any]] = []
        self.current_phase = 0
        self.status = "PENDING"  # PENDING, RUNNING, COMPLETED, FAILED
        self.error_message = None
        self.progress_percentage = 0
        
        # Conversion data
        self.loadscript = None
        self.parsed_script = None
        self.m_query = None
        self.tables_count = 0
        self.fields_count = 0
        
    def add_log(self, message: str, level: LogLevel = LogLevel.INFO, phase: int = None, data: Dict = None):
        """Add a log entry"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "level": level.value,
            "message": message,
            "phase": phase or self.current_phase,
            "data": data or {}
        }
        self.logs.append(log_entry)
        
        # Log to console as well
        if level == LogLevel.SUCCESS:
            logger.info(f"✅ {message}")
        elif level == LogLevel.ERROR:
            logger.error(f"❌ {message}")
        elif level == LogLevel.WARNING:
            logger.warning(f"⚠️  {message}")
        else:
            logger.info(f"📍 {message}")
    
    def set_phase(self, phase: int, description: str = ""):
        """Set current phase and log it"""
        self.current_phase = phase
        phase_names = {
            1: "Connection Test",
            2: "Fetch Apps",
            3: "App Details",
            4: "Fetch LoadScript",
            5: "Parse Script",
            6: "Convert to M Query",
            7: "Generate Files"
        }
        phase_name = phase_names.get(phase, f"Phase {phase}")
        message = f"PHASE {phase}: {phase_name}" + (f" - {description}" if description else "")
        self.add_log(f"{'='*60}\n{message}\n{'='*60}", LogLevel.INFO, phase)
        
    def set_progress(self, percentage: int, message: str = ""):
        """Update progress percentage"""
        self.progress_percentage = min(100, max(0, percentage))
        if message:
            self.add_log(f"{message} ({self.progress_percentage}%)", LogLevel.INFO)
    
    def set_status(self, status: str, message: str = ""):
        """Set conversion status"""
        self.status = status
        if message:
            if status == "COMPLETED":
                self.add_log(message, LogLevel.SUCCESS)
            elif status == "FAILED":
                self.add_log(message, LogLevel.ERROR)
                self.error_message = message
            else:
                self.add_log(message, LogLevel.INFO)
    
    def set_result_data(self, loadscript: str = None, parsed: Dict = None, m_query: str = None):
        """Set conversion result data"""
        if loadscript:
            self.loadscript = loadscript
        if parsed:
            self.parsed_script = parsed
            self.tables_count = parsed.get('summary', {}).get('tables_count', 0)
            self.fields_count = parsed.get('summary', {}).get('fields_count', 0)
        if m_query:
            self.m_query = m_query
    
    def finalize(self):
        """Mark session as complete"""
        self.end_time = datetime.now()
        duration = (self.end_time - self.start_time).total_seconds()
        self.add_log(f"Session completed in {duration:.2f} seconds", LogLevel.SUCCESS)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert session to dictionary for API response"""
        duration = 0
        if self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()
        else:
            duration = (datetime.now() - self.start_time).total_seconds()
        
        return {
            "session_id": self.session_id,
            "status": self.status,
            "progress": self.progress_percentage,
            "current_phase": self.current_phase,
            "duration_seconds": duration,
            "log_count": len(self.logs),
            "error_message": self.error_message,
            "data_summary": {
                "tables_count": self.tables_count,
                "fields_count": self.fields_count,
                "loadscript_length": len(self.loadscript) if self.loadscript else 0,
                "m_query_length": len(self.m_query) if self.m_query else 0,
            },
            "timestamp_start": self.start_time.isoformat(),
            "timestamp_end": self.end_time.isoformat() if self.end_time else None
        }


class ConversionSessionManager:
    """Manages multiple conversion sessions"""
    
    def __init__(self):
        """Initialize the session manager"""
        self.sessions: Dict[str, ConversionSession] = {}
        self.max_sessions = 100  # Keep last 100 sessions in memory
    
    def create_session(self) -> str:
        """Create a new conversion session"""
        session = ConversionSession()
        self.sessions[session.session_id] = session
        
        # Clean up old sessions if needed
        if len(self.sessions) > self.max_sessions:
            oldest_session_id = min(
                self.sessions.keys(),
                key=lambda k: self.sessions[k].start_time
            )
            del self.sessions[oldest_session_id]
        
        logger.info(f"Created conversion session: {session.session_id}")
        return session.session_id
    
    def get_session(self, session_id: str) -> Optional[ConversionSession]:
        """Get a session by ID"""
        return self.sessions.get(session_id)
    
    def log_to_session(self, session_id: str, message: str, level: LogLevel = LogLevel.INFO, 
                      phase: int = None, data: Dict = None):
        """Add log to a specific session"""
        session = self.get_session(session_id)
        if session:
            session.add_log(message, level, phase, data)
    
    def get_all_sessions(self) -> List[Dict[str, Any]]:
        """Get all active sessions"""
        return [session.to_dict() for session in self.sessions.values()]
    
    def get_session_logs(self, session_id: str, limit: int = None) -> List[Dict[str, Any]]:
        """Get logs for a specific session"""
        session = self.get_session(session_id)
        if not session:
            return []
        
        logs = session.logs
        if limit:
            logs = logs[-limit:]
        return logs
    
    def get_session_data(self, session_id: str) -> Dict[str, Any]:
        """Get full session data including logs and results"""
        session = self.get_session(session_id)
        if not session:
            return {"error": "Session not found"}
        
        return {
            "session": session.to_dict(),
            "logs": session.logs,
            "data": {
                "loadscript": session.loadscript,
                "parsed_script": session.parsed_script,
                "m_query": session.m_query
            }
        }


# Global session manager instance
_session_manager = ConversionSessionManager()


def get_session_manager() -> ConversionSessionManager:
    """Get the global session manager instance"""
    return _session_manager
