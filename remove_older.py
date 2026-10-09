
import os
import sys
import time
import cloud.storage.storage
import shutil

html_bucket = "aspiring-html-files"
pdf_bucket = os.getenv("PDF_S3_BUCKET", "aspiring-pdf-files")
pdf_path = "/home/ubuntu/tmp/htmltopdf"
html_path = "/home/ubuntu/tmp/htmlonly"
older_pdf_path = "/home/ubuntu/tmp/olderpdf"
cache_days = 30


def sorted_ls(path):
    mtime = lambda f: os.stat(os.path.join(path, f)).st_mtime
    lis =  list(sorted(os.listdir(path), key=mtime, reverse=False))
    return [f for f in lis if (f[-4:] == ".pdf") or (f[-5:] == ".html")]

def remove_older_pdf_files():
    now = time.time()

    for f in sorted_ls(pdf_path):
        fname = pdf_path+"/"+f
        if os.stat(fname).st_mtime < now - cache_days * 86400:
            #remove the file if it exists in 
            fnameid = fname[:-4].split("/")[-1]
            bucket = pdf_bucket
            if fname[-4:] == ".pdf":
                if cloud.storage.storage.existsItem(fnameid, pdf_bucket):
                    #file is ripe for deletion
                    print("moving ",f,"created ",time.ctime(os.path.getctime(fname)))
                    #os.remove(fname)
                    shutil.move(fname,older_pdf_path+"/"+f)
                else:
                    print("found old file not in S3 !!!")
                    break
            else:
                print("skipping", f)
        else:
            print("less than 30, breaking")
            break

def usage():
    print("usage: remove_older")
    exit()

if __name__ == "__main__":
    #if len(sys.argv) != 2:
    #    usage()
    #move_to_s3(sys.argv[1])
    #print sorted_ls(pdf_path)[:10]
    remove_older_pdf_files()
    
    
