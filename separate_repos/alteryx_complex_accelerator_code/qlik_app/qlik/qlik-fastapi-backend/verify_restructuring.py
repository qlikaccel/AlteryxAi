#!/usr/bin/env python3
"""
Project Restructuring Verification Script
==========================================

This script verifies that the project has been correctly restructured
and all imports are working properly.

Run: python verify_restructuring.py
"""

import sys
import os
from pathlib import Path


def check_directory_structure():
    """Verify all required directories exist."""
    print("\n" + "="*70)
    print("CHECKING DIRECTORY STRUCTURE")
    print("="*70)
    
    required_dirs = {
        "app": ["core", "api", "db", "dependencies", "exceptions", "models", "schemas", "services", "utils"],
        "app/api": ["v1"],
        "app/api/v1": ["endpoints"],
        "tests": [],
        "scripts": [],
    }
    
    all_good = True
    for parent, subdirs in required_dirs.items():
        parent_path = Path(parent)
        
        if not parent_path.exists():
            print(f"  ✗ MISSING: {parent}/")
            all_good = False
            continue
        
        print(f"  ✓ {parent}/")
        
        for subdir in subdirs:
            subdir_path = parent_path / subdir
            if not subdir_path.exists():
                print(f"    ✗ MISSING: {parent}/{subdir}/")
                all_good = False
            else:
                print(f"    ✓ {subdir}/")
    
    return all_good


def check_init_files():
    """Verify __init__.py files exist in all packages."""
    print("\n" + "="*70)
    print("CHECKING __init__.py FILES")
    print("="*70)
    
    packages = [
        "app",
        "app/core",
        "app/api",
        "app/api/v1",
        "app/api/v1/endpoints",
        "app/db",
        "app/dependencies",
        "app/exceptions",
        "app/models",
        "app/schemas",
        "app/services",
        "app/utils",
        "tests",
    ]
    
    all_good = True
    for package in packages:
        init_file = Path(package) / "__init__.py"
        if init_file.exists():
            print(f"  ✓ {package}/__init__.py")
        else:
            print(f"  ✗ MISSING: {package}/__init__.py")
            all_good = False
    
    return all_good


def count_files_in_directories():
    """Count Python files in key directories."""
    print("\n" + "="*70)
    print("FILE ORGANIZATION SUMMARY")
    print("="*70)
    
    dirs_to_check = {
        "app/services": "Service modules",
        "app/utils": "Utility modules",
        "app/api/v1/endpoints": "Endpoint handlers",
        "app/db": "Database modules",
        "tests": "Test & debug files",
    }
    
    for dir_path, description in dirs_to_check.items():
        path = Path(dir_path)
        if path.exists():
            py_files = list(path.glob("*.py"))
            json_files = list(path.glob("*.json"))
            total_files = len(py_files) + len(json_files)
            print(f"  ✓ {dir_path}: {total_files} files ({description})")
        else:
            print(f"  ✗ {dir_path}: NOT FOUND")


def check_key_imports():
    """Try importing key modules to verify imports work."""
    print("\n" + "="*70)
    print("TESTING KEY IMPORTS")
    print("="*70)
    
    # Add project root to path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    imports_to_test = [
        ("app", "FastAPI app package"),
        ("app.services", "Services package"),
        ("app.utils", "Utils package"),
        ("app.db", "Database package"),
        ("app.api", "API package"),
    ]
    
    all_good = True
    for module_path, description in imports_to_test:
        try:
            __import__(module_path)
            print(f"  ✓ import {module_path:<30} ({description})")
        except ImportError as e:
            print(f"  ✗ import {module_path:<30} - ERROR: {e}")
            all_good = False
        except Exception as e:
            print(f"  ✗ import {module_path:<30} - ERROR: {e}")
            all_good = False
    
    return all_good


def check_main_entry_point():
    """Verify main.py entry point exists and is a wrapper."""
    print("\n" + "="*70)
    print("CHECKING ENTRY POINTS")
    print("="*70)
    
    if Path("main.py").exists():
        print("  ✓ main.py (root wrapper)")
    else:
        print("  ✗ main.py NOT FOUND")
        return False
    
    if Path("app/main.py").exists():
        print("  ✓ app/main.py (FastAPI app)")
    else:
        print("  ✗ app/main.py NOT FOUND")
        return False
    
    if Path("main_old.py").exists():
        print("  ✓ main_old.py (backup)")
    else:
        print("  ⚠ main_old.py NOT FOUND (optional)")
    
    return True


def main():
    """Run all checks."""
    print("\n" + "="*70)
    print("PROJECT RESTRUCTURING VERIFICATION")
    print("="*70)
    
    results = {
        "Directory Structure": check_directory_structure(),
        "__init__.py Files": check_init_files(),
        "Entry Points": check_main_entry_point(),
    }
    
    count_files_in_directories()
    
    # Test imports only if basic structure is good
    if results.get("Directory Structure"):
        results["Key Imports"] = check_key_imports()
    
    # Summary
    print("\n" + "="*70)
    print("VERIFICATION SUMMARY")
    print("="*70)
    
    for check_name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {check_name}")
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n" + "🎉 "*20)
        print("✓ ALL CHECKS PASSED!")
        print("Your project has been successfully restructured!")
        print("="*70)
        print("\nNext steps:")
        print("  1. Run the app: uvicorn main:app --reload")
        print("  2. Test the endpoints")
        print("  3. Update your deployment configuration if needed")
        print("  4. Read PROJECT_RESTRUCTURE.md for more details")
        print()
        return 0
    else:
        print("\n" + "⚠️ "*20)
        print("✗ SOME CHECKS FAILED")
        print("Please review the errors above and fix them.")
        print("="*70)
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
