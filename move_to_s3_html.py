
import os
import sys
import time
import cloud.storage.storage

html_bucket = "aspiring-html-files"
pdf_bucket = os.getenv("PDF_S3_BUCKET", "aspiring-pdf-files")
pdf_path = "/home/ubuntu/tmp/htmltopdf"

def sorted_ls(path):
    mtime = lambda f: os.stat(os.path.join(path, f)).st_mtime
    lis =  list(sorted(os.listdir(path), key=mtime, reverse=True))
    return [f for f in lis if (f[-4:] == ".pdf") or (f[-5:] == ".html")]


def move_to_s3(fname, fnameid, bucket):
    cloud.storage.storage.putItem(fnameid, open(fname).read(), bucket)

def move_files_to_s3():
    files = sorted_ls(pdf_path)
    count = 0
    bucket = None
    for f in files:
        fname = pdf_path+"/"+f
        if fname[-5:] == ".html":
            fnameid = fname[:-5].split("/")[-1]+".html"
            bucket = html_bucket
        elif fname[-4:] == ".pdf":
            fnameid = fname[:-4].split("/")[-1]
            bucket = pdf_bucket
            count = count+1
            continue
        else:
            count = count+1
            continue
        print(fname, fnameid, count,len(files), os.path.getsize(fname), bucket)
        if not cloud.storage.storage.existsItem(fnameid, html_bucket):
            print("moving")
            move_to_s3(fname, fnameid, bucket)
            time.sleep(1)
            count = count + 1
        else:
            print("exists, breaking")
            continue

def usage():
    print("usage: move_to_s3 filename")
    exit()

if __name__ == "__main__":
    #if len(sys.argv) != 2:
    #    usage()
    #move_to_s3(sys.argv[1])
    #print sorted_ls(pdf_path)[:10]
    move_files_to_s3()
    
    
