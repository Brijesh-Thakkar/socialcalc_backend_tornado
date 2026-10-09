
#
# compressed cache, timestamp embedded in file
# 
#

import urllib.request, urllib.error, urllib.parse
import time
import zlib
import os

import hashlib
#import web

lastamznread = time.time()
cachedir = "util/cache/"
#cachedir = "cache"
#
# cached, delayed url read
#

def getFileFromUrl(url):
    m = hashlib.md5()
    m.update(url)
    dig = cachedir+m.hexdigest()
    #print dig
    return dig

def findInCache(url):
    fname = getFileFromUrl(url)
    if fname != "" and os.path.exists(fname):
        data = open(fname,"rb").read()
        return zlib.decompress(data)
    else:
        return ""
    
def putInCache(url,data):
    fname = getFileFromUrl(url)
    open(fname,"wb").write(zlib.compress(data))

def delayedUrlRead(url, sleeptime=2.0):
    global lastamznread
    current = time.time()
    if (current - lastamznread) < sleeptime:
        time.sleep(sleeptime)
    lastamznread = current
    #print "reading-%s"%url
    try:
        data =  urllib.request.urlopen(url).read()
    except:
        time.sleep(sleeptime)
        lastamznread = time.time()
        data =  urllib.request.urlopen(url).read()        
    return data

def getUrl(url, allowcache = True):
    if allowcache:
        data = findInCache(url)
        if data != "":
            return data
    data = delayedUrlRead(url)
    if allowcache:
        putInCache(url,data)
    return data


def demo():
    industrybase = "http://biz.yahoo.com/p/1conameu.html"    
    data = getUrl(industrybase)
    data1 = getUrl(industrybase)
    if data == data1:
        print("test ok")
    else:
        print("test fail %d %d"%(len(data),len(data1)))
    
if __name__ == "__main__":
    demo()
