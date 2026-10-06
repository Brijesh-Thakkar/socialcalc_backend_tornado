"""
Cloud Storage Infrastructure

using amazon S3
"""

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
import json
import os
import logging


aws_access_key = os.environ.get('AWS_ACCESS_KEY_ID')
aws_secret_key = os.environ.get('AWS_SECRET_ACCESS_KEY')
# When set, all S3 calls go to this endpoint (e.g. http://minio:9000 for local dev/CI).
# Leave unset to use real AWS S3.
aws_endpoint_url = os.environ.get('AWS_ENDPOINT_URL') or None
# Used only when region discovery fails (no credentials, bucket unreachable, etc.).
_default_region = os.environ.get('AWS_DEFAULT_REGION', 'us-east-1')

# region → boto3.resource  (populated lazily, one entry per region encountered)
_resources = {}

# bucket_name → region string  (populated on first getBucket() call for each bucket)
_bucket_regions = {}

# Single lightweight client used only for region discovery.  Uses the global
# path-style endpoint so S3 returns 301 + x-amz-bucket-region for any bucket
# that is not in us-east-1, without requiring s3:GetBucketLocation permission.
# When using a custom endpoint (MinIO etc.) region discovery is skipped entirely.
if aws_endpoint_url:
    _probe_client = boto3.client(
        's3',
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        endpoint_url=aws_endpoint_url,
        region_name='us-east-1',
        config=Config(s3={'addressing_style': 'path'}),
    )
else:
    _probe_client = boto3.client(
        's3',
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name='us-east-1',
        endpoint_url='https://s3.amazonaws.com',
        config=Config(s3={'addressing_style': 'path'}),
    )
AspiringStorageBucket = "mc2-app-storage-useast1"

print("Starting cloud import")

#
# The following are the base ITEM key, value APIs
#    Using this key,value storage is built a
#    user storage metaphor
#


# store a user item
# returns True/False
def putItem(path, filedata, bucket_name=None):
    try:
        if bucket_name is None:
            bucket_name = AspiringStorageBucket
        bucket = getBucket(bucket_name)
        body = filedata.encode('utf-8') if isinstance(filedata, str) else filedata
        bucket.Object(path).put(Body=body)
        return True
    except Exception as e:
        logging.error("putItem failed for path=%s: %s" % (path, str(e)))
        return False

# get a user item
# returns data/None
def getItem(path, bucket_name=None):
    try:
        if bucket_name==None:
            bucket_name = AspiringStorageBucket
        bucket = getBucket(bucket_name)
        data = bucket.Object(path).get()['Body'].read()
        return data
    except:
        return None

# does item exist
# returns boolean
def existsItem(path, bucket_name=None):
    try:
        if bucket_name==None:
            bucket_name = AspiringStorageBucket
        bucket = getBucket(bucket_name)
        bucket.Object(path).load()
        return True
    except:
        return False


# delete a user item
# returns True/False
def deleteItem(path, bucket_name=None):
    if bucket_name==None:
        bucket_name = AspiringStorageBucket
    bucket = getBucket(bucket_name)
    bucket.Object(path).delete()
    return True

#  The following are helpers to implement the API

def createBucket(bucketname):
    conn = getConnection()
    bucket = conn.create_bucket(Bucket=bucketname)
    return bucket

def _discover_region(bucketname):
    """Return the AWS region that owns bucketname.

    For custom endpoints (MinIO, LocalStack) region discovery is meaningless —
    return us-east-1 immediately.

    For real AWS: call HeadBucket against the global path-style endpoint.
    - Non-us-east-1 bucket  → S3 returns 301 with x-amz-bucket-region header.
    - us-east-1 bucket      → S3 returns 200 or 403; header is still present.
    - No credentials / network error → fall back to _default_region.
    """
    if aws_endpoint_url:
        return 'us-east-1'
    try:
        r = _probe_client.head_bucket(Bucket=bucketname)
        # 200: read BucketRegion from the response dict (always present on 200).
        return r.get('BucketRegion', 'us-east-1')
    except ClientError as e:
        region = e.response.get('ResponseMetadata', {}) \
                           .get('HTTPHeaders', {}) \
                           .get('x-amz-bucket-region')
        if region:
            return region
        logging.warning(
            "region discovery failed for bucket=%s (%s); using %s",
            bucketname, e, _default_region,
        )
        return _default_region
    except Exception as e:
        logging.warning(
            "region discovery failed for bucket=%s (%s); using %s",
            bucketname, e, _default_region,
        )
        return _default_region


