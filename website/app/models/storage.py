#
#
#  models a generic storage infra
#  implemented using mongodb
#
#  Can change the backend by just changing this file
#  the rest of the app should not care
# 
#  can also put a cache
#

#import pymongo

#conn = pymongo.Connection("localhost", 27017)

def getDb(coll, db):
    db = conn[db]
    users = db[coll]
    return users

def getItemWithFilter(coll, filter, db='test'):
    return (getDb(coll, db).find_one(filter))

def getItem(coll, path, db='test'):
    return (getDb(coll, db).find_one({"path":path}))

def getCursor(coll, db="test"):
    return (getDb(coll, db).find())
    
def updateItem(coll, path, data, db='test'):
    data["path"] = path
    #print data
    getDb(coll, db).update({"path":path},{"$set": data}, upsert=False)

def createItem(coll, path, data, db='test'):
    data["path"] = path
    getDb(coll, db).save(data)

def createOrUpdate(coll, path, data, db='test'):
    if (getItem(coll,path,db) != None):
        updateItem(coll, path, data, db)
    else:
        createItem(coll, path, data, db)

def deleteItem(coll, path, db='test'):
    getDb(coll, db).remove({"path":path})


if __name__ == "__main__":
    print("hello")
    data = {}
    data["name"] = "test"
    data["email"] = "email"
    #createItem("email", data)
    d = getItem("users", "email")
    print(d)
    print(d['name'], d['email'])
    deleteItem("users", "email")
    d = getItem("users", "email")
    print(d)
