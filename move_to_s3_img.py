
import os
import sys
import time
import cloud.storage.storage

img_bucket = "aspiring-img-files"
img_path = "/home/ubuntu/tmp/iconimg"

def sorted_ls(path):
    mtime = lambda f: os.stat(os.path.join(path, f)).st_mtime
    lis =  list(sorted(os.listdir(path), key=mtime, reverse=True))
    return [f for f in lis if (f[-4:] == ".png") or (f[-5:] == ".jpeg")]


def move_to_s3(fname, fnameid, bucket):
    cloud.storage.storage.putItem(fnameid, open(fname).read(), bucket)

def move_files_to_s3():
    files = sorted_ls(img_path)
    count = 0
    bucket = None
    for f in files:
        fname = img_path+"/"+f
        if fname[-4:] == ".png":
            fnameid = fname[:-4].split("/")[-1]
            bucket = img_bucket
        elif fname[-5:] == ".jpeg":
            fnameid = fname[:-5].split("/")[-1]
            bucket = img_bucket
        else:
            count = count+1
            continue
        print(fname, fnameid, count,len(files), os.path.getsize(fname), bucket)
        if not cloud.storage.storage.existsItem(fnameid, img_bucket):
            print("moving")
            move_to_s3(fname, fnameid, bucket)
            time.sleep(1)
            count = count + 1
        else:
            print("exists, breaking")
            # break
            continue

def usage():
    print("usage: move_to_s3_img imgfiles")
    exit()

if __name__ == "__main__":
    #if len(sys.argv) != 2:
    #    usage()
    #move_to_s3(sys.argv[1])
    #print sorted_ls(img_path)[:10]
    move_files_to_s3()
    
    