def _resource_for_region(region):
    """Return a cached boto3.resource for the given region."""
    if region not in _resources:
        kwargs = dict(
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
            region_name=region,
        )
        if aws_endpoint_url:
            kwargs['endpoint_url'] = aws_endpoint_url
        _resources[region] = boto3.resource('s3', **kwargs)
    return _resources[region]


def getBucket(bucketname):
    """Return a boto3 Bucket wired to the correct regional resource.

    Region is discovered on the first call per bucket name and cached
    for the lifetime of the process.  Subsequent calls are pure dict lookups.
    """
    if bucketname not in _bucket_regions:
        region = _discover_region(bucketname)
        _bucket_regions[bucketname] = region
        logging.info("bucket %s resolved to region %s", bucketname, region)
    return _resource_for_region(_bucket_regions[bucketname]).Bucket(bucketname)


def getConnection():
    # Kept for backward compatibility (used by createBucket).
    # Returns a resource for the default region.
    return _resource_for_region(_default_region)

#
#
#  The following are user file abstraction
#    built using a key-value storage
#
#  The abstraction is simple
#  The path to the file is the key
#  The metadata on the key indicates if it is a file or directory
#  If it is a directory, then, it contains the list of files as the value
#  which gets updated when files get added or deleted 
#
#  Note that the user is embedded into the filesystem path
#
#  path itself is a stringified json list
#
#

# path manipulation apis

# first define dir, and file classes

class File:
    def __init__(self,name,data):
        self.fname = name
        self.data = data

class Directory:
    def __init__(self,name,filelist):
        self.fname = name
        self.files = [File(i,"") for i in filelist]


def pathToString(path):
    return json.dumps(path)

# path is a list
# returns True/False
def createDir(path):
    # check if dir exists, if so handle gracefully
    spath = pathToString(path)
    data = getItem(spath)
    if (data != None):
        try:
            dirdata = json.loads(data)
            if isinstance(dirdata, dict) and dirdata.get("type") == "dir":
                print("dir exists - graceful return")
                return True
        except:
            pass
        print("dir exists but is not a valid directory format")
        return False
    # create the dir file
    dirdata = {}
    dirdata["data"] = json.dumps([])
    dirdata["path"] = path
    dirdata["type"] = "dir"
    if not (putItem(spath, json.dumps(dirdata))):
        print("putitem failed")
        return False
    #print "createDir passed"    
    return True
    
    
def deleteDir(path):
    # not implemented yet
    pass


# path is list, return python file object
def getFileRaw(path):
    spath = pathToString(path)
    data = getItem(spath)
    if (data == None):
        return None
    filedata = json.loads(data)
    return filedata

# path is list, returns directory object or file object as the case
# may be
def getFile(path):
    data = getFileRaw(path)
    print("getfile",data)
    if data == None:
        return None
    if data["type"] == "dir":
        fileslist = json.loads(data["data"])
        fname = path[len(path)-1]
        fileobj = Directory(fname, fileslist)
        return fileobj
    elif data["type"] == "file":
        fname = path[len(path)-1]
        fileobj = File(fname, data["data"])
        return fileobj
    else:
        return None

##
## path is list, data is a string
##
def createFile(path, data):
    # make sure parent dir exists
    if len(path) <= 1:
        print("parent path failed")
        return False
    ppath = path[:-1]    
    parentdata = getFileRaw(ppath)
    if parentdata == None:
        print("parent data failed")        
        return False
    # check if file exists
    spath = pathToString(path)
    if getItem(spath) != None:
        print("file exists failed")                
        return False
    # update the file
    filedata = {}
    filedata["data"] = data
    filedata["path"] = path
    filedata["type"] = "file"
    if (not putItem(spath, json.dumps(filedata))):
        print("putfile failed")                
        return False
    # the update the directory
    fname = path[len(path)-1]
    fileslist = json.loads(parentdata["data"])
    fileslist.append(fname)
    parentdata["data"] = json.dumps(fileslist)    
    if (not putItem(pathToString(ppath), json.dumps(parentdata))):
        # this is unexpected, unwind !
        print("putdir failed")                        
        deleteFile(path)
        return False
    return True
    
