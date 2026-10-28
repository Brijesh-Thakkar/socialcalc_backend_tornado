import os
import boto
from . import msnparse
import json
from . import xbrlmap

from boto.s3.connection import S3Connection

from boto.s3.key import Key

conn = S3Connection(os.environ.get('AWS_ACCESS_KEY_ID'), os.environ.get('AWS_SECRET_ACCESS_KEY'))

sdb = boto.connect_sdb(os.environ.get('AWS_ACCESS_KEY_ID'), os.environ.get('AWS_SECRET_ACCESS_KEY'))
tickerdomain = sdb.get_domain('tickerdata')

#bucket = conn.get_bucket('aspiring-tickerdata')

        
def getYears(years):
    yearlis = []
    for year in range(1,len(years)):
        dates = years[year].split("/")
        yearlis.append(dates[2])
    return yearlis

def convertToPerYear(rows):
    years = rows[0]
    yearlis = getYears(years)
    stmt = {}
    for year in yearlis:
        stmt[year] = {}
    for r in range(0,len(rows)):
        for c in range(0,len(yearlis)):
            #print c,r,yearlis[c],rows[r][0],rows[r][c]
            stmt[yearlis[c]][rows[r][0]] = rows[r][c+1]
    return stmt

def getStmtKey(ticker, stmttype):
    key = ticker+"-stmt-"+stmttype
    return key

def getNormalizedTable(data, type):
    retrows = []
    canonlist = xbrlmap.getNormalizedList(type)
    years = list(data.keys())
    #print years
    years.sort()
    years.reverse()

    stmt = data[years[0]]


    for item in canonlist:
        row = []
        row.append(item)

        #print stmt
        if item in stmt:
            for year in years:
                #print data[year][item]
                row.append(data[year][item])
        else:
            for year in years:
                row.append("0")
        retrows.append(row)
    return retrows
    
    
def getStmtFromSimpleDb(ticker,stmttype):
    key = getStmtKey(ticker, stmttype)
    s3key = bucket.get_key(key)
    if s3key == None:
        return None
    else:
        stringval = s3key.get_contents_as_string()
        data = json.loads(stringval)
        #print data
        rows = getNormalizedTable(data, stmttype)
        return rows

def getFromSimpleDb(ticker):
    rows = getStmtFromSimpleDb(ticker,"Balance")
    if rows == None:
        return None
    str1,startrow = msnparse.getSheetTable(rows, 2)
    rows = getStmtFromSimpleDb(ticker,"Income")
    if rows == None:
        return None    
    str2,startrow = msnparse.getSheetTable(rows, 2)
    rows = getStmtFromSimpleDb(ticker,"CashFlow")
    if rows == None:
        return None    
    str3,startrow = msnparse.getSheetTable(rows, 2)
    return msnparse.savetemplate1+"\\n"+str1+'"'+msnparse.savetemplate2+"\\n"+str2+'"'+msnparse.savetemplate3+"\\n"+str3+'"'+msnparse.savetemplate4    
            
def loadStmtToSimpleDb(ticker, stmttype):
    #get the statement
    #convert it to json
    #for each year of the data, load it into simpleDb for that year
    try:
        data = msnparse.get_ticker_data(ticker, stmttype, "Ann")
    except:
        print(ticker+"-FAILED")
        return
    if len(data) < 10:
        print(ticker+"-FAILED")
        return
    # convert data to per year data
    stmt = convertToPerYear(data)
    item = tickerdomain.get_item(ticker)
    if item == None:
        item = tickerdomain.new_item(ticker)
    if item != 'None':
        key = getStmtKey(ticker,stmttype)
        s3key = bucket.get_key(key)
        stringval = json.dumps(stmt)
        print(len(stringval))
        if s3key == None:
            k = Key(bucket)
            k.key = key
            k.set_contents_from_string(stringval)
        else:
            s3key.set_contents_from_string(stringval)
        item["years"] = json.dumps(list(stmt.keys()))
        item.save()


def getAllS3Keys():
    keys = bucket.get_all_keys()
    print(keys)
    #for key in keys:
    #    print key.get_contents_as_string()

def getAllSDBItems():
    for item in tickerdomain:
        print(item.name)
    
def loadTickerToSimpleDb(ticker):
    loadStmtToSimpleDb(ticker, "Income")
    loadStmtToSimpleDb(ticker, "Balance")
    loadStmtToSimpleDb(ticker, "CashFlow")       

def loadTickersFromFile(fname):
    data = open(fname).read().split("\n")
    count = 1
    for tick in data:
        print(count,tick,"----")
        if count <= 309:
            count = count + 1
            continue
        if len(tick) > 0:
            try:
                loadTickerToSimpleDb(tick)
            except:
                print("exception "+tick)
        count = count + 1


if __name__ == "__main__":
    #import sys
    #ticker = sys.argv[1]
    #loadTickerToSimpleDb("msft")
    #getAllS3Keys()
    #getAllSDBItems()
    loadTickersFromFile("util/sp500.txt")
    #print getFromSimpleDb("msft")    
