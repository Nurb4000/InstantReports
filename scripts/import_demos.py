#!/usr/bin/env python3
"""Import demo reports into InstantReports."""

import httpx
import json
import os
from pathlib import Path

BASE_URL = "http://localhost:8080"
DEMO_DIR = Path(__file__).parent / "demos"

def main():
    # Login
    client = httpx.Client()
    login_data = {'email': 'admin@example.com', 'password': 'admin'}
    client.post(f"{BASE_URL}/auth/login", data=login_data)
    
    # Import each demo report
    for json_file in DEMO_DIR.glob("*.ir.json"):
        print(f"Importing {json_file.name}...")
        
        with open(json_file) as f:
            report_data = json.load(f)
        
        # Upload the file
        with open(json_file, 'rb') as f:
            files = {'file': (json_file.name, f, 'application/json')}
            response = client.post(f"{BASE_URL}/designer/reports/import", files=files)
        
        if response.status_code in (200, 303):
            print(f"  ✓ Successfully imported: {report_data['report']['name']}")
            if response.status_code == 303:
                location = response.headers.get('location', '')
                report_id = location.split('/')[-1] if location else 'unknown'
                print(f"  Report ID: {report_id}")
        else:
            print(f"  ✗ Failed: {response.status_code} - {response.text[:100]}")
    
    print("\nDemo reports imported successfully!")
    print("You can now find them in the Designer > Reports list.")

if __name__ == "__main__":
    main()
