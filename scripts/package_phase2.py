"""
package_phase2.py - Automated packaging script for HackBridge Phase 2 Submission.

Generates:
1. Main_Code.zip - Self-contained, runnable source code with frontend, backend, evaluation, tests, and data.
2. Team_Submission/ folder containing Main_Code.zip and Screenshots/.
3. Team_Submission.zip - Final archive adhering strictly to HackBridge Phase 2 folder structure.
"""

import os
import shutil
import zipfile
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {
    ".venv",
    ".git",
    "__pycache__",
    ".pytest_cache",
    "original_downloads",
    "Team_Submission",
    "build",
    "dist",
}

EXCLUDE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".db-wal",
    ".db-shm",
    ".zip",
    ".parquet",
    ".tar",
    ".gz",
}

EXCLUDE_FILES = {
    "feature_1.png",
    "feature_2.png",
    "feature_3.png",
    "homepage.png",
    "fullpage.png",
    "fullpage_complete.png",
    "test.zip",
}

def checkpoint_database():
    db_path = REPO_ROOT / "data" / "capacity_intelligence.db"
    if db_path.exists():
        print(f"Checkpointing SQLite DB: {db_path}...")
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.close()
            print("Checkpoint successful.")
        except Exception as e:
            print(f"Warning: Could not checkpoint DB: {e}")

def should_include_in_main_code(rel_path: Path) -> bool:
    parts = rel_path.parts
    # Exclude unwanted directories
    for part in parts:
        if part in EXCLUDE_DIRS:
            return False
    # Exclude Screenshots in Main_Code.zip (Screenshots lives in Team_Submission)
    if parts and parts[0] == "Screenshots":
        return False
    if parts and parts[0] == "scripts":
        # Keep scripts if helpful
        pass
    if rel_path.suffix.lower() in EXCLUDE_EXTENSIONS:
        return False
    if rel_path.name in EXCLUDE_FILES:
        return False
    return True

def create_main_code_zip(output_path: Path):
    print(f"\n[1/3] Creating {output_path.name}...")
    file_count = 0
    total_bytes = 0
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
        for root, dirs, files in os.walk(REPO_ROOT):
            # Prune exclude dirs in place
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and d != "Screenshots" and d != "Team_Submission"]
            for file in sorted(files):
                full_path = Path(root) / file
                rel_path = full_path.relative_to(REPO_ROOT)
                if should_include_in_main_code(rel_path):
                    zipf.write(full_path, arcname=str(rel_path).replace("\\", "/"))
                    file_count += 1
                    total_bytes += full_path.stat().st_size
    
    zip_size = output_path.stat().st_size
    print(f"[OK] {output_path.name} created: {file_count} files, uncompressed {total_bytes / (1024*1024):.2f} MB -> compressed {zip_size / (1024*1024):.2f} MB")

def build_team_submission(staging_dir: Path, main_code_zip: Path, final_zip_path: Path):
    print(f"\n[2/3] Preparing staging directory {staging_dir.name}...")
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy Main_Code.zip into staging_dir
    dest_main_code = staging_dir / "Main_Code.zip"
    shutil.copy2(main_code_zip, dest_main_code)

    # 2. Copy Screenshots directory
    dest_screenshots = staging_dir / "Screenshots"
    dest_screenshots.mkdir(parents=True, exist_ok=True)
    src_screenshots = REPO_ROOT / "Screenshots"

    for screen_file in ["homepage.png", "feature_1.png", "feature_2.png", "feature_3.png"]:
        src = src_screenshots / screen_file
        if src.exists():
            shutil.copy2(src, dest_screenshots / screen_file)
            print(f"  + Added screenshot: {screen_file} ({src.stat().st_size / 1024:.1f} KB)")
        else:
            print(f"  ! Warning: {screen_file} not found in {src_screenshots}")

    # 3. Zip into Team_Submission.zip
    print(f"\n[3/3] Creating final archive {final_zip_path.name}...")
    with zipfile.ZipFile(final_zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
        # Add Main_Code.zip at root of Team_Submission.zip
        zipf.write(dest_main_code, arcname="Main_Code.zip")
        # Add Screenshots folder
        for s_file in sorted(dest_screenshots.iterdir()):
            zipf.write(s_file, arcname=f"Screenshots/{s_file.name}")

    print(f"[OK] {final_zip_path.name} created successfully: {final_zip_path.stat().st_size / (1024*1024):.2f} MB")

def verify_archive(archive_path: Path):
    print(f"\n[VERIFICATION] Inspecting contents of {archive_path.name}:")
    with zipfile.ZipFile(archive_path, "r") as zipf:
        infolist = zipf.infolist()
        for info in infolist:
            print(f"  - {info.filename:30s} ({info.file_size / 1024:8.1f} KB -> {info.compress_size / 1024:8.1f} KB)")
        print(f"Total entries: {len(infolist)}")

if __name__ == "__main__":
    checkpoint_database()
    
    main_code_zip = REPO_ROOT / "Main_Code.zip"
    staging_folder = REPO_ROOT / "Team_Submission"
    final_submission_zip = REPO_ROOT / "Team_Submission.zip"
    
    create_main_code_zip(main_code_zip)
    build_team_submission(staging_folder, main_code_zip, final_submission_zip)
    verify_archive(final_submission_zip)
    print("\nPhase 2 packaging complete!")
