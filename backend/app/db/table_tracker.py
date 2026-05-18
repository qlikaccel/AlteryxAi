"""
Table Tracker - Tracks when tables are first discovered/added to apps
Stores metadata to identify recently added tables
"""
 
import json
import os
from datetime import datetime
from typing import Dict, List, Any, Optional
import time
 
TRACKER_FILE = "table_tracker.json"
 
class TableTracker:
    """Manages table metadata including creation timestamps"""
   
    def __init__(self, tracker_file: str = TRACKER_FILE):
        self.tracker_file = tracker_file
        self.data = self._load_data()
   
    def _load_data(self) -> Dict[str, Any]:
        """Load tracking data from file"""
        if os.path.exists(self.tracker_file):
            try:
                with open(self.tracker_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Warning: Could not load tracker file: {e}")
                return {}
        return {}
   
    def _save_data(self) -> None:
        """Save tracking data to file"""
        try:
            with open(self.tracker_file, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save tracker file: {e}")
   
    def record_tables(self, app_id: str, table_names: List[str]) -> None:
        """
        Record tables for an app.
        - New tables get the current timestamp
        - Existing tables keep their original timestamp
        """
        if app_id not in self.data:
            self.data[app_id] = {}
       
        app_tables = self.data[app_id]
        current_time = datetime.utcnow().isoformat()
       
        for table_name in table_names:
            if table_name not in app_tables:
                # New table - record timestamp
                app_tables[table_name] = {
                    "added_at": current_time,
                    "added_timestamp": time.time()
                }
            # Existing tables keep their original timestamp
       
        self._save_data()
   
    def get_table_info(self, app_id: str, table_names: List[str]) -> Dict[str, Any]:
        """
        Get table info with timestamps.
        Returns dict mapping table name to metadata with added_at timestamp
        """
        result = {}
        current_time = datetime.utcnow().isoformat()
        current_timestamp = time.time()
       
        if app_id in self.data:
            app_tables = self.data[app_id]
            for table_name in table_names:
                if table_name in app_tables:
                    result[table_name] = app_tables[table_name]
                else:
                    # New table not yet tracked
                    result[table_name] = {
                        "added_at": current_time,
                        "added_timestamp": current_timestamp,
                        "is_new": True
                    }
        else:
            # App not tracked yet - all tables are new
            for table_name in table_names:
                result[table_name] = {
                    "added_at": current_time,
                    "added_timestamp": current_timestamp,
                    "is_new": True
                }
       
        return result
   
    def mark_tables_as_seen(self, app_id: str, table_names: List[str]) -> None:
        """Mark tables as seen (recorded in tracking system)"""
        self.record_tables(app_id, table_names)
   
    def get_recently_added(self, app_id: str, table_names: List[str],
                          hours: int = 24) -> List[str]:
        """
        Get list of tables added in the last N hours
        """
        if app_id not in self.data:
            return table_names  # All are new
       
        current_time = time.time()
        time_threshold = current_time - (hours * 3600)
       
        app_tables = self.data[app_id]
        recently_added = []
       
        for table_name in table_names:
            if table_name in app_tables:
                added_timestamp = app_tables[table_name].get("added_timestamp", 0)
                if added_timestamp >= time_threshold:
                    recently_added.append(table_name)
            else:
                # Not in tracking = new/recently added
                recently_added.append(table_name)
       
        # Sort by timestamp (newest first)
        def get_timestamp(name):
            if app_id in self.data and name in self.data[app_id]:
                return self.data[app_id][name].get("added_timestamp", 0)
            return 0
       
        recently_added.sort(key=get_timestamp, reverse=True)
        return recently_added
 
 
# Global tracker instance
_tracker: Optional[TableTracker] = None
 
def get_tracker() -> TableTracker:
    """Get or create global tracker instance"""
    global _tracker
    if _tracker is None:
        _tracker = TableTracker()
    return _tracker
 
def enhance_tables_with_timestamps(app_id: str, tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Enhance table objects with timestamp information and sort by recency
    """
    tracker = get_tracker()
   
    table_names = [t.get('name') if isinstance(t, dict) else t for t in tables]
    table_info = tracker.get_table_info(app_id, table_names)
   
    # Record these tables
    tracker.record_tables(app_id, table_names)
   
    # Add timestamp info to each table
    enhanced = []
    for table in tables:
        if isinstance(table, dict):
            table_name = table.get('name', '')
            table['added_at'] = table_info.get(table_name, {}).get('added_at')
            table['added_timestamp'] = table_info.get(table_name, {}).get('added_timestamp')
            table['is_new'] = table_info.get(table_name, {}).get('is_new', False)
            enhanced.append(table)
        else:
            # Simple string table name
            table_obj = {
                'name': table,
                'added_at': table_info.get(table, {}).get('added_at'),
                'added_timestamp': table_info.get(table, {}).get('added_timestamp'),
                'is_new': table_info.get(table, {}).get('is_new', False)
            }
            enhanced.append(table_obj)
   
    # Sort by added_timestamp (newest first)
    enhanced.sort(key=lambda t: t.get('added_timestamp', 0), reverse=True)
   
    return enhanced