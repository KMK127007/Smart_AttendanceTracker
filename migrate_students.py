"""
Migration Script: Upload Students from Excel to Supabase
Run this ONCE after setting up Supabase database
"""

import pandas as pd
from supabase import create_client, Client
import os

# =============================================================================
# CONFIGURATION - UPDATE THESE WITH YOUR SUPABASE CREDENTIALS
# =============================================================================

SUPABASE_URL = "https://fjtsnqxbvltfcaujqkpi.supabase.co"  # e.g., https://xxxxx.supabase.co
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZqdHNucXhidmx0ZmNhdWpxa3BpIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3Mjk2NjI3OSwiZXhwIjoyMDg4NTQyMjc5fQ.b7HxI3Twg43cVxHWSaZT6-NACGRSXufigsxDA_mHO1Q"  # Use service_role key for bulk insert

EXCEL_FILE = "Students_List_For_Sreenidhi_attenance.xlsx"

# =============================================================================

def migrate_students():
    """Upload all students from Excel to Supabase"""
    
    print("🚀 Starting migration...")
    
    # Connect to Supabase
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Connected to Supabase")
    
    # Read Excel file
    df = pd.read_excel(EXCEL_FILE)
    print(f"📂 Loaded {len(df)} students from Excel")
    
    # Normalize column names to match database
    df = df.rename(columns={
        'S.No.': 'sno',
        'Name': 'name',
        'Roll No': 'rollnumber',
        'Course': 'course',
        'Mobile': 'mobile',
        'Email ID': 'email',
        'Gender': 'gender',
        'Current Term Score': 'current_term_score',
        'Xth percentage': 'xth_percentage',
        'XIIth percentage': 'xiith_percentage',
        'Backlogs': 'backlogs'
    })
    
    # Clean roll numbers (lowercase, strip spaces)
    df['rollnumber'] = df['rollnumber'].astype(str).str.strip().str.lower()
    
    # Convert to list of dicts for Supabase
    students_data = df[[
        'rollnumber', 'name', 'course', 'mobile', 'email', 
        'gender', 'current_term_score', 'xth_percentage', 
        'xiith_percentage', 'backlogs'
    ]].to_dict('records')
    
    # Convert numeric fields properly
    for student in students_data:
        # Mobile as string
        if pd.notna(student['mobile']):
            student['mobile'] = str(int(student['mobile'])) if isinstance(student['mobile'], float) else str(student['mobile'])
        else:
            student['mobile'] = None
            
        # Scores as numbers
        for field in ['current_term_score', 'xth_percentage', 'xiith_percentage']:
            if pd.isna(student[field]):
                student[field] = None
        
        # Clean text fields
        for field in ['name', 'course', 'email', 'gender', 'backlogs']:
            if pd.isna(student[field]):
                student[field] = None
            elif isinstance(student[field], str):
                student[field] = student[field].strip()
    
    print(f"📊 Prepared {len(students_data)} student records")
    
    # Batch insert (Supabase handles up to 1000 rows per call, we'll do 500 at a time)
    batch_size = 500
    total = len(students_data)
    success_count = 0
    error_count = 0
    
    for i in range(0, total, batch_size):
        batch = students_data[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (total + batch_size - 1) // batch_size
        
        print(f"📤 Uploading batch {batch_num}/{total_batches} ({len(batch)} students)...", end=" ")
        
        try:
            response = supabase.table('students').insert(batch).execute()
            success_count += len(batch)
            print(f"✅ Success")
        except Exception as e:
            error_count += len(batch)
            print(f"❌ Error: {str(e)}")
            
            # Try inserting one by one to find the problematic record
            print("   🔍 Trying individual inserts...")
            for j, student in enumerate(batch):
                try:
                    supabase.table('students').insert(student).execute()
                    success_count += 1
                except Exception as e2:
                    error_count += 1
                    print(f"   ❌ Failed: {student['rollnumber']} - {str(e2)}")
    
    print("\n" + "="*60)
    print("✨ MIGRATION COMPLETE")
    print(f"✅ Successfully uploaded: {success_count} students")
    if error_count > 0:
        print(f"❌ Failed: {error_count} students")
    print("="*60)
    
    # Verify
    count_response = supabase.table('students').select('id', count='exact').execute()
    print(f"\n📊 Total students in database: {count_response.count}")

if __name__ == "__main__":
    # Check if credentials are set
    if "YOUR_SUPABASE_URL" in SUPABASE_URL:
        print("❌ ERROR: Please update SUPABASE_URL and SUPABASE_KEY in this script first!")
        print("\nGet them from:")
        print("1. Go to your Supabase project")
        print("2. Click Settings → API")
        print("3. Copy 'Project URL' and 'service_role' key")
        exit(1)
    
    if not os.path.exists(EXCEL_FILE):
        print(f"❌ ERROR: Excel file not found: {EXCEL_FILE}")
        print("Please place the Excel file in the same directory as this script")
        exit(1)
    
    # Run migration
    migrate_students()
