#!/usr/bin/env python3
"""
🚀 COMPLETE WORKFLOW: Fetch LoadScript → Parse → Convert to M Query → Download
This script does everything in one go and generates a ready-to-use M Query for Power BI
"""

import requests
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from complete_mquery_generator import CompleteMQueryGenerator

# Colors for terminal output
class Color:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'

def print_step(step_num, title):
    print(f"\n{Color.BOLD}{Color.BLUE}{'='*80}{Color.END}")
    print(f"{Color.BOLD}{Color.GREEN}STEP {step_num}: {title}{Color.END}")
    print(f"{Color.BOLD}{Color.BLUE}{'='*80}{Color.END}\n")

def print_success(msg):
    print(f"{Color.GREEN}✅ {msg}{Color.END}")

def print_error(msg):
    print(f"{Color.RED}❌ {msg}{Color.END}")

def print_info(msg):
    print(f"{Color.CYAN}ℹ️  {msg}{Color.END}")

def print_warning(msg):
    print(f"{Color.YELLOW}⚠️  {msg}{Color.END}")

def run_complete_workflow(app_id):
    """Execute the complete workflow"""
    
    print(f"\n{Color.BOLD}{Color.HEADER}")
    print("╔" + "═"*78 + "╗")
    print("║" + " "*15 + "QLIK TO POWER BI - COMPLETE WORKFLOW" + " "*27 + "║")
    print("║" + " "*9 + "Fetch → Parse → Convert → Download (Ready for Power BI)" + " "*10 + "║")
    print("╚" + "═"*78 + "╝")
    print(f"{Color.END}\n")
    
    api_base = "http://127.0.0.1:8000/api/migration"
    # api_base = "https://qliksense-stuv.onrender.com/api/migration"
    
    # ============================================================================
    # STEP 1: FETCH FULL LOADSCRIPT
    # ============================================================================
    print_step(1, "🚀 FETCH FULL LOADSCRIPT FROM QLIK CLOUD")
    print_info(f"App ID: {app_id}")
    print_info("Using: WebSocket Engine API")
    
    try:
        print("Requesting full loadscript...")
        response = requests.post(
            f"{api_base}/fetch-loadscript?app_id={app_id}",
            timeout=120
        )
        
        if response.status_code != 200:
            print_error(f"Failed with status {response.status_code}")
            print(response.text)
            return False
        
        fetch_result = response.json()
        
        if fetch_result.get('status') != 'success':
            print_warning(fetch_result.get('message', 'Unknown error'))
            if 'loadscript' not in fetch_result or not fetch_result['loadscript']:
                print_error("No loadscript content received")
                return False
        
        loadscript = fetch_result.get('loadscript', '')
        script_length = len(loadscript)
        
        print_success(f"LoadScript fetched successfully!")
        print_info(f"Method: {fetch_result.get('method')}")
        print_info(f"Script Length: {script_length} characters")
        print_info(f"App Name: {fetch_result.get('app_name')}")
        
        # Show preview
        print(f"\n{Color.BOLD}Preview of LoadScript:{Color.END}")
        preview_lines = loadscript.split('\n')[:5]
        for line in preview_lines:
            print(f"  {line}")
        print("  ...")
        
    except Exception as e:
        print_error(f"Error fetching loadscript: {str(e)}")
        return False
    
    # ============================================================================
    # STEP 2: PARSE LOADSCRIPT
    # ============================================================================
    print_step(2, "📋 PARSE LOADSCRIPT - EXTRACT TABLES & FIELDS")
    
    try:
        print("Sending loadscript to parser...")
        response = requests.post(
            f"{api_base}/parse-loadscript?loadscript={requests.utils.quote(loadscript)}",
            timeout=120
        )
        
        if response.status_code != 200:
            print_error(f"Failed with status {response.status_code}")
            print(response.text)
            return False
        
        parse_result = response.json()
        
        if parse_result.get('status') != 'success':
            print_error(parse_result.get('message', 'Parse failed'))
            return False
        
        print_success("LoadScript parsed successfully!")
        
        summary = parse_result.get('summary', {})
        print_info(f"Tables Found: {summary.get('tables_count', 0)}")
        print_info(f"Fields Found: {summary.get('fields_count', 0)}")
        print_info(f"Connections: {summary.get('connections_count', 0)}")
        
        details = parse_result.get('details', {})
        
        # Show table details
        if details.get('tables'):
            print(f"\n{Color.BOLD}Tables Detected:{Color.END}")
            for i, table in enumerate(details.get('tables', [])[:5], 1):
                table_name = table.get('name', 'Unknown')
                fields = table.get('fields', [])
                print(f"  {i}. {Color.CYAN}{table_name}{Color.END} ({len(fields)} fields)")
            if len(details.get('tables', [])) > 5:
                print(f"  ... and {len(details.get('tables', [])) - 5} more tables")
        
        parsed_script_json = json.dumps(parse_result)
        
    except Exception as e:
        print_error(f"Error parsing loadscript: {str(e)}")
        return False
    
    # ============================================================================
    # STEP 3: CONVERT TO M QUERY
    # ============================================================================
    print_step(3, "🔄 CONVERT TO POWERBI M QUERY")
    
    try:
        print("Sending parsed script to M Query converter...")
        response = requests.post(
            f"{api_base}/convert-to-mquery?parsed_script_json={requests.utils.quote(parsed_script_json)}",
            timeout=120
        )
        
        if response.status_code != 200:
            print_error(f"Failed with status {response.status_code}")
            print(response.text)
            return False
        
        convert_result = response.json()
        
        if convert_result.get('status') != 'success':
            print_error(convert_result.get('message', 'Conversion failed'))
            return False
        
        print_success("Conversion completed successfully!")
        
        m_query = convert_result.get('m_query', '')
        print_info(f"M Query Length: {len(m_query)} characters")
        print_info(f"Warnings: {convert_result.get('warnings_count', 0)}")
        print_info(f"Errors: {convert_result.get('errors_count', 0)}")
        
        stats = convert_result.get('statistics', {})
        print_info(f"Tables Converted: {stats.get('total_tables_converted', 0)}")
        print_info(f"Transformations: {stats.get('total_transformations', 0)}")
        print_info(f"JOINs: {stats.get('total_joins', 0)}")
        
        # Show warnings
        if convert_result.get('warnings'):
            print(f"\n{Color.YELLOW}{Color.BOLD}Warnings:{Color.END}")
            for i, warning in enumerate(convert_result.get('warnings', [])[:3], 1):
                print(f"  {i}. {warning}")
            if len(convert_result.get('warnings', [])) > 3:
                print(f"  ... and {len(convert_result.get('warnings', [])) - 3} more")
        
    except Exception as e:
        print_error(f"Error converting to M Query: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    
    # ============================================================================
    # STEP 4: CREATE POWERBI-READY FILE
    # ============================================================================
    print_step(4, "💾 CREATE COMPLETE M QUERY FILE (WITH FULL DETAILS)")
    
    try:
        # Use the CompleteMQueryGenerator to create a full, complete M Query
        print("Generating complete M Query with all table definitions...")
        
        complete_generator = CompleteMQueryGenerator(parse_result)
        complete_m_query = complete_generator.generate()
        
        print_success("Complete M Query generated!")
        print_info(f"Complete M Query Length: {len(complete_m_query)} characters")
        
        # Create file with complete M Query
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        powerbi_query = complete_m_query
        
        # Save to file
        output_dir = Path.cwd()
        filename = f"powerbi_query_{app_id.replace('-', '')[:8]}_COMPLETE.m"
        filepath = output_dir / filename
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(powerbi_query)
        
        print_success(f"M Query file created successfully!")
        print_info(f"File: {Color.BOLD}{filename}{Color.END}")
        print_info(f"Location: {filepath}")
        print_info(f"Size: {len(powerbi_query)} bytes")
        
    except Exception as e:
        print_error(f"Error creating file: {str(e)}")
        return False
    
    # ============================================================================
    # SUMMARY
    # ============================================================================
    print_step("✅", "COMPLETE WORKFLOW FINISHED")
    
    print(f"{Color.BOLD}Summary:{Color.END}")
    print(f"  ✅ Fetched full loadscript ({script_length} chars)")
    print(f"  ✅ Parsed {summary.get('tables_count', 0)} tables and {summary.get('fields_count', 0)} fields")
    print(f"  ✅ Generated COMPLETE M Query for Power BI ({len(complete_m_query)} chars)")
    print(f"  ✅ Created {Color.CYAN}{filename}{Color.END}")
    
    print(f"\n{Color.BOLD}{Color.GREEN}NEXT STEPS:{Color.END}")
    print(f"1. Open: {Color.CYAN}{filepath}{Color.END}")
    print(f"2. Copy all content")
    print(f"3. Open Power BI Desktop")
    print(f"4. Go to: Home → Get Data → Power Query Editor → Advanced Editor")
    print(f"5. Paste the M Query code")
    print(f"6. Update data source connections")
    print(f"7. Click 'Done' and configure relationships")
    
    print(f"\n{Color.BOLD}{Color.YELLOW}IMPORTANT:{Color.END}")
    print(f"• Review warnings if any")
    print(f"• Update data source references in the M Query")
    print(f"• Test the query before using in production")
    print(f"• Create relationships between tables")
    
    print(f"\n{Color.BOLD}{Color.GREEN}🎉 WORKFLOW COMPLETE! Your M Query is ready for Power BI!{Color.END}\n")
    
    return True

def main():
    """Main entry point"""
    
    if len(sys.argv) < 2:
        print(f"\n{Color.BOLD}{Color.YELLOW}Usage:{Color.END}")
        print(f"  python complete_workflow.py <APP_ID>")
        print(f"\n{Color.BOLD}Example:{Color.END}")
        print(f"  python complete_workflow.py 764185f-b9cc-4dab-8f72-35e1ba8d1547")
        print()
        
        # Use default app ID for demo
        app_id = "764185f-b9cc-4dab-8f72-35e1ba8d1547"
        print(f"{Color.CYAN}Using default app ID: {app_id}{Color.END}\n")
    else:
        app_id = sys.argv[1]
    
    success = run_complete_workflow(app_id)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
