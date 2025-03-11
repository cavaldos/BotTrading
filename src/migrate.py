import os
import time
import shutil
from pathlib import Path
import threading
import queue

def process_file(file_path, dest_folder, status_queue, delay_seconds, batch_size):
    """Process a single file line by line with delay in batches"""
    dest_file = dest_folder / file_path.name
    
    # Check if destination file already exists and remove it
    if dest_file.exists():
        os.remove(dest_file)
        status_queue.put(f"Removed existing file: {dest_file}")
    
    status_queue.put(f"Starting migration of {file_path} to {dest_file}")
    
    # Read all lines from source file
    with open(file_path, 'r') as source_file:
        lines = source_file.readlines()
    
    # Get header if exists (assuming first line is header)
    header = lines[0] if lines else ""
    
    # Write header first
    with open(dest_file, 'w') as dest_file_obj:
        dest_file_obj.write(header)
    
    # Process the rest of the lines (actual candle data)
    data_lines = lines[1:] if len(lines) > 1 else []
    
    # Process candles in batches
    total_candles = len(data_lines)
    status_queue.put(f"{file_path.name}: Total candles to process: {total_candles}")
    
    batch_index = 0
    while batch_index < total_candles:
        end_index = min(batch_index + batch_size, total_candles)
        current_batch = data_lines[batch_index:end_index]
        
        status_queue.put(f"{file_path.name}: Processing batch {batch_index//batch_size + 1}: candles {batch_index+1}-{end_index} of {total_candles}")
        
        # Write the entire batch at once
        with open(dest_file, 'a') as dest_file_obj:
            dest_file_obj.writelines(current_batch)
        
        # Progress info
        progress = end_index / total_candles * 100
        status_queue.put(f"{file_path.name}: Migrated {end_index}/{total_candles} candles ({progress:.1f}%)")
        
        # Delay before next batch (except for the last batch)
        if end_index < total_candles:
            status_queue.put(f"{file_path.name}: Waiting for {delay_seconds} seconds before next batch...")
            time.sleep(delay_seconds)
        
        # Move to next batch
        batch_index = end_index
    
    status_queue.put(f"Completed migration of {file_path.name}")

def migrate_files_line_by_line(old_folder, new_folder, delay_seconds=5, batch_size=1):
    """
    Migrates files from source folder to destination folder in batches with configurable delay
    between each batch. Processes multiple files in parallel.
    
    Args:
        old_folder (str): Source folder path
        new_folder (str): Destination folder path
        delay_seconds (int): Delay in seconds between writing each batch
        batch_size (int): Number of candles to copy in each batch
    """
    # Define source and destination folders
    source_folder = Path(old_folder)
    dest_folder = Path(new_folder)
    
    # Create destination folder if it doesn't exist
    if not os.path.exists(dest_folder):
        os.makedirs(dest_folder)
        print(f"Created destination folder: {dest_folder}")
    
    # Get all files from source folder
    files = [f for f in list(source_folder.glob('*')) if f.is_file()]
    
    if not files:
        print(f"No files found in {old_folder} folder.")
        return
    
    print(f"Found {len(files)} files in {old_folder} folder.")
    print(f"Configuration: Delay = {delay_seconds} seconds, Batch size = {batch_size} candles")
    
    # Create a queue for status messages
    status_queue = queue.Queue()
    
    # Create and start a thread for each file
    threads = []
    for file_path in files:
        thread = threading.Thread(
            target=process_file,
            args=(file_path, dest_folder, status_queue, delay_seconds, batch_size)
        )
        threads.append(thread)
        thread.start()
        print(f"Started migration thread for {file_path.name}")
    
    # Process status messages from queue while threads are running
    while any(thread.is_alive() for thread in threads):
        try:
            message = status_queue.get(timeout=0.1)
            print(message)
            status_queue.task_done()
        except queue.Empty:
            pass
    
    # Process any remaining messages
    while not status_queue.empty():
        print(status_queue.get())
        status_queue.task_done()
    
    # Wait for all threads to complete
    for thread in threads:
        thread.join()
        
    print("All migrations completed.")

if __name__ == "__main__":
    # Define the source and destination folders
    old_folder = "src/data"
    new_folder = "src/data2"
    
    # Configuration parameters
    delay_seconds = 0.2   # Delay between batches in seconds
    batch_size = 5  # Number of candles to copy in each batch
    
    # Start the migration process
    migrate_files_line_by_line(old_folder, new_folder, delay_seconds, batch_size)
    
    # Display completion message
    print(f"Migration completed. Source folder: {old_folder}, Destination folder: {new_folder}")