import os
import time
import pandas as pd
import threading
import csv
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from util.notify import entry_point

class ResultFolderHandler(FileSystemEventHandler):
    def __init__(self, result_folder):
        self.result_folder = os.path.abspath(result_folder)
        self.last_processed = {}
        self.processing_cooldown = 3
        self.signals_log_file = os.path.join(os.path.dirname(self.result_folder), "signals_log.csv")
        self.initialize_signals_log()
        print(f"Watching result directory: {self.result_folder}")

    def initialize_signals_log(self):
        # Create signals log file if it doesn't exist
        if not os.path.exists(self.signals_log_file):
            with open(self.signals_log_file, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['Timestamp', 'File', 'Signal_Time', 'Type', 'Entry', 'Stoploss', 'Take_Profit'])
            print(f"Created signals log file: {self.signals_log_file}")

    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".csv"):
            self.handle_file(event.src_path)

    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".csv"):
            self.handle_file(event.src_path)

    def handle_file(self, file_path):
        file_path = os.path.abspath(file_path)
        file_name = os.path.basename(file_path)
        current_time = time.time()

        if file_name in self.last_processed and current_time - self.last_processed[file_name] < self.processing_cooldown:
            return

        self.last_processed[file_name] = current_time
        self.process_result_file(file_path)

    def process_result_file(self, file_path):
        try:
            df = pd.read_csv(file_path)
            df['Time'] = pd.to_datetime(df['Time'])
            df = df.sort_values(by='Time').reset_index(drop=True)

            for i in range(1, len(df)):
                current_trade = df.iloc[i]
                previous_trade = df.iloc[i - 1]

                if current_trade['Status'] == 'OnGoing' and previous_trade['Status'] == 'win':
                    self.print_trade_signal(current_trade, file_path)
                    print(f"📊 Signal found in: {os.path.basename(file_path)}")

        except Exception as e:
            print(f"Error processing file {file_path}: {str(e)}")

    def print_trade_signal(self, trade, file_path):
        border = "=" * 50
        signal_emoji = "🟢" if trade['Type'] == "Long" else "🔴"
        direction = "↗️" if trade['Type'] == "Long" else "↘️"
        
        # Convert pandas Timestamp to string format before passing to entry_point
        time_str = trade['Time'].strftime('%Y-%m-%d %H:%M:%S')
        
        # # Run entry_point in a separate thread to prevent blocking
        # notification_thread = threading.Thread(
        #     target=entry_point,
        #     args=(trade['Type'], trade['Entry'], trade['Stoploss'], trade['Take_Profit'], time_str, os.path.basename(file_path))
        # )
        # notification_thread.daemon = True  # Allow program to exit even if thread is running
        # notification_thread.start()
        
        # Save signal to CSV log file
        self.save_signal_to_csv(trade, file_path)
        
        print(f"\n{border}")
        print(f"{signal_emoji} SIGNAL ALERT - After Winning Trade {signal_emoji}")
        print(f"File: {os.path.basename(file_path)}")
        print(f"Time: {trade['Time']}")
        print(f"Type: {trade['Type']} {direction}")
        print(f"Entry: {trade['Entry']}")
        print(f"Stop Loss: {trade['Stoploss']}")
        print(f"Take Profit: {trade['Take_Profit']}")
        print(f"{border}\n")

    def save_signal_to_csv(self, trade, file_path):
        try:
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            signal_time = trade['Time'].strftime('%Y-%m-%d %H:%M:%S')
            
            with open(self.signals_log_file, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    current_time,
                    os.path.basename(file_path),
                    signal_time,
                    trade['Type'],
                    trade['Entry'],
                    trade['Stoploss'],
                    trade['Take_Profit']
                ])
            print(f"Signal saved to log file: {self.signals_log_file}")
        except Exception as e:
            print(f"Error saving signal to log file: {str(e)}")

def start_result_watcher(result_folder):
    result_folder = os.path.abspath(result_folder)

    if not os.path.exists(result_folder):
        print(f"Result folder {result_folder} does not exist.")
        return

    handler = ResultFolderHandler(result_folder)
    observer = Observer()
    observer.schedule(handler, path=result_folder, recursive=False)

    observer.start()
    print(f"Monitoring {result_folder} for signal opportunities...")
    print("Press Ctrl+C to stop monitoring")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


def main_send():
    print("Starting to monitor for signals...")
    result_folder = "src/res"  # Remove 'src/' prefix
    start_result_watcher(result_folder)

if __name__ == "__main__":
    main_send()
