from analysis import analysis_func
from get_data import getdata


import threading

if __name__ == '__main__':
    t1 = threading.Thread(target=analysis_func)
    t2 = threading.Thread(target=getdata)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    print("Done!")
    
    