##
## path is list, data is a string
##    
def updateFile(path, data):
    # file must exist
    filedata = getFileRaw(path)
    if (filedata == None):
        return False
    filedata["data"] = data
    if not putItem(pathToString(path), json.dumps(filedata)):
        return False
    return True

##
## path is list
##
def deleteFile(path):
    filedata = getFileRaw(path)
    if filedata == None or filedata["type"] != "file":
        print("file does not exist")
        return False
    #
    # update the parent directory first
    #
    ppath = path[:-1]    
    parentdata = getFileRaw(ppath)
    if parentdata == None:
        print("parent data failed")        
        return False
    fileslist = json.loads(parentdata["data"])
    newlist = []
    fname = path[len(path)-1]
    for i in fileslist:
        if fname == i:
            pass
        else:
            newlist.append(i)
    parentdata["data"] = json.dumps(newlist)
    if (not putItem(pathToString(ppath), json.dumps(parentdata))):
        # this is unexpected, unwind !
        print("putdir failed")                        
        return False
    # then delete the file
    if not deleteItem(pathToString(path)):
        print("delete file failed")
        return False
    return True

##
## path is list, query is a string
##
def searchFiles(path, query):
    directory = getFile(path)
    if directory is None or not isinstance(directory, Directory):
        return []
    query_lower = query.strip().lower()
    matches = []
    for f in directory.files:
        if query_lower in f.fname.lower():
            matches.append(f)
    return matches


#### The following are unit tests

def unitTestItems():
    putItem("foobar1","test1")
    print(getItem("foobar1"))
    putItem("foobar2","test2")    
    print(getItem("foobar2"))
    deleteItem("foobar1")
    deleteItem("foobar2")
    print(getItem("foobar1"))    
    print(getItem("foobar2"))    

def unitTestItemsInBucket():
    bkt_name = os.getenv("PDF_S3_BUCKET", "aspiring-pdf-files")
    putItem("foobar1","test1", bkt_name)
    print(existsItem("foobar1", bkt_name))
    print(getItem("foobar1", bkt_name))
    putItem("foobar2","test2", bkt_name)    
    print(existsItem("foobar2", bkt_name))
    print(getItem("foobar2", bkt_name))
    deleteItem("foobar1", bkt_name)
    deleteItem("foobar2", bkt_name)
    print(existsItem("foobar1", bkt_name))
    print(existsItem("foobar1", bkt_name))
    print(getItem("foobar1", bkt_name))    
    print(getItem("foobar2", bkt_name))    


def unitTestFiles():
    path = ["home","demo"]
    print("--create dir--")
    createDir(path)
    print(getFileRaw(path))
    fpath = path[:]
    fpath.append("fname")
    print("--del file--")    
    deleteFile(fpath)
    print("--create file--")        
    createFile(fpath, "FileData Test1")
    print(getFileRaw(fpath))
    print(getFileRaw(path))
    print(str(getFile(fpath)))
    print("--update file--")        
    updateFile(fpath, "FileData Test2")
    print(getFileRaw(fpath))
    print(getFileRaw(path))
    print(str(getFile(fpath)))
    print("--create second file--")
    fpath2 = path[:]
    fpath2.append("fname2")    
    createFile(fpath2, "FileData2 Test1")
    print(getFileRaw(fpath2))
    print(getFileRaw(path))
    print(str(getFile(fpath2)))
    print("--update second file--")        
    updateFile(fpath2, "FileData2 Test2")
    print(getFileRaw(fpath2))
    print(getFileRaw(path))
    print(str(getFile(fpath2)))
    print("--del file--")    
    deleteFile(fpath)
    print(getFileRaw(path))
    print(str(getFile(fpath)))
    deleteFile(fpath2)
    print(getFileRaw(path))
    print(str(getFile(fpath)))
    
print("Cloud imported")

if __name__ == "__main__":
    # unit tests here
    #unitTestItems()
    #unitTestFiles()
    unitTestItemsInBucket()
