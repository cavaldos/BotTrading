import time
import datetime

def run_at_interval(callback=None):

    print("Task will run every minute.")
    while True:
        # Get current time
        now = datetime.datetime.now()
        seconds_until_next_minute = 60 - now.second - now.microsecond/1000000
        if seconds_until_next_minute == 60:
            seconds_until_next_minute = 0
        
        # Sleep until the next minute mark
        time.sleep(seconds_until_next_minute)
        
        # Execute the task
        if callback:
            callback()
        else:
            print("No task defined.")

if __name__ == "__main__":
    run_at_interval